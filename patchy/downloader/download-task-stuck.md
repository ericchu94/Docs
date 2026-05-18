# Downloader: DownloadTaskStuck Investigation

## Background

The `DownloadTaskStuck` alert should never fire — it represents a bug in the downloader application. A 24-hour kill timeout in the app is supposed to prevent tasks from running indefinitely. If this alert fires, investigate and fix the root cause rather than just killing the process.

Source: `/home/ericchu/pg/patchy/downloader/lib/downloader.ts`

## Steps

### 1 — Get context from the alert

Extract `url_id` and `download_id` from the alert labels.

### 2 — Check what's running in the container

```bash
kubectl exec -n default downloader-0 -- ps aux
```

Look for:
- Is `yt-dlp` running?
- Is `ffmpeg` running as an orphan (no yt-dlp parent)?
- Any unexpected PIDs?

### 3 — Check the oldest logs for the stuck task

```bash
logcli query '{namespace="default", pod="downloader-0"} | json | urlId="<url_id>"' \
  --since=48h --limit=50 --forward
```

### 4 — Check if the 24h kill timeout fired

The `download_id` timestamp prefix is Unix milliseconds (e.g. `1774703793203` → start time). Calculate 24h after start, then query logs around that window:

```bash
logcli query '{namespace="default", pod="downloader-0"}' \
  --from="<24h-after-start>" --to="<24h-after-start+1h>" --limit=50
```

Look for: `"download timeout exceeded"` log message. If absent, the timeout never fired.

### 5 — Identify the cause

Common causes:
- Slow download speed causing yt-dlp to hang
- yt-dlp errors leaving ffmpeg running as an orphan
- Orphaned ffmpeg holding stdio pipes open, preventing yt-dlp from exiting
- Network issues causing silent hangs

### 6 — Fix the bug

The right remediation is fixing the underlying bug in the downloader app:
1. Identify the bug in `downloader.ts`
2. Propose and implement a code fix
3. Build with skaffold
4. Deploy via helmsman
