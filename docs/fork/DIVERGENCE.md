# Fork Divergence Policy

This repository is a personal fork of [`CoderLuii/HolyClaude`](https://github.com/CoderLuii/HolyClaude).
The fork exists to keep the bundled CloudCLI (`@cloudcli-ai/cloudcli`, the
renamed continuation of `@siteboon/claude-code-ui`) on a faster cadence than
upstream.

## Path ownership

| Path                                | Ownership          | Notes                                                                                                |
|-------------------------------------|--------------------|------------------------------------------------------------------------------------------------------|
| `Dockerfile`                        | tracked upstream   | Modified only by the `cloudcli-sync` workflow (vendor tarball reference) and merge resolution.       |
| `scripts/`                          | tracked upstream   | Bootstrap, entrypoint, notifier — accept upstream changes.                                           |
| `s6-overlay/`                       | tracked upstream   | Service definitions — accept upstream changes.                                                       |
| `config/`                           | tracked upstream   | Default settings + claude-memory templates.                                                          |
| `docs/`                             | tracked upstream   | Except `docs/fork/**` which is ours.                                                                 |
| `vendor/artifacts/`                 | **ours, automated**| Managed by the `cloudcli-sync` workflow (tracks `@cloudcli-ai/cloudcli`). Do not edit by hand.       |
| `docs/fork/**`                      | **ours forever**   | Fork-specific docs (this file, runbooks).                                                            |
| `.github/workflows/`                | **ours forever**   | Replaced wholesale; upstream's `docker-publish.yml` was removed because it targets Docker Hub creds we do not have. |
| `.github/CODEOWNERS`                | **ours forever**   |                                                                                                      |
| `.github/labels.yml`                | **ours forever**   |                                                                                                      |
| `.releaserc.json`                   | **ours forever**   | semantic-release config for the fork's own semver lineage.                                           |
| `.commitlintrc.json`                | **ours forever**   | conventional-commits enforcement for the commitlint CI gate.                                         |

## Sync strategy

> Three independent automations watch three different upstreams. Each has a
> narrow scope so they do not collide.
>
> | Automation | What it tracks | Why it can't be merged with the others |
> |---|---|---|
> | `upstream-sync` | `CoderLuii/HolyClaude` master | git-level merge, not a dependency bump |
> | `cloudcli-sync` | npm `@cloudcli-ai/cloudcli` (vendored as a tarball) | requires `npm pack` + binary commit + Dockerfile rewrite, which Renovate cannot do |
> | `renovate` (this repo) | github-actions `uses:` versions, node base image, `S6_OVERLAY_VERSION` ARG | standard Renovate scope; auto-merge minor/patch |

1. `upstream-sync` workflow runs daily. When `CoderLuii/HolyClaude`
   master advances, it opens a PR labeled `upstream-sync` into our `master`.
2. `cloudcli-sync` workflow runs daily. When `npm view @cloudcli-ai/cloudcli version`
   returns a value newer than what is vendored, it fetches the new tarball,
   replaces `vendor/artifacts/cloudcli-ai-cloudcli-*.tgz`, rewrites the
   `Dockerfile` lines that reference the old version, and opens a PR labeled
   `cloudcli-sync`. **Note:** the upstream package was renamed from
   `@siteboon/claude-code-ui` to `@cloudcli-ai/cloudcli` at the
   `@siteboon@2.0.0` redirect-stub release. The fork tracks the new package;
   the legacy name is recorded here for archeology.
3. `🧪 ci` runs on both PR types: commitlint, hadolint, shellcheck,
   yamllint, build (no push), smoke tests (verify the served UI version
   matches the vendored version), Trivy scan. Smoke tests are how we
   discover that the JS-bundle patches in the `Dockerfile` (lines tagged
   `# patch v1.2.2-*`) have rotted on a new CloudCLI release. The patches
   are fork-specific UX tweaks (model selector, websocket binary frames,
   scroll preservation) — when their anchor strings no longer match the
   bundle, the smoke test prints `WARN  patch missing in …` rather than
   failing the build, and the `cloudcli-sync` PR description surfaces the
   same fact for human attention. Without the patches the UI still runs;
   you just lose the fork-specific UX improvements until the patches are
   re-ported.
4. Merge to `master` triggers the chain documented in the README "This is
   a fork" callout: `🧪 ci` → `🚀 release` (semantic-release cuts a
   `vX.Y.Z` tag based on conventional commits) → `🐳 docker-publish`
   (multi-arch slim image to GHCR with semver, sha, and `latest` tags;
   bundled CloudCLI version recorded as the `io.holyclaude.cloudcli-version`
   image-config label). The upstream `full` variant blocks
   (Junie/OpenCode/Azure CLI/PDF/video tooling) remain in the Dockerfile
   but the build job does not exercise them.

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
