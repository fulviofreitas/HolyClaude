# Branch protection

This file documents the protection rules currently configured on `master`.
They were applied via `gh api` on 2026-04-30 (see `Apply` section below for
the exact command if they ever need to be reapplied).

> Solo-maintainer note: `required_approving_review_count` is **0**, not 1
> as a multi-developer fork would use. With `0` and `require_code_owner_reviews=true`
> the maintainer can still self-merge their own PRs while preserving CODEOWNERS
> visibility on what changed.

## Required settings on `master`

| Setting                               | Value                                            |
|---------------------------------------|--------------------------------------------------|
| Require pull request before merging   | yes                                              |
| Required reviewers                    | 0 (solo-maintainer)                              |
| Dismiss stale reviews on push         | yes                                              |
| Require review from Code Owners       | yes                                              |
| Require status checks                 | yes — `Lint (...)`, `Build & smoke (slim)`       |
| Require branches to be up to date     | yes                                              |
| Require linear history                | no (we use merge commits for upstream syncs)     |
| Allow force pushes                    | no                                               |
| Allow deletions                       | no                                               |

## Apply with gh CLI

The `-F` flag for `gh api` doesn't handle nested objects cleanly — use
`--input -` with a JSON payload instead:

```bash
cat <<'JSON' | gh api -X PUT repos/fulviofreitas/HolyClaude/branches/master/protection --input -
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Lint (hadolint + shellcheck + yamllint)",
      "Build & smoke (slim)"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

The status-check context names are the `name:` strings used by the jobs in
`.github/workflows/ci.yml` — if those job names change, the protection
rules need to be re-applied with the new names.
