# Fork Divergence Policy

This repository is a personal fork of [`CoderLuii/HolyClaude`](https://github.com/CoderLuii/HolyClaude).
The fork exists to keep the bundled CloudCLI (`siteboon/claudecodeui`) on a
faster cadence than upstream, and to deploy the resulting image to a private
Kubernetes cluster.

## Path ownership

| Path                                | Ownership          | Notes                                                                                                |
|-------------------------------------|--------------------|------------------------------------------------------------------------------------------------------|
| `Dockerfile`                        | tracked upstream   | Modified only by the `cloudcli-sync` workflow (vendor tarball reference) and merge resolution.       |
| `scripts/`                          | tracked upstream   | Bootstrap, entrypoint, notifier — accept upstream changes.                                           |
| `s6-overlay/`                       | tracked upstream   | Service definitions — accept upstream changes.                                                       |
| `config/`                           | tracked upstream   | Default settings + claude-memory templates.                                                          |
| `docs/`                             | tracked upstream   | Except `docs/fork/**` which is ours.                                                                 |
| `vendor/artifacts/`                 | **ours, automated**| Managed by the `cloudcli-sync` workflow (tracks `@cloudcli-ai/cloudcli`). Do not edit by hand.        |
| `docs/fork/**`                      | **ours forever**   | Fork-specific docs (this file, runbooks).                                                            |
| `.github/workflows/`                | **ours forever**   | Replaced wholesale; upstream's `docker-publish.yml` was removed because it targets Docker Hub creds we do not have. |
| `.github/CODEOWNERS`                | **ours forever**   |                                                                                                      |
| `.github/labels.yml`                | **ours forever**   |                                                                                                      |
| `deploy/k8s/` (if added)            | **ours forever**   | Source-of-truth manifests live in `fulviofreitas/ff-k8s`, this is a reference snapshot.              |

## Sync strategy

1. `holyclaude-upstream-sync` workflow runs daily. When `CoderLuii/HolyClaude`
   master advances, it opens a PR labeled `upstream-sync` into our `master`.
2. `cloudcli-sync` workflow runs daily. When `npm view @cloudcli-ai/cloudcli version`
   returns a value newer than what is vendored, it fetches the new tarball,
   replaces `vendor/artifacts/cloudcli-ai-cloudcli-*.tgz`, rewrites the
   `Dockerfile` lines that reference the old version, and opens a PR labeled
   `cloudcli-sync`. **Note:** the upstream package was renamed from
   `@siteboon/claude-code-ui` to `@cloudcli-ai/cloudcli` at the
   `@siteboon@2.0.0` redirect-stub release. The fork tracks the new package;
   the legacy name is recorded here for archeology.
3. CI runs on both PR types: hadolint, shellcheck, build (no push), smoke
   tests (verify the served UI version matches the vendored version), Trivy
   scan. Smoke tests are how we discover that the JS-bundle patches in the
   `Dockerfile` (lines tagged `# patch v1.2.2-*`) have rotted on a new
   CloudCLI release. The patches are fork-specific UX tweaks (model
   selector, websocket binary frames, scroll preservation) — when their
   anchor strings no longer match the bundle, the smoke test prints
   `WARN  patch missing in …` rather than failing the build, and the
   `cloudcli-sync` PR description surfaces the same fact for human
   attention. Without the patches the UI still runs; you just lose the
   fork-specific UX improvements until the patches are re-ported.
4. Merge to `master` triggers the `build` workflow which publishes
   `ghcr.io/fulviofreitas/holyclaude:<semver>-fork.<n>` plus
   `cloudcli-<X.Y.Z>`, `latest`, `slim`, and `sha-<short>` tags.
5. The cluster-side image switchover (from `coderluii/holyclaude:*` to
   `ghcr.io/fulviofreitas/holyclaude:*`) is owned by the GitOps repo
   `fulviofreitas/ff-k8s` and is wired up there. The
   `.github/workflows/manifest-bump.yml` workflow in this repo remains
   callable via `workflow_dispatch` once the GitOps side is ready; it is
   no longer auto-triggered from `build.yml` so freshly-published images
   never silently force a downstream PR.

## Patch rot

The `Dockerfile` carries inline `sed`/`perl` patches against minified CloudCLI
bundle files (e.g. `index-X3ImjnMV.js`). The bundle filename contains a content
hash that will change every CloudCLI release. When the hash changes:

- The patch shell expressions look for the old filename and silently print
  a warning. The image still builds.
- The smoke test checks that the patched behavior is still present at
  runtime. It is the canary.
- The `cloudcli-sync` PR description includes a section listing every
  `# patch` block whose target file no longer matches, so the human merging
  the PR knows to refresh the patches before merging.

## Rollback

The Kubernetes deployment pins an image tag, not `latest`:

```yaml
image: ghcr.io/fulviofreitas/holyclaude:1.2.2-fork.1
```

To roll back:

1. Find the previous tag in [GHCR](https://github.com/fulviofreitas/HolyClaude/pkgs/container/holyclaude).
2. In `fulviofreitas/ff-k8s`, edit `kubernetes/apps/holyclaude/deployment.yaml`
   and set the image to the previous tag.
3. Commit + push. ArgoCD will reconcile within minutes.

For an emergency rollback without going through git:

```bash
kubectl -n holyclaude set image deploy/holyclaude \
  holyclaude=ghcr.io/fulviofreitas/holyclaude:<previous-tag>
```

This will be reverted by ArgoCD's self-heal — use only as an emergency
stop-gap before opening the rollback PR.
