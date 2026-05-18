# ddclient: KubeJobFailed

## Steps

### 1 — Check the logs

```bash
kubectl logs -n ddclient job/<job-name> --tail=50
```

### 2 — Check recent job history

```bash
kubectl get jobs -n ddclient
```

### 3 — Remediate

If the failure is due to timeouts or transient network errors (not auth/config issues), **and** other recent jobs have completed successfully:

```bash
kubectl delete job -n ddclient <job-name>
```

If recent jobs are also failing, do not delete — investigate the pattern (auth failure, config issue, upstream DDNS provider problem).
