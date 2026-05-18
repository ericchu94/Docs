# Deploying an App

## Overview

Apps are built with skaffold and deployed via helmsman. The version must be bumped in three places before building.

## Steps

### 1 — Bump the version

For each app (e.g. `downloader`), bump the version in:

```
<app>/package.json          "version": "X.Y.Z"
<app>/chart/Chart.yaml      version: X.Y.Z  +  appVersion: "X.Y.Z"
helmsman.yaml               version: "X.Y.Z"   (under the app's block)
```

All three must match or helmsman will fail validation.

### 2 — Build and push the image

Run from the app directory:

```bash
cd /home/ericchu/pg/patchy/<app>
skaffold build
```

Skaffold tags the image using `appVersion` from `chart/Chart.yaml` and pushes to `registry.patchy.dev/<app>:X.Y.Z`.

### 3 — Review the diff

Run from the patchy root (so the `.env` file is loaded):

```bash
cd /home/ericchu/pg/patchy
helmsman --show-diff -f helmsman.yaml
```

Verify only the expected changes appear (version labels + image tag). Investigate anything unexpected before applying.

### 4 — Apply

```bash
cd /home/ericchu/pg/patchy
helmsman --apply -f helmsman.yaml
```

## Verifying the deploy

```bash
kubectl rollout status statefulset/<app> -n default
kubectl get pod <app>-0 -n default
```

Check startup logs via Loki:

```bash
LOKI_ADDR=https://loki.patchy.dev logcli query '{namespace="default", pod="<app>-0"}' --since=2m --limit=30
```
