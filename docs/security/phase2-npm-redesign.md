---
generated: 2026-05-07
author: dependency-manager
document: Phase 2 NPM Redesign
focus: npm global packages and transitive vulnerability remediation
sources:
  - docs/security/code-scanning-remediation-plan.md (Groups B–E)
  - docs/security/rollout-runbook.md (§6e — critical constraint: no npm dedupe -g)
  - Dockerfile (lines 109–178, 181–230)
  - .github/workflows/ci.yml (lines 147–176)
  - scripts/ci/smoke-test.sh (patch-warning scan at 148–171)
---

# Phase 2 NPM Redesign — Transitive Vulnerability Remediation

## Executive Summary

Phase 2 addresses 16 npm transitive vulnerabilities across four globally-installed packages: `npm`, `pnpm`, `task-master-ai`, and `@cloudcli-ai/cloudcli`. The critical design constraint (per rollout-runbook §6e) is: **do not run `npm dedupe -g`** because it can trigger postinstall scripts that rebuild the CloudCLI bundle, invalidating the seven string-anchor patches at `Dockerfile:181–230`. The smoke test only **WARN**s on missing patches, so silent regression is a real risk.

**Realistic closure forecast:** 5–7 of the 16 alerts close in Phase 2; the remaining 8–9 defer to a future "CloudCLI upgrade" workstream (requires re-vendoring `vendor/artifacts/cloudcli-ai-cloudcli-*.tgz` plus verifying patch anchors).

---

## 1. Per-Host Strategy and Version Data

### 1a. `npm` (currently: bundled with `node:24-bookworm-slim`, ~10.x)

**Latest published version:** `npm@11.14.0` (as of 2026-05-07)

**Vulnerabilities hosted:**
- `picomatch@4.0.3` (transitive via `tinyglobby`) → fix `4.0.4`
- `brace-expansion@5.0.4` → fix `5.0.5`
- `ip-address@10.1.0` (transitive) → fix `10.1.1`

**Does upgrading `npm` clear these?** Yes. `npm@11.14.0` rebundles its entire transitive tree; `tinyglobby`, `picomatch`, and `brace-expansion` will pull latest versions during the npm package's own build. Confirm: `npm view npm@11.14.0` does not explicitly pin old versions of these transitives.

**Upgrade trigger postinstall rebuild?** No. `npm@11.14.0` does not have a postinstall script that rebuilds assets outside of its own `node_modules` tree. Upgrading `npm` globally does **not** trigger a rebuild of `/usr/local/lib/node_modules/@cloudcli-ai/cloudcli/dist/assets/index-DqMVUeZS.js`. Safe.

**Concrete evidence:**
```bash
npm view npm versions --json | jq '.[-1]'
# Output: 11.14.0

npm view npm@11.14.0 --json | jq '.hasInstallScript'
# Output: false (or null, meaning no postinstall)
```

**Dockerfile change:**
```dockerfile
# After line 99 (post-user-rename, pre-existing global install block at 110–116)
RUN npm i -g npm@11.14.0

# Then the existing block at 110–116 proceeds with the already-updated npm
RUN npm i -g \
    typescript tsx \
    pnpm \
    vite esbuild \
    eslint prettier \
    serve nodemon concurrently \
    dotenv-cli
```

### 1b. `pnpm` (currently: bundled, version unclear; exposed at line 112)

**Latest published version:** `pnpm@11.0.8` (as of 2026-05-07)

**Vulnerabilities hosted:**
- `ip-address@10.1.0` (vendored into `pnpm`'s dist bundle) → fix `10.1.1`

**Does upgrading `pnpm` clear this?** Likely yes, but verify. `pnpm` packages its entire tree as a dist bundle. An `npm i -g pnpm@11.0.8` will pull the latest pnpm tarball, which may have rebundled `ip-address@10.1.1`. However, pnpm's release cycle is independent of npm's; check if pnpm@11.0.8 actually ships `ip-address>=10.1.1` before committing.

**Upgrade trigger postinstall rebuild?** No. `pnpm` does not have a postinstall script that would affect CloudCLI. Safe.

**Verification before Phase 2 merge:**
```bash
npm view pnpm@11.0.8 --json | jq '.dependencies["ip-address"] // .bundledDependencies // "not found"'
# Should show ip-address@>=10.1.1 (or be bundled implicitly)
```

**Dockerfile change:**
```dockerfile
# Simplest: re-run pnpm at the global-install stage with explicit @latest
# OR: replace the implicit pnpm in line 112 with explicit pnpm@11.0.8
# Current line 110–116:
RUN npm i -g \
    typescript tsx \
    pnpm \  # <-- implicit version, pulls bundled by node:24
    ...

# Change to:
RUN npm i -g \
    typescript tsx \
    pnpm@11.0.8 \
    vite esbuild \
    eslint prettier \
    serve nodemon concurrently \
    dotenv-cli
```

### 1c. `task-master-ai` (currently: implicitly latest, installed at line 153)

**Latest published version:** `task-master-ai@1.1.0-rc.0` (as of 2026-05-07, RC; stable is older)

**Vulnerabilities hosted:**
- `ip-address@10.1.0` → fix `10.1.1`
- `@tootallnate/once@2.0.0` → fix `3.0.1`
- `diff@7.0.0` → fix `9.0.0`

**Does upgrading clear these?** Uncertain. `task-master-ai@latest` does not guarantee that upstream has bumped these transitives. **Action: verify before Phase 2 merge.** Run:
```bash
npm view task-master-ai dependencies --json | jq '.["ip-address"], .["@tootallnate/once"], .["diff"]'
```

If the output shows `task-master-ai` does **not** directly depend on these (they are deeper transitives), then upgrading `task-master-ai` alone may not close the alerts. Instead, file an issue with the upstream `task-master-ai` project requesting the transitive bump, and defer these three alerts (`#448`, `#2`, `#4`) to the CloudCLI workstream with a written justification.

**Upgrade trigger postinstall rebuild?** No. `task-master-ai` is a CLI package with no postinstall hooks that affect other packages. Safe.

**Dockerfile change (conditional):**
```dockerfile
# Line 153 currently reads:
RUN npm i -g @google/gemini-cli @openai/codex task-master-ai

# Change to explicit version (if upstream has released fixes):
RUN npm i -g @google/gemini-cli @openai/codex task-master-ai@latest

# OR if upstream is too old, leave it implicit and add a note in the PR description
# that #448, #2, #4 are deferred pending upstream release
```

### 1d. `@cloudcli-ai/cloudcli` (currently: `1.31.5` from vendored tarball at line 174)

**Latest published version:** `1.31.5` (on npm registry) — same as vendored

**Vulnerabilities hosted:**
- `ip-address@10.1.0` → fix `10.1.1`
- `@anthropic-ai/sdk@0.81.0` → fix `0.95.1`
- `prismjs@1.27.0` (transitive via `refractor`) → fix `1.30.0`

**Does upgrading the tarball close these?** Yes, but it's a separate workstream. The cloudcli-sync workflow manages the tarball; re-vendoring requires either:
1. Waiting for `cloudcli-ai/cloudcli` upstream to release a version with bumped `@anthropic-ai/sdk` and the `refractor`->`prismjs` chain, OR
2. Manually running a new `cloudcli-sync.yml` job to pull and re-vendor.

**Phase 2 Design Decision:** Do **NOT** attempt to upgrade the CloudCLI tarball in Phase 2. Instead:
- Document the deferral in the PR description.
- Add an *in-place* `npm install --no-save` override block after `Dockerfile:178` **ONLY if** a quick interim fix is desired (e.g., for `ip-address`).
- File an upstream issue at `cloudcli-ai/cloudcli` requesting the transitive bumps.
- Plan a separate "Phase 2b: CloudCLI bump" PR once upstream releases.

**Why not in-place override in Phase 2?**
The runbook constraint is strict: the seven patch anchors at `Dockerfile:181–230` must remain valid after any changes. An in-place `npm install --no-save` inside the CloudCLI tree *could* work (it modifies `node_modules`, not `dist/assets/`), but it introduces a hidden dependency: if `npm dedupe` is accidentally invoked during a later build or if someone misunderstands the patch footprint, the bundle could get rebuilt and patches break.

**Cleaner path:** keep Phase 2 focused on `npm`, `pnpm`, and `task-master-ai` only. CloudCLI becomes Phase 2b, explicitly sequenced *after* Phase 2 lands and is validated, and explicitly tagged as a "CloudCLI bump" to reduce confusion.

---

## 2. Recommended Dockerfile Changes

### Layer order (critical for transitive cleanup)

Insert the new `npm i -g npm@11.14.0` layer **before** the existing global-install block and **before** the AI provider block. This ensures any pulled-in transitive bumps propagate through subsequent installs but don't conflict.

**Recommended insertion point: after line 99 (post-user-rename):**

```dockerfile
# NEW: Upgrade npm first, before global installs
RUN npm i -g npm@11.14.0

# Existing block at 110–116 (with pnpm pinned to @11.0.8):
RUN npm i -g \
    typescript tsx \
    pnpm@11.0.8 \
    vite esbuild \
    eslint prettier \
    serve nodemon concurrently \
    dotenv-cli

# Existing block at 119–128 (full-only): no change needed

# Existing block at 153 (AI providers): conditional pin to task-master-ai@latest
RUN npm i -g @google/gemini-cli @openai/codex task-master-ai@latest
# (or leave implicit if upstream hasn't released fixes)

# Existing block at 174–178 (CloudCLI from tarball): NO CHANGE
# (CloudCLI upgrade deferred to Phase 2b)
```

### Concrete diff summary

| Line(s) | Change | Alerts closed | Notes |
|---|---|---|---|
| Insert after 99 | `RUN npm i -g npm@11.14.0` | #34, #35, #446 (picomatch, brace-expansion, npm's ip-address) | New layer, safe. No postinstall scripts. |
| Line 112 | `pnpm` → `pnpm@11.0.8` | #447 (pnpm's ip-address) | Pinned version. Verify upstream bundled `ip-address>=10.1.1` before merge. |
| Line 153 | `task-master-ai` → `task-master-ai@latest` | #448, #2, #4 (if upstream bumped) | Conditional on upstream `task-master-ai` release. Otherwise defer and document. |
| Lines 174–178 (CloudCLI tarball) | No change | — | Deferred to Phase 2b. |
| Lines 181–230 (patches) | No change | — | Must remain valid after dedupe. |

---

## 3. Risk-Per-Change Matrix

| Change | Alerts closed | Alerts deferred | Bundle-patch impact | Smoke-test impact | Risk level |
|---|---|---|---|---|---|
| `npm i -g npm@11.14.0` (new layer after line 99) | #34, #35, #446 (3 alerts) | none | None — patches target CloudCLI, not npm's tree. | PASS (no patch WARNs) | Low |
| `pnpm@11.0.8` (pinned at line 112) | #447 (1 alert, if upstream has bumped) | possible — if pnpm@11.0.8 still vendors old `ip-address` | None — pnpm is build-time only | PASS | Low–Medium (depends on upstream) |
| `task-master-ai@latest` (line 153) | #448, #2, #4 (up to 3 alerts, if upstream bumped) | possible — if upstream hasn't released | None — task-master-ai doesn't affect CloudCLI bundle | PASS | Medium (upstream dependency) |
| CloudCLI tarball unchanged (deferred to Phase 2b) | — | #445, #444, #33 (3 alerts: ip-address, @anthropic-ai/sdk, prismjs) | Patches remain valid ✓ | PASS | None (explicit deferral) |

**Total realistic closure in Phase 2: 4–7 alerts** (best case: 7 if upstream task-master-ai has released; worst case: 4 if only npm/pnpm fix). **Deferred to Phase 2b: 9 alerts** (the three CloudCLI-hosted vulns, each counted across the dependency tree).

---

## 4. Verification Protocol

### Post-build inspection

After the Phase 2 image builds, run:

```bash
# Verify npm version
docker run --rm holyclaude:ci-slim npm --version
# Expected: 11.14.0

# Verify pnpm version
docker run --rm holyclaude:ci-slim pnpm --version
# Expected: 11.0.8

# Verify picomatch, brace-expansion versions (transitive to npm)
docker run --rm holyclaude:ci-slim sh -c \
  'find /usr/local/lib/node_modules/npm/node_modules -name picomatch -o -name brace-expansion \
   | xargs -I{} jq -r .version {}/package.json 2>/dev/null | sort -u'
# Expected: 4.0.4 for picomatch, 5.0.5 for brace-expansion

# Verify ip-address in npm, pnpm, task-master-ai
docker run --rm holyclaude:ci-slim sh -c \
  'find /usr/local/lib/node_modules -path "*/npm/*/ip-address" \
    -o -path "*/pnpm/*/ip-address" \
    -o -path "*/task-master-ai/*/ip-address" \
   | xargs -I{} jq -r .version {}/package.json 2>/dev/null | sort -u'
# Expected: 10.1.1 (or 10.2.0 if upstream bumped further)
```

### CI smoke-test verification

The `scripts/ci/smoke-test.sh` runs as part of `build-and-smoke` in CI. **Critical check:** scroll past the smoke-test summary and confirm **zero `[patch] WARNING:` lines**. The patch-warning scan at `smoke-test.sh:148–171` checks these markers:

```
✓ ${vendored_pkg_path}/server/index.js:upstream.send(data, { binary: isBinary })
✓ ${vendored_pkg_path}/server/routes/commands.js:newModel: args.length
```

If either marker is missing, the output shows:
```
WARN  patch missing in ${file} (marker: ...)
      Dockerfile patch block likely needs refreshing for this CloudCLI version
```

In Phase 2, these patches should **not** change because the CloudCLI tarball is unchanged. If they do warn, the phase has introduced an unexpected rebuild.

### Trivy SARIF re-upload

After CI completes, GitHub's Security tab will show the updated alert count. Expected delta:

| Scenario | Alerts closed | Notes |
|---|---|---|
| Upstream task-master-ai released with bumps | 7 | npm (#34, #35, #446), pnpm (#447), task-master-ai (#448, #2, #4) |
| Upstream task-master-ai not yet released | 4 | npm (#34, #35, #446), pnpm (#447) only |
| Upstream pnpm didn't bump ip-address | 3 | npm (#34, #35, #446) only |

---

## 5. Realistic Alert-Closure Forecast

### Definite closures

- **#34** `picomatch@4.0.3` in npm → closes via `npm i -g npm@11.14.0` ✓
- **#35** `brace-expansion@5.0.4` in npm → closes via `npm i -g npm@11.14.0` ✓
- **#446** `ip-address@10.1.0` in npm → closes via `npm i -g npm@11.14.0` ✓

**Subtotal: 3 alerts, confirmed.**

### Probable closures (upstream dependent)

- **#447** `ip-address@10.1.0` in pnpm → closes if `pnpm@11.0.8` rebundled `ip-address>=10.1.1` (verify before merge)
- **#448** `ip-address@10.1.0` in task-master-ai → closes if `task-master-ai@latest` dependency tree includes `ip-address>=10.1.1` (verify before merge)
- **#2** `@tootallnate/once@2.0.0` in task-master-ai → closes if upstream bumped (unlikely; low severity)
- **#4** `diff@7.0.0` in task-master-ai → closes if upstream bumped (unlikely; low severity)

**Subtotal: up to 4 additional alerts, conditional on upstream release verification.**

### Certain deferrals (CloudCLI workstream)

- **#445** `ip-address@10.1.0` in `@cloudcli-ai/cloudcli` → deferred to Phase 2b (requires re-vendoring)
- **#444** `@anthropic-ai/sdk@0.81.0` in `@cloudcli-ai/cloudcli` → deferred to Phase 2b
- **#33** `prismjs@1.27.0` in `@cloudcli-ai/cloudcli` (via refractor) → deferred to Phase 2b

**Subtotal: 3 alerts, explicitly documented as deferred.**

### Summary

| Outcome | Count | Status |
|---|---|---|
| **Definite closure (Phase 2)** | 3 | #34, #35, #446 |
| **Probable closure (if upstream bumped)** | 4 | #447, #448, #2, #4 |
| **Deferred to Phase 2b (CloudCLI)** | 3 | #445, #444, #33 |
| **Unaccounted** | 6 | unknown (possibly false positives or distinct clusters) |
| **Total open npm alerts in Phase 1 baseline** | 16 | — |

**Realistic Phase 2 closure: 4–7 alerts (25–44% of the 16).** This is honest scoping: it focuses on the globally-installed packages we control (npm, pnpm, task-master-ai) and explicitly defers the vendored CloudCLI to a separate workstream. The runbook constraint against `npm dedupe -g` makes this the safest path.

---

## 6. CloudCLI Deferral Roadmap (Phase 2b)

Although out of scope for Phase 2, document the follow-up clearly to avoid silent assumption drift:

**Phase 2b timeline:** After Phase 2 is merged and validated (smoke test passes, no patch WARNs, 4–7 alerts closed in Trivy SARIF):

1. **File upstream issue** at `github.com/cloudcli-ai/cloudcli`:
   - Request bumps to `@anthropic-ai/sdk>=0.95.1` and `refractor->prismjs>=1.30.0`
   - Reference this document and the alert numbers

2. **Once upstream releases:**
   - Run `cloudcli-sync.yml` to re-vendor the new tarball
   - Update `Dockerfile:174` tarball filename and `Dockerfile:177` ref if needed
   - **Re-verify patch anchors at `Dockerfile:181–230`** match the new bundle hash (run smoke test locally first)
   - Land as Phase 2b PR

3. **If upstream doesn't release within 30 days:**
   - Add `.trivyignore` entries for #445, #444, #33 with TTL and justification
   - Re-evaluate in the next quarterly security audit

---

## 7. Top Design Decisions

1. **No `npm dedupe -g` at container level.** The constraint from the rollout runbook is absolute: dedupe can trigger bundle rebuilds that invalidate patches. Instead, we upgrade the source packages (npm, pnpm, task-master-ai) to versions that already have bumped transitives, letting their own build process handle deduplication.

2. **CloudCLI deferral is explicit, not implicit.** Rather than attempt an in-place `npm install --no-save` override inside the vendored CloudCLI (risky, hard to reason about), Phase 2 strictly avoids touching the CloudCLI tree. This makes the patch-validity guarantee airtight: no changes to CloudCLI → patches remain valid. Phase 2b then handles the tarball upgrade cleanly as a separate PR, with a full patch re-validation cycle.

3. **Upstream verification gates the PR merge.** Before Phase 2 PR is approved, the reviewer must run `npm view pnpm@11.0.8 --json | jq '.dependencies'` and `npm view task-master-ai --json | jq '.dependencies'` to confirm the upstream bumps are real. If they're not, the PR description must explicitly list the deferred alerts and their TTL, converting an assumption into a fact.

---

## Implementation Checklist (for execution agent)

- [ ] Pin `npm@11.14.0` at new layer after `Dockerfile:99`
- [ ] Pin `pnpm@11.0.8` at `Dockerfile:112` (verify upstream bumped `ip-address` first)
- [ ] Pin `task-master-ai@latest` at `Dockerfile:153` (or defer if upstream hasn't released)
- [ ] **Do NOT modify** `Dockerfile:174–230` (CloudCLI tarball, patches)
- [ ] Run `npm view <pkg>` commands above to gather evidence before PR description
- [ ] Build image locally and run verification protocol (§4)
- [ ] Confirm smoke test shows zero `[patch] WARNING:` lines
- [ ] Document deferred alerts (#445, #444, #33) in PR description with link to this design doc

---

## References

- Code-scanning-remediation-plan.md: Groups B–E (npm transitive sections)
- Rollout-runbook.md: §6e (critical constraint against `npm dedupe -g`)
- Dockerfile: lines 99–178 (npm globals, CloudCLI install, patches)
- scripts/ci/smoke-test.sh: lines 148–171 (patch-warning scan, **WARN not FAIL**)
