# Z-Wave JS UI: USB Device Not Found

## Symptoms

- Z-Wave JS UI cannot open `/dev/ttyUSB0`
- `kubectl exec ... -- stat /dev/ttyUSB0` shows `directory` instead of `character special file`
- `talosctl -n <node-ip> ls -l /dev | grep ttyACM` shows `drwxr-xr-x` instead of `Dcrw-------`

## Background

The pod mounts the host's `/dev/ttyACM0` as `/dev/ttyUSB0` via a `hostPath` volume. If the USB stick loses power or is disconnected while a pod is running, the kernel removes the character device from devtmpfs but may leave a stale directory placeholder behind. When the stick is replugged, the kernel cannot recreate the device node over an existing directory, so `/dev/ttyACM0` remains a directory and the container sees an empty directory instead of a serial device.

## Diagnosis

```bash
# 1. Check the host device type — should show Dcrw-------, not drwxr-xr-x
talosctl -n <node-ip> ls -l /dev | grep -E "tty(ACM|USB)"

# 2. Check kernel USB errors
talosctl -n <node-ip> dmesg | grep -i -E "usb|ttyACM|cdc_acm" | tail -30

# 3. Check what the container sees
kubectl exec -n home-assistant <pod> -- stat /dev/ttyUSB0
```

## Fix

### Step 1 — Remove the stale directory (if present)

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: dev-fix
  namespace: home-assistant
spec:
  nodeSelector:
    kubernetes.io/hostname: <node-name>   # e.g. talos-hrv-yti
  restartPolicy: Never
  containers:
  - name: fix
    image: alpine
    securityContext:
      privileged: true
    command: ["sh", "-c", "rmdir /host-dev/ttyACM0 && echo 'removed' || echo 'nothing to remove'"]
    volumeMounts:
    - mountPath: /host-dev
      name: host-dev
  volumes:
  - name: host-dev
    hostPath:
      path: /dev
EOF

kubectl logs dev-fix -n home-assistant
kubectl delete pod dev-fix -n home-assistant
```

### Step 2 — Replug the USB stick

Physically unplug and replug the Z-Wave USB stick from the node. Confirm the kernel sees it:

```bash
talosctl -n <node-ip> ls -l /dev | grep ttyACM
# Expected: Dcrw-------   0   0   0   <timestamp>   ttyACM0
```

### Step 3 — Restart the pod

```bash
kubectl rollout restart deployment/zwave-js-ui -n home-assistant
kubectl rollout status deployment/zwave-js-ui -n home-assistant
```

### Step 4 — Verify

```bash
POD=$(kubectl get pods -n home-assistant -l app.kubernetes.io/name=zwave-js-ui -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n home-assistant $POD -- stat /dev/ttyUSB0
# Expected: character special file, Device type: a6,0
```

## Notes

- The node for this deployment is `talos-hrv-yti` (IP `10.0.0.201`).
- The hostPath volume maps host `/dev/ttyACM0` → container `/dev/ttyUSB0`. The device name mismatch is intentional.
- `kubectl debug node` with `nsenter` does not work on Talos — Talos blocks access to the host mount namespace from debug pods. Use the hostPath pod approach above instead.
- The `hostPath.type: ""` (unset) in the deployment means Kubernetes does no pre-existence check, which is how the stale directory mount goes undetected at pod start.
