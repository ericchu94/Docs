# PVC Resize: iSCSI Volumes (democratic-csi)

## When to use

The `KubePersistentVolumeFillingUp` alert fired, or a PVC needs more space. For iSCSI-backed volumes (storage class `freenas-iscsi-csi`), the block resize happens automatically but the filesystem resize requires manual intervention.

## Steps

### 1 — Expand the PVC

```bash
# Check current size
kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.spec.resources.requests.storage}'

# Patch to 4x current size
kubectl patch pvc <pvc-name> -n <namespace> -p '{"spec":{"resources":{"requests":{"storage":"<4x>"}}}}'
```

### 2 — Check if filesystem resize is needed

```bash
kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.status.conditions}'
```

If `FileSystemResizePending` is present, continue below. If not, you're done.

### 3 — Scale the workload down

The volume must be unmounted before resizing.

**STOP — check the PVC retention policy first.** If `whenScaled` is `Delete`, scaling to 0 deletes the PVC (and, since reclaim policy is `Delete`, the backing zvol too). Scaling back up then provisions a brand-new empty volume from `volumeClaimTemplates`, silently discarding both your resize and the data.

```bash
kubectl get statefulset <name> -n <namespace> -o jsonpath='{.spec.persistentVolumeClaimRetentionPolicy}{"\n"}'
```

- If `whenScaled` is `Retain` (the k8s default) — safe to proceed below.
- If `whenScaled` is `Delete` — do **not** scale down. Instead, either:
  - Patch the policy to `Retain` first (`kubectl patch statefulset <name> -n <namespace> -p '{"spec":{"persistentVolumeClaimRetentionPolicy":{"whenScaled":"Retain"}}}'`), scale down/up as normal, then patch it back if the chart expects `Delete`; or
  - Skip steps 3 and 7-8 (unmount/fsck/resize2fs) entirely and rely on kubelet's online filesystem expansion instead: just leave the pod running after step 1. The PVC condition will read `FileSystemResizePending` with the message "Waiting for user to (re-)start a pod to finish file system resize" — a normal `kubectl delete pod <pod-name>` (which the StatefulSet controller reschedules, no scale-to-0 involved) is enough to trigger the resize on remount for CSI drivers that support online expansion (this one does).

```bash
kubectl scale statefulset <name> -n <namespace> --replicas=0
kubectl wait --for=delete pod/<pod-name> -n <namespace> --timeout=60s
```

### 4 — Find the node the PV is attached to

```bash
kubectl get volumeattachment -o json | \
  jq -r '.items[] | select(.spec.source.persistentVolumeName=="<pv-name>") | .spec.nodeName'
```

### 5 — Find the democratic-csi node pod on that node

```bash
kubectl get pod -n democratic-csi -l app.kubernetes.io/name=democratic-csi,app.kubernetes.io/component=node-linux \
  -o jsonpath='{.items[?(@.spec.nodeName=="<node-name>")].metadata.name}'
```

### 6 — Identify the block device

```bash
kubectl exec -n democratic-csi <node-pod> -c csi-driver -- lsblk -o NAME,SIZE,MOUNTPOINT
```

Look for the device that is only globalmounted (no pod volume mount) and matches the expected size.

### 7 — Unmount the globalmount

```bash
kubectl exec -n democratic-csi <node-pod> -c csi-driver -- \
  umount /var/lib/kubelet/plugins/kubernetes.io/csi/org.democratic-csi.iscsi/<hash>/globalmount
```

### 8 — fsck and resize

```bash
kubectl exec -n democratic-csi <node-pod> -c csi-driver -- e2fsck -f -y /dev/<device>
kubectl exec -n democratic-csi <node-pod> -c csi-driver -- resize2fs /dev/<device>
```

### 9 — Scale back up and verify

```bash
kubectl scale statefulset <name> -n <namespace> --replicas=1
kubectl exec -n <namespace> <pod> -- df -h <mountpath>
```

## Notes

- Do NOT update `volumeClaimTemplates` in the helm chart — they are immutable in Kubernetes and changing them will break `helmsman apply`.
- The globalmount path hash is derived from the PV name; use tab-completion or `ls` inside the csi-driver container to find it.
- **Prefer the online-expansion path (delete pod, don't scale to 0) whenever possible.** Steps 4-8 (manual unmount/fsck/resize2fs) are only needed if the CSI driver/filesystem doesn't support online expansion, or if you've confirmed the globalmount actually survives a scale-to-0 in your case. This driver + ext4 supports online expansion, so in practice steps 3-8 are usually unnecessary.
- **Incident (2026-09-06):** resized `storage-loki-0` by scaling `loki` StatefulSet to 0/1. The `loki` chart sets `persistentVolumeClaimRetentionPolicy.whenScaled: Delete` (unlike `alertmanager`/`prometheus` in this cluster, which use the k8s default `Retain`). Scaling to 0 deleted the PVC and its backing zvol; scaling back up silently provisioned a fresh empty 10Gi volume from `volumeClaimTemplates`, losing all Loki log data. Always check the retention policy (step 3) before scaling any StatefulSet to 0 for this procedure.
