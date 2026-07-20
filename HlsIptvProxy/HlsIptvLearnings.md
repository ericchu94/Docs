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

## Python Proxy Server Code
Below is the full source code for the `hls_proxy.py` script we developed to handle this:

```python
#!/usr/bin/env python3
"""
HLS Proxy for PNG-wrapped IPTV streams.

Fetches segments from TikTok CDN (wrapped in PNG), strips the PNG header,
and serves clean MPEG-TS to Kodi (or any HLS player) over HTTP.

Usage:
    python3 hls_proxy.py "<m3u8_url>" [port]

Kodi URL:
    http://<this-machine-ip>:<port>/stream.m3u8

Update URL on the fly (when token expires):
    curl "http://localhost:8888/set?url=<new_m3u8_url>"
"""

import sys
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import socket

# ── Config ─────────────────────────────────────────────────────────────────
DEFAULT_PORT = 8888
UPSTREAM_M3U8 = ""  # Set via command-line arg or /set?url=... endpoint
TIMEOUT = 15        # seconds for upstream requests
# ───────────────────────────────────────────────────────────────────────────


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def find_ts_offset(data: bytes) -> int:
    """Locate the first valid MPEG-TS sync byte triplet (0x47 every 188 bytes)."""
    search_limit = min(4096, len(data) - 376)
    for i in range(search_limit):
        if data[i] == 0x47 and data[i + 188] == 0x47 and data[i + 376] == 0x47:
            return i
    return -1


def rewrite_m3u8(content: bytes) -> bytes:
    """Replace upstream segment URLs with local proxy /seg?url=... URLs."""
    lines = content.decode("utf-8", errors="replace").splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("http"):
            encoded = urllib.parse.quote(stripped, safe="")
            out.append(f"/seg?url={encoded}")
        else:
            out.append(line)
    return "\n".join(out).encode("utf-8")


class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [{self.client_address[0]}] {fmt % args}")

    def send_simple(self, code: int, msg: str = ""):
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global UPSTREAM_M3U8
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # ── /set?url=<new_m3u8_url>  — update upstream on the fly ──────────
        if parsed.path == "/set":
            url = params.get("url", [""])[0]
            if not url:
                self.send_simple(400, "Missing ?url= parameter")
                return
            UPSTREAM_M3U8 = url
            print(f"\n  Upstream updated:\n    {UPSTREAM_M3U8}\n")
            self.send_simple(200, f"Upstream set to:\n{UPSTREAM_M3U8}\n")
            return

        # ── /stream.m3u8  — serve rewritten playlist to Kodi ───────────────
        if parsed.path in ("/", "/stream.m3u8", "/fox4k.m3u8", "/playlist.m3u8"):
            if not UPSTREAM_M3U8:
                self.send_simple(503, "No upstream URL set. Use /set?url=<m3u8_url> first.")
                return
            try:
                raw = fetch(UPSTREAM_M3U8)
                rewritten = rewrite_m3u8(raw)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(rewritten)))
                self.send_header("Cache-Control", "no-cache, no-store")
                self.end_headers()
                self.wfile.write(rewritten)
            except Exception as exc:
                print(f"  m3u8 fetch error: {exc}")
                self.send_simple(502, f"Upstream error: {exc}")
            return

        # ── /seg?url=<encoded_segment_url>  — strip PNG, serve raw TS ──────
        if parsed.path == "/seg":
            url = params.get("url", [""])[0]
            if not url:
                self.send_simple(400, "Missing ?url= parameter")
                return
            try:
                data = fetch(url)
                offset = find_ts_offset(data)
                if offset == -1:
                    print("  No TS sync found in segment")
                    self.send_simple(502, "No MPEG-TS sync byte found in segment")
                    return
                ts_data = data[offset:]
                self.send_response(200)
                self.send_header("Content-Type", "video/MP2T")
                self.send_header("Content-Length", str(len(ts_data)))
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


if __name__ == "__main__":
    port = DEFAULT_PORT

    if len(sys.argv) >= 2:
        UPSTREAM_M3U8 = sys.argv[1]
    if len(sys.argv) >= 3:
        port = int(sys.argv[2])

    local_ip = get_local_ip()
    server = ThreadedHTTPServer(("0.0.0.0", port), ProxyHandler)

    print("=" * 60)
    print("  HLS PNG-Unwrap Proxy")
    print("=" * 60)
    if UPSTREAM_M3U8:
        print(f"  Upstream : {UPSTREAM_M3U8}")
    else:
        print(f"  Upstream : (none - update via /set?url=...)")
    print(f"  Kodi URL : http://{local_ip}:{port}/stream.m3u8")
    print(f"  Update   : http://{local_ip}:{port}/set?url=<new_m3u8_url>")
    print("=" * 60)
    print("  Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.shutdown()
```
