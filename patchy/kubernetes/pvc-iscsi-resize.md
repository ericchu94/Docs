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
kubectl get pod -n democratic-csi -l app=democratic-csi-node \
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
