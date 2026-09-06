# HLS IPTV Streaming and 4K Encodings: Learnings

## The Issue
We encountered an issue where standard streaming tools (like `yt-dlp` or Kodi natively) failed to download or play an HLS stream (m3u8 playlist) from an IPTV provider. The error indicated that the segments had an invalid MIME type or were not valid video segments.

Upon closer inspection, downloading the individual segments listed in the `.m3u8` playlist revealed that they were served from a TikTok CDN with `.image` extensions. Checking the file types showed them as `1x1 PNG image data`.

## The Encoding Trick
The IPTV provider was using a clever obfuscation technique to bypass CDN restrictions and host video content for free on TikTok's image CDN:

1. **PNG Wrapper:** The actual MPEG-TS video data for each segment was appended to the end of a valid, tiny 1x1 PNG image file.
2. **Hidden Payload:** Because a standard image viewer or CDN only reads the PNG header up to the `IEND` marker and ignores the rest, the CDN accepts and hosts the file as an image.
3. **MPEG-TS Sync Byte:** Standard video players and `ffmpeg` expect a clean `.ts` file, where packets start with the MPEG-TS sync byte (`0x47` every 188 bytes). Since the file starts with a PNG header instead, standard tools fail to parse it.

## The Solution
To play these streams in Kodi or other standard players, we built a local HTTP proxy server in Python. 

The proxy performs the following tasks dynamically:
1. It fetches the upstream `.m3u8` playlist.
2. It rewrites the segment URLs so that requests route back through the proxy itself (`/seg?url=...`).
3. When the player requests a segment, the proxy fetches the `.image` file from the CDN.
4. It scans the downloaded binary data to find the first valid MPEG-TS sync byte sequence (`0x47` appearing at intervals of 188 bytes).
5. It strips the PNG header entirely and serves only the raw MPEG-TS video data back to the player with the correct `video/MP2T` MIME type.

## 4K Stream Quality
Once we extracted the TS data, `mediainfo` confirmed we were getting genuine high-quality 4K streams. For example, the FOX 4K feed from Sling TV showed:
- **Resolution:** 3840 x 2160 (4K UHD)
- **Codec:** HEVC (H.265) Main10@L5.1
- **HDR:** HDR10 (SMPTE ST 2086) with PQ transfer characteristics.
- **Bitrate:** Variable, peaking up to 12.8 Mb/s.
- **Frame Rate:** 59.94 fps.

## Referer Protection & Disguised Extensions (.pdf / .zst)
Later streams (e.g. Varnish-cached or Cloudflare-protected feeds) introduced additional barriers:

1. **Varnish Referer Whitelist (`403 Invalid Referer`):**
   The upstream Varnish caching tier explicitly rejects requests unless the exact origin/player URL is passed in the `Referer` (and `Origin`) header.
2. **Master & Variant Playlists:**
   The top-level `.m3u8` is often a master playlist pointing to relative sub-playlists (e.g. `tracks-v1a1/mono.m3u8`). The proxy must resolve relative paths and rewrite both variant playlists (`/playlist.m3u8?url=...`) and segment URLs.
3. **Disguised Extensions (`.pdf`, `.zst`, `.image`):**
   Upstream segments often use misleading file extensions like `.pdf` or `.zst` to evade filters, but are actually raw MPEG-TS video (or TS with PNG wrappers). FFmpeg and Kodi reject non-standard extensions by default. The proxy solves this by rewriting segment paths to `/seg.ts?url=...` and returning the `video/MP2T` MIME type.

## Python Proxy Server Code
Below is the full source code for the updated [`hls_proxy.py`](file:///home/ericchu/pg/Docs/HlsIptvProxy/hls_proxy.py) script:

```python
#!/usr/bin/env python3
"""
HLS Proxy for IPTV streams requiring custom Referer / Headers or PNG unwrapping.

Features:
- Passes custom Referer and auto-derived Origin to upstream CDNs/servers (e.g. Varnish, Cloudflare).
- Handles nested Master Playlists and Variant Streams (resolves relative URLs).
- Strips PNG headers if segments are disguised in PNG wrappers (TikTok CDN trick).
- Rewrites segment endpoints to /seg.ts?url=... so players (Kodi, FFmpeg, VLC) recognize valid TS extensions.
- Serves clean MPEG-TS with video/MP2T MIME type.

Usage:
    python3 hls_proxy.py "<m3u8_url>" [referer] [port]
    python3 hls_proxy.py "<m3u8_url>" --referer "<referer>" --port 8888

Kodi / Player URL:
    http://<this-machine-ip>:<port>/stream.m3u8

Update URL or Referer on the fly:
    curl "http://localhost:8888/set?url=<new_m3u8_url>&referer=<new_referer>"
"""

import sys
import argparse
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import socket

# ── Config ─────────────────────────────────────────────────────────────────
DEFAULT_PORT = 8888
UPSTREAM_M3U8 = ""      # Set via CLI or /set?url=...
UPSTREAM_REFERER = ""   # Set via CLI or /set?referer=...
TIMEOUT = 15            # seconds for upstream requests
# ───────────────────────────────────────────────────────────────────────────


def get_headers(referer: str = "") -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) Gecko/20100101 Firefox/154.0",
        "Accept": "*/*",
    }
    ref = referer or UPSTREAM_REFERER
    if ref:
        headers["Referer"] = ref
        parsed = urllib.parse.urlparse(ref)
        if parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    return headers


def fetch(url: str, referer: str = "") -> bytes:
    req = urllib.request.Request(url, headers=get_headers(referer))
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def find_ts_offset(data: bytes) -> int:
    """Locate the first valid MPEG-TS sync byte triplet (0x47 every 188 bytes)."""
    search_limit = min(4096, len(data) - 376)
    for i in range(max(0, search_limit)):
        if data[i] == 0x47 and data[i + 188] == 0x47 and data[i + 376] == 0x47:
            return i
    # Fallback if smaller or single sync byte
    if data.startswith(b"\x47"):
        return 0
    return -1


def rewrite_m3u8(content: bytes, base_url: str) -> bytes:
    """
    Rewrite playlist content:
    - Resolves relative URLs against base_url.
    - Routes sub-playlists (variant streams) to /playlist.m3u8?url=...
    - Routes video/audio segments to /seg.ts?url=... so players recognize valid TS extension.
    """
    lines = content.decode("utf-8", errors="replace").splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
        else:
            full_url = urllib.parse.urljoin(base_url, stripped)
            encoded = urllib.parse.quote(full_url, safe="")
            if ".m3u8" in stripped.lower() or ".m3u8" in full_url.lower():
                out.append(f"/playlist.m3u8?url={encoded}")
            else:
                out.append(f"/seg.ts?url={encoded}")
    return "\n".join(out).encode("utf-8")


class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [{self.client_address[0]}] {fmt % args}")

    def send_simple(self, code: int, msg: str = ""):
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global UPSTREAM_M3U8, UPSTREAM_REFERER
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # ── /set?url=<m3u8_url>&referer=<ref>  — update on the fly ─────────
        if parsed.path == "/set":
            new_url = params.get("url", [""])[0]
            new_ref = params.get("referer", [""])[0]

            if not new_url and not new_ref:
                self.send_simple(400, "Provide ?url=... or ?referer=... parameter")
                return

            if new_url:
                UPSTREAM_M3U8 = new_url
            if new_ref:
                UPSTREAM_REFERER = new_ref

            msg = f"Config updated:\n  Upstream URL: {UPSTREAM_M3U8}\n  Referer     : {UPSTREAM_REFERER or '(none)'}\n"
            print(f"\n{msg}")
            self.send_simple(200, msg)
            return

        # ── /stream.m3u8 or /playlist.m3u8  — serve rewritten playlist ─────
        if parsed.path in ("/", "/stream.m3u8", "/fox4k.m3u8", "/playlist.m3u8"):
            target_url = params.get("url", [""])[0] or UPSTREAM_M3U8
            if not target_url:
                self.send_simple(503, "No upstream URL set. Use /set?url=<m3u8_url> first.")
                return
            try:
                raw = fetch(target_url)
                rewritten = rewrite_m3u8(raw, target_url)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(rewritten)))
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(rewritten)
            except Exception as exc:
                print(f"  m3u8 fetch error: {exc}")
                self.send_simple(502, f"Upstream error: {exc}")
            return

        # ── /seg or /seg.ts?url=<encoded_segment_url>  — strip PNG, serve raw TS
        if parsed.path in ("/seg", "/seg.ts"):
            url = params.get("url", [""])[0]
            if not url:
                self.send_simple(400, "Missing ?url= parameter")
                return
            try:
                data = fetch(url)
                offset = find_ts_offset(data)
                if offset == -1:
                    print("  No TS sync found in segment, serving as-is")
                    ts_data = data
                else:
                    ts_data = data[offset:]
                self.send_response(200)
                self.send_header("Content-Type", "video/MP2T")
                self.send_header("Content-Length", str(len(ts_data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(ts_data)
            except Exception as exc:
                print(f"  Segment fetch error: {exc}")
                self.send_simple(502, f"Upstream error: {exc}")
            return

        self.send_simple(404, "Not found")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in its own thread for parallel segment downloads."""
    daemon_threads = True


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def parse_args():
    parser = argparse.ArgumentParser(description="HLS Proxy for IPTV streams requiring Referer or PNG unwrapping")
    parser.add_argument("pos_url", nargs="?", default="", help="Upstream .m3u8 URL")
    parser.add_argument("pos_referer_or_port", nargs="?", default="", help="Referer URL or Port")
    parser.add_argument("pos_port", nargs="?", type=int, default=None, help="Port number (if 2nd arg was referer)")
    parser.add_argument("-u", "--url", default="", help="Upstream .m3u8 URL")
    parser.add_argument("-r", "--referer", default="", help="Referer header to send to upstream")
    parser.add_argument("-p", "--port", type=int, default=None, help=f"Port to listen on (default: {DEFAULT_PORT})")

    args = parser.parse_args()

    url = args.url or args.pos_url
    referer = args.referer
    port = args.port

    if args.pos_referer_or_port:
        if args.pos_referer_or_port.isdigit():
            port = int(args.pos_referer_or_port)
        else:
            referer = args.pos_referer_or_port

    if args.pos_port:
        port = args.pos_port

    if port is None:
        port = DEFAULT_PORT

    return url, referer, port


if __name__ == "__main__":
    url, referer, port = parse_args()
    if url:
        UPSTREAM_M3U8 = url
    if referer:
        UPSTREAM_REFERER = referer

    local_ip = get_local_ip()
    server = ThreadedHTTPServer(("0.0.0.0", port), ProxyHandler)

    print("=" * 65)
    print("  HLS IPTV Proxy (Referer & PNG-Unwrap Support)")
    print("=" * 65)
    print(f"  Upstream : {UPSTREAM_M3U8 or '(none - update via /set?url=...)'}")
    print(f"  Referer  : {UPSTREAM_REFERER or '(none - update via /set?referer=...)'}")
    print(f"  Kodi URL : http://{local_ip}:{port}/stream.m3u8")
    print(f"  Update   : http://{local_ip}:{port}/set?url=<m3u8_url>&referer=<ref>")
    print("=" * 65)
    print("  Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.shutdown()
```
