---
generated: 2026-05-07
author: implementation-strategist
sources:
  - docs/security/code-scanning-remediation-plan.md
  - Dockerfile
  - .github/workflows/ci.yml
  - .github/workflows/release.yml
  - .github/workflows/renovate.yml
  - .github/renovate.json5
  - .github/branch-protection.md
worktrees:
  phase1: .claude/worktrees/agent-a98d3b11f6077429a (security/phase1-imagemagick-deb12u9, HEAD 1c4027b)
  phase3: .claude/worktrees/agent-af9addfcad575a0ca (security/phase3-codeql-trivy-prep, HEAD bfad907)
  phase2: not started
---

# Rollout Runbook — Code-Scanning Remediation

This runbook turns the master plan in `docs/security/code-scanning-remediation-plan.md`
into a concrete, dated sequencing decision. Three branches exist in worktrees
today; one of them (`security/phase2-*`) has not been opened yet. The hard
constraint from the maintainer is: **the image must keep building, the smoke
test must keep passing, and `release.yml` (gated by `workflow_run: ci`)
must not ship an incomplete remediation as part of a `master` push**.

## 1. Sequencing decision matrix

| Phase | Branch | Files touched (actual diff vs master) | CI jobs exercised | Depends on | Rollback (one command) |
|---|---|---|---|---|---|
| **1** ImageMagick deb12u9 | `security/phase1-imagemagick-deb12u9` (HEAD `1c4027b`) | `Dockerfile` only — inserts a new `RUN apt-get … --only-upgrade` block between current lines 63 and 65 (after the main install RUN, before `chmod u+s /usr/bin/bwrap`). +14 / -0. | `commitlint`, `lint` (hadolint runs on Dockerfile), `build-and-smoke` (rebuild + smoke + advisory Trivy SARIF), `ci-success` | none | `git revert 1c4027b` (after merge: revert the merge SHA on master) |
| **3** CodeQL workflow | `security/phase3-codeql-trivy-prep` (HEAD `bfad907`) | `.github/workflows/codeql.yml` only (new file). +69 / -0. **No `ci.yml` change** — the brief mentioned a "TODO above the Trivy gate" that is **not present** in the actual commit. See "Plan corrections" below. | `commitlint`, `lint` (yamllint runs on `.github/`), `build-and-smoke`, `ci-success`. After merge: a new top-level `codeql` workflow run also fires on push (separate from `ci`). | none on Phase 1 in code; sequenced after Phase 1 only to keep the Security-tab alert delta clean (Phase 1 closes 40 alerts, Phase 3 opens an unknown number of CodeQL alerts — separating the two makes the audit trail readable). | `git revert <merge-sha>` — deletes `codeql.yml`, alerts auto-close, no other side effects. |
| **2** npm-global hardening | `security/phase2-npm-globals` (not yet opened) | `Dockerfile` (Group B/C/D/E from plan): adds `RUN npm i -g npm@latest pnpm@latest` after current line 96 / line 116, plus a CloudCLI in-tree dedupe block after line 178, plus `task-master-ai@latest` after line 153. Estimated +20 / -0. | Same as Phase 1 (Dockerfile change → full `build-and-smoke` cycle). | **Phase 1 must be merged first** because both edit `Dockerfile`. Trying to land them concurrently invites a textual conflict in the same RUN-block region; even if conflicts are clean, the smoke test verifies the *combined* image — easier to debug if Phase 1 has already baked. | `git revert <merge-sha>` — restores the older `npm`/`pnpm`/`task-master-ai`. Image rebuild required to take effect; existing `holyclaude:slim` published tags are unaffected. |

CI jobs reference: `.github/workflows/ci.yml:41-90` (commitlint), `:61-97` (lint), `:102-176` (build-and-smoke), `:186-212` (ci-success aggregator).

## 2. Merge order

**Recommended order: Phase 1 → Phase 3 → Phase 2.** This differs slightly from
the plan's PR numbering (PR-1 ImageMagick, PR-2 npm-bundle, PR-7 CodeQL) but
matches the *worktree state* the agents have produced today. Justification:

1. **Phase 1 first** — it carries the highest signal:value ratio (40 alerts
   closed including all 5 criticals) and the smallest blast radius (one
   `RUN` block, no behavioral change to runtime). Smoke test gives
   definitive go/no-go.
2. **Phase 3 second** — a workflow-only change. Zero risk to the image. Lands
   the SAST coverage early so by the time Phase 2 ships, CodeQL is already
   producing baseline alerts that we can compare against.
3. **Phase 2 third** — touches the same `Dockerfile` region as Phase 1 (Groups
   B/C/D/E from the plan all stack new `RUN` blocks into the layer chain).
   Sequencing it last means the in-place CloudCLI dedupe runs against a
   `Dockerfile` whose ImageMagick fix has already been validated, so any
   smoke-test regression is unambiguously a Phase 2 issue.

### Success criteria gating each step

Before merging Phase 1:
- `commitlint`, `lint`, `build-and-smoke (slim)`, `ci-success` all green on the PR.
- `build-and-smoke` logs show the new `apt-get --only-upgrade` RUN block executed and reported `Setting up imagemagick (8:6.9.11.60+dfsg-1.6+deb12u9) …` (or newer) — verifiable from the buildx step output.
- Smoke test PASS line at end of `scripts/ci/smoke-test.sh`: `PASS: all smoke checks ok`.
- Trivy SARIF re-uploaded; on the PR's Security tab, the imagemagick cluster (alerts #399–#443) shows as **fixed in PR**.

Before proceeding to Phase 3:
- Phase 1 merged to `master`.
- `master` `🧪 ci` run green → `🚀 release` `workflow_run` either no-op (no semantic-release-worthy change since last tag) or produces a tag whose smoke output is identical to the PR's.
- `gh api repos/fulviofreitas/HolyClaude/code-scanning/alerts?state=open --paginate | jq '[.[] | select(.rule.id | startswith("CVE-2026-25971") or startswith("CVE-2026-339") or startswith("CVE-2026-338") or startswith("CVE-2026-403"))] | length'` returns `0` (or close to 0 — should be the 40-alert imagemagick cluster).

Before proceeding to Phase 2:
- Phase 3 merged to `master`.
- A first `codeql` run completed on master (success or failure, but ran). This produces the SAST baseline that the Phase 2 PR's CodeQL run can be compared against.
- New baseline alert count fetched and recorded in the Phase 2 PR description.

## 3. Blast-radius analysis

### Phase 1 — ImageMagick `apt --only-upgrade`
- **Runtime behaviour change:** ImageMagick versions advance from `8:6.9.11.60+dfsg-1.6+deb12u8` → `+deb12u9`. Same major/minor — Debian point-release. ABI is preserved by Debian policy. Sharp/Pillow/Playwright pipelines that shell out to `convert`/`identify` see no API change.
- **Affected:**
  - **Image consumers** (downstream repos pulling `ghcr.io/.../holyclaude:slim`): unchanged binary surface, smaller CVE surface. No action needed downstream.
  - **`release.yml`:** if Phase 1 is the only change since the last tag, semantic-release computes a `fix:` patch bump from the conventional commit `fix(security): pin imagemagick-6 cluster to deb12u9` (commit `1c4027b`). A new `vX.Y.Z+1` tag fires `docker-publish.yml`. **Expected and acceptable** — this is a real fix worth shipping.
  - **Renovate:** unaffected. The custom regex manager only watches `S6_OVERLAY_VERSION` and the `node` base image.
- **Bad-rollout shape:** the `apt-get update` inside the new RUN block fails to reach the Debian Security mirror (transient), build fails, no image published. Re-run CI. Or: the Debian Security pocket is *ahead* of `deb12u9` and pulls a newer point release whose ABI broke a downstream — extremely unlikely for a Debian point release, smoke test catches it via the chromium/Pillow/playwright probes inside `scripts/ci/smoke-test.sh:95-101` and the CloudCLI HTTP probe at `:111-124`.

### Phase 3 — CodeQL workflow
- **Runtime behaviour change:** none. Workflow-only.
- **Affected:**
  - **`release.yml`:** **none** — release.yml's `workflow_run` filter is `workflows: ["🧪 ci"]` (`release.yml:19`). It does **not** trigger on the new `🛡️ codeql` workflow's completion. Confirmed.
  - **GitHub Actions minutes:** new ~5–10 min job per PR + weekly cron. Well within free tier.
  - **Security tab:** new SARIF category `python` appears. May produce new alerts on `scripts/notify.py` — these are *new findings*, not regressions. Plan to triage them in a follow-up issue, not block Phase 3 merge on them (the workflow is advisory; not added to `ci-success`'s `needs:` list).
- **Bad-rollout shape:** CodeQL fails to compile/extract Python (very rare). Workflow goes red on master but does not break `ci-success` because they're independent workflows.

### Phase 2 — npm globals + CloudCLI in-tree dedupe
- **Runtime behaviour change:** real. `npm` and `pnpm` advance major/minor; `task-master-ai` advances; CloudCLI's `node_modules/ip-address`, `node_modules/@anthropic-ai/sdk`, `node_modules/refractor/node_modules/prismjs` are bumped *in place inside the published image*.
- **Affected:**
  - **CloudCLI patches at `Dockerfile:181-230`:** the patches target `dist/assets/index-DqMVUeZS.js` (a compiled bundle hash). The `npm dedupe`/`npm install --no-save` step proposed in the plan modifies `node_modules` siblings, not `dist/assets/`, so the bundle hash should be unchanged and patches should still apply. **Risk:** if `npm dedupe` triggers a package's postinstall that rebuilds the bundle, the hash changes and every patch grep at `:182, :192, :198, :206, :213, :220, :227` fails the anchor check, prints `[patch] WARNING: ...`, and the smoke test's "Patch-warning scan (advisory)" at `smoke-test.sh:148-171` emits WARN (not FAIL). User-visible impact: model selector and websocket binary frames break. **Verify before merge.**
  - **`release.yml`:** semantic-release fires on a `fix(security):` or `chore(deps):` commit and ships a new tag. Expected.
  - **Image consumers:** Cloudflare Tunnel / OAuth runtime is unaffected. Anyone pinned to an explicit `holyclaude:vX.Y.Z` tag is unaffected; only `:latest` / floating-tag consumers see the change.
  - **Renovate:** Phase 2 introduces `npm i -g npm@latest pnpm@latest` literal `@latest` strings. Renovate's `customManagers` (`renovate.json5:53-67`) doesn't currently match these — fine, they remain unmanaged. **If a future Phase 3 hardening PR adds a `customManagers` regex for `npm i -g <pkg>` lines, the Phase-2 `@latest` strings will start producing pinned PRs the day after.**
- **Bad-rollout shape:** in-place `npm install --no-save` inside `/usr/local/lib/node_modules/@cloudcli-ai/cloudcli` produces a tree that fails the smoke test's served-version assertion at `smoke-test.sh:126-146` because `package.json` rewrite changed the recorded version. Mitigation: use `--no-save` strictly, never `npm install <pkg>@<v>` without it.

## 4. Pre-merge gate checklist (per PR)

The reviewer (or auto-merger, after `auto-merge.yml` is permitted) must verify
**all** of the following in the PR's Files-changed and Checks tabs:

- [ ] `📝 Commit lint` green. (Required for semantic-release downstream.)
- [ ] `🧹 Lint (hadolint + shellcheck + yamllint)` green. New `RUN` blocks must not introduce new hadolint errors. The current ignore list at `ci.yml:78` is `DL3002,DL3008,DL3013,DL3015,DL3018,DL4006,SC2086,DL3059,DL3025`; do not silently expand it.
- [ ] `🐳 Build & smoke (slim)` green. In the run logs, search for:
  - `Setting up imagemagick* … +deb12u9` (Phase 1 only)
  - `PASS: all smoke checks ok` (every PR)
  - No new `[patch] WARNING:` lines (Phase 2 in particular)
- [ ] `✅ ci-success` green. This is the aggregator at `ci.yml:186-212`.
- [ ] **Trivy SARIF re-uploaded.** Open the Security → Code scanning tab on the PR; the alert delta should match the phase's expected close set:
  - Phase 1: 40 alerts closed (#399–#443)
  - Phase 3: 0 closed; possibly N new CodeQL findings
  - Phase 2: at least #34, #35, #3, #446, #447, #445, #444, #33, #448 closed
- [ ] **Diff scope check.** Reviewer rejects the PR if it touches files outside its declared scope:
  - Phase 1 PR: `Dockerfile` only.
  - Phase 3 PR: `.github/workflows/codeql.yml` only.
  - Phase 2 PR: `Dockerfile` only.
- [ ] **No new shellcheck/yamllint warnings.** `ci.yml:80-97` runs both; new `.github/workflows/*.yml` (Phase 3) must pass yamllint with `line-length: 200`.
- [ ] **CHANGELOG / release-notes preview.** Run `gh workflow run release.yml -f dry_run=true` against the PR base after the build is green; confirm the proposed release type (`patch` for `fix:`, none for `docs:`) matches expectations.

## 5. Rollback playbook

All three phases are pure additions to `Dockerfile` or `.github/workflows/`. No
DB migrations, no schema changes, no externally-visible API. **Every rollback
is a `git revert`.** The image must rebuild before the rollback takes effect
in any consumer; older tags (`:vX.Y.Z`) are unaffected by definition.

| Phase | Command | Expected side effects |
|---|---|---|
| Phase 1 | `git revert <merge-commit-sha-of-phase1>` then `git push origin master` | The 40 imagemagick alerts re-open within ~10 min of the next CI Trivy upload. CI baseline returns to pre-rollout. **No data loss.** Existing published images (e.g. `:v1.x.y`) keep their fix; only `:latest` and any newly-built tag regress. |
| Phase 3 | `git revert <merge-commit-sha-of-phase3>` | `codeql.yml` deleted; the `🛡️ codeql` workflow stops running on PRs/pushes. SARIF previously uploaded remains in the Security tab as historical data. **No CI breakage.** |
| Phase 2 | `git revert <merge-commit-sha-of-phase2>` | `npm` / `pnpm` / `task-master-ai` global versions return to whatever was bundled in `node:24-bookworm-slim`. Group B/C/D/E alerts re-open. CloudCLI in-tree dedupe is undone — patches at `Dockerfile:181-230` continue to apply because they're version-independent string-anchor patches. **No data loss.** |

If a phase is merged and the *next* phase has already begun, revert the
chronologically-later phase first. Reverting Phase 1 while Phase 2 is on
master would leave Phase 2's `Dockerfile` deltas pointing at the old
ImageMagick install block — possibly fine, possibly a textual conflict;
revert in LIFO order to be safe.

## 6. Cross-cutting risks

### 6a. Docker layer-cache staleness (Phase 1's biggest hidden risk)

`build-and-smoke` uses BuildKit GHA cache (`ci.yml:139-142`):
```
cache-from:
  type=gha,scope=ci-slim
  type=gha,scope=master-slim-amd64
cache-to:  type=gha,mode=max,scope=ci-slim
```

The new `RUN apt-get update && apt-get install --only-upgrade …` block sits
between the existing apt RUN at `Dockerfile:40-63` and `chmod u+s
/usr/bin/bwrap` at `:66`. BuildKit will execute it because the layer hash
changes, but the **`apt-get update` inside the new block can pull a stale
package index** if the underlying base layer's `/var/lib/apt/lists/` is
restored from cache — except the prior step at `:63` ends with `rm -rf
/var/lib/apt/lists/*`, which means the new RUN starts with an empty list
dir and therefore *must* re-run `apt-get update` against the live mirror.
This is correct by construction, but it depends on the deletion at `:63`
actually firing before the cache snapshot. **Recommended verification on
first run:** in the Phase-1 PR's `build-and-smoke` log, confirm the line
`Get:1 http://deb.debian.org/debian-security bookworm-security/main amd64
Packages` appears inside the *new* RUN step, not just the original. If it
doesn't, force `--no-cache` for the first build by temporarily commenting
out `cache-from` in `ci.yml`.

For Phase 2: same caveat. `npm i -g npm@latest` will hit the npm registry
fresh because npm doesn't cache by default in CI. Lower risk.

### 6b. `release.yml` race during merge cluster

`release.yml:18-21` triggers on `workflow_run: 🧪 ci, conclusion=success,
branches=[master]`. Concrete risk:

1. Phase 1 merges to master → ci runs → success → release fires → tag `vX.Y.Z+1` → docker-publish ships an image with **only** the imagemagick fix.
2. While Phase 1's release is still running, Phase 3 merges → ci runs → success → release fires (concurrency group `release` cancel-in-progress=false at `release.yml:33-35`, so it queues, doesn't drop) → tag `vX.Y.Z+2`.

This is **intended behaviour** — each phase ships as its own tagged release.
There is no scenario where a half-applied remediation ships, because each
phase's `Dockerfile` is internally consistent on its own.

**The actual risk**: if you merge Phase 1, observe ci passing, then immediately
merge Phase 2 *before* Phase 1's release pipeline completes, semantic-release
will see *two* `fix:` commits and bump twice in close succession, but
`docker-publish.yml` runs serially per-tag (concurrency in the publish
workflow, not asserted here — verify via `gh workflow view docker-publish.yml`
before clustering merges). Recommendation: **wait for `🚀 release` to reach
`completed` between phase merges**. Adds ~10 min between merges; cheap.

### 6c. Branch protection — current state vs. documented state

`.github/branch-protection.md:14-20` documents that `master` requires status
checks `Lint (...)` and `Build & smoke (slim)`. **The live API disagrees:**
`gh api repos/fulviofreitas/HolyClaude/branches/master/protection/required_status_checks`
returns `404 Required status checks not enabled`. (The plan called this out
at `code-scanning-remediation-plan.md:303` and `:309`.)

Operational consequence: today **nothing prevents a red ci from being merged
to master**. The PR will show red checks; the `Merge` button is still
available. Self-discipline only. For this rollout that means the reviewer
must manually verify the green-check matrix in §4 above before clicking
Merge.

**Recommended pre-rollout action**: re-apply the protection rules using the
`gh api` payload at `.github/branch-protection.md:31-51` *before* merging
Phase 1. This brings live state into agreement with documentation and
makes the §4 checklist enforced automatically. Treat it as a separate
zero-risk PR (it touches no files; it's a `gh api` call).

### 6d. Renovate concurrent-PR conflicts

Live state today (`gh pr list --state open`):

- **#41** `apefrei:feature/install-ui-from-npm` — third-party draft,
  inactive since 2026-04-30. Touches the CloudCLI install path. **Conflict
  surface with Phase 2.** Action: leave open, ignore; if the maintainer
  ever picks it up, rebase it after Phase 2 lands.
- No open Renovate PRs for `Dockerfile` lines that Phase 1 / Phase 2 modify.
  Renovate's narrow scope (`renovate.json5:41` whitelist =
  `["github-actions", "dockerfile", "custom.regex"]`, `dockerfile` manager
  matches only the `FROM node:…` line at `Dockerfile:10`) means it cannot
  produce conflicts in lines 40-178. Confirmed.
- Local branches `deps/node-24.x` and `renovate/node-24` exist but no
  corresponding open PR — likely already merged (commit `0f0cf6b chore(deps):
  update node to v24.15.0`). No conflict.

### 6e. `auto-merge.yml`

Recently added (`732e0ab`). Risk: a green-check Phase 2 PR auto-merges before
the reviewer has a chance to verify the §4 checklist (specifically the
"no new `[patch] WARNING:` lines" item, which is **WARN**, not FAIL, in
the smoke test). Recommendation: **mark each phase PR with `do-not-merge`
or set `auto-merge: false` until the reviewer has eyeballed the build log**.

## 7. Phase 2 readiness checklist (forward-looking)

Before the dependency-manager agent opens `security/phase2-npm-globals`:

- [ ] **Phase 1 merged to master** and the resulting release tag's
      `docker-publish.yml` run is green (image actually published to GHCR).
- [ ] **Trivy alerts re-baselined.** Run:
      ```
      gh api 'repos/fulviofreitas/HolyClaude/code-scanning/alerts?state=open&per_page=100' --paginate \
        | jq '[.[] | {n:.number, id:.rule.id, sev:(.rule.security_severity_level // .rule.severity), pkg:(.most_recent_instance.message.text | capture("Package: (?<p>[^ ]+)").p)}]' \
        > /tmp/post-phase1-alerts.json
      ```
      Confirm: `jq '[.[] | select(.pkg | test("imagemagick|libmagick"))] | length'` returns `0`.
- [ ] **Phase 3 merged to master** and the first `codeql` run completed
      (success or failure either way; just need the workflow to have actually
      run so the alert baseline exists).
- [ ] **Globals to bump** (in the order they appear in `Dockerfile`):
  | Line(s) | Current | Bump strategy | Upstream coordination needed |
  |---|---|---|---|
  | `:110-116` | `npm` (whatever node:24-bookworm-slim ships, typically `npm@10.x`) | `RUN npm i -g npm@latest` *before* the existing `npm i -g …` block (insert after line 96, after the user rename). Closes #34, #35, #3, #446. | none |
  | `:110-116` | `pnpm@<bundled>` | bumped by re-running `npm i -g pnpm@latest` (already in the global list — explicit `@latest` makes the upgrade explicit). Closes #447. | none |
  | `:153` | `task-master-ai@<latest-at-build>` | `RUN npm i -g task-master-ai@latest` re-run, plus `npm dedupe` against the global tree. Closes #448 if upstream bumped `ip-address`; #2, #4 if upstream dropped/bumped `@tootallnate/once` and `diff`. | **Yes** — `task-master-ai` upstream may not have shipped a release with the new transitives. **Verify first**: `npm view task-master-ai@latest dependencies`. If still pinned to the old `ip-address`, dismiss #448, #2, #4 as `won't_fix` per plan §"False Positives". |
  | `:174-178` | `@cloudcli-ai/cloudcli@1.31.5` (vendored tarball) | **Do not bump the tarball here** — that's `cloudcli-sync.yml`'s job. Instead add a post-install RUN block after `:178` that does `npm install ip-address@^10.1.1 @anthropic-ai/sdk@^0.91.1 --no-save && npm dedupe` inside `/usr/local/lib/node_modules/@cloudcli-ai/cloudcli`. For prismjs, do the same inside `…/node_modules/refractor`. Closes #33, #444, #445. | **Yes** — file an issue at `cloudcli-ai/cloudcli` requesting upstream bumps so the in-place dedupe can be removed when `cloudcli-sync.yml` next pulls. |
- [ ] **Patch verification plan**: after the in-place CloudCLI dedupe, the
      smoke test at `scripts/ci/smoke-test.sh:158-171` will WARN (not FAIL)
      if any of the seven patch anchors at `Dockerfile:181-230` no longer
      match. The reviewer **must scroll past the smoke summary** and
      confirm zero `[patch] WARNING:` lines from the build log.
- [ ] **`vendor/artifacts/` left alone**. Renovate's `ignorePaths`
      (`renovate.json5:45-47`) and the cloudcli-sync workflow both expect
      the tarball to be untouched. Phase 2 is *runtime* dedupe inside
      `node_modules`, not a tarball replacement.

## Plan corrections

Items where the master plan or the orchestrator brief disagrees with the
live repository state. Flagging, not amending.

1. **Brief vs. actual Phase 3 commit.** The brief stated Phase 3 "adds a
   CodeQL workflow + a TODO above the Trivy gate." The actual commit
   (`bfad907`) only adds `.github/workflows/codeql.yml`. There is **no**
   change to `.github/workflows/ci.yml` and no TODO comment was added
   above the Trivy step at `ci.yml:147`. Either the brief was wrong or
   the security-engineer agent dropped that part of its task. **Action:**
   verify with the security-engineer agent before considering Phase 3
   complete; if the TODO is wanted, it's a one-line `ci.yml` change that
   can be tucked into the same PR.
2. **Branch-protection drift.** `.github/branch-protection.md:14-25`
   documents required status checks. The live API
   (`gh api repos/fulviofreitas/HolyClaude/branches/master/protection/required_status_checks`)
   returns `404 Required status checks not enabled`. The plan flagged this
   at `code-scanning-remediation-plan.md:303` ("This is the single biggest
   governance gap"). **Action:** apply the documented protection before
   the rollout starts (zero-risk `gh api` call, no commit needed). This
   converts the §4 checklist from advisory to enforced.
3. **Plan §"CI Gating Plan" item 1** suggests `vuln-type: os` to scope the
   blocking pass to Debian packages only. Trivy's `aquasecurity/trivy-action`
   accepts `vuln-type` as `os,library` (default) — confirm the action
   version pinned at `ci.yml:149` (`@master`, unpinned) actually supports
   this option before relying on it in Phase 3 hardening PRs. Pinning the
   action to a digest is independently a good idea for supply-chain hygiene.
4. **Plan §"Group B"** says "insert `RUN npm i -g npm@latest` after
   `Dockerfile:96`." Line 96 in the current file is the start of the
   `usermod -l claude …` RUN block (lines 96-99). Inserting *after* line 99
   is fine; inserting *between* lines 96 and 97 would split the RUN.
   The Phase-2 implementer should treat the plan's line numbers as
   approximate and re-anchor against the live file.
