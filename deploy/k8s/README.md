# Kubernetes deployment (reference snapshot)

This directory is a **reference snapshot** of the manifests used to deploy
the fork image. The source-of-truth lives in
[`fulviofreitas/ff-k8s`](https://github.com/fulviofreitas/ff-k8s) at
`k8s-homelab/kubernetes/apps/holyclaude/` — those are the manifests
ArgoCD reconciles.

This snapshot exists so:

1. Reviewers of `cloudcli-sync` and `upstream-sync` PRs can see the
   deployment shape without context-switching repos.
2. If you want to run this fork on your own cluster, you have a starting
   point that doesn't depend on the homelab's bespoke Cilium / Istio /
   external-dns setup.

## Files

| File | Purpose |
|---|---|
| [`deployment.yaml`](deployment.yaml) | Deployment + PVCs |
| [`service.yaml`](service.yaml) | ClusterIP for the web UI |
| [`pdb.yaml`](pdb.yaml) | PodDisruptionBudget — single-replica, max 0 unavailable |
| [`kustomization.yaml`](kustomization.yaml) | Kustomize index |
| [`httproute.yaml.example`](httproute.yaml.example) | Example HTTPRoute exposing the UI via Cloudflare Tunnel + Istio Gateway |

## Drift policy

The reference snapshot is **not** auto-synced from `ff-k8s`. After
landing a deployment-shape change in `ff-k8s`, mirror it here in a
follow-up commit so the snapshot stays representative. Image-tag bumps
are tracked only in `ff-k8s` — do not echo them here, the snapshot
intentionally pins a stale-but-explanatory tag.
