# Checking Alerts

## Query active alerts

```bash
amtool alert -o extended --alertmanager.url=https://alertmanager.patchy.dev
```

Summarize results grouped by severity, highlight critical issues.

## Alert runbooks

| Alert | Runbook |
|-------|---------|
| `DownloadTaskStuck` | [downloader/download-task-stuck.md](../downloader/download-task-stuck.md) |
| `KubePersistentVolumeFillingUp` | [kubernetes/pvc-iscsi-resize.md](../kubernetes/pvc-iscsi-resize.md) |
| `KubeJobFailed` (ddclient namespace) | [ddclient/job-failed.md](../ddclient/job-failed.md) |
| `ZWaveControllerUnavailable` | [home-assistant/usb-device-not-found.md](../home-assistant/usb-device-not-found.md) |
