# Branch protection (intent)

This file documents the protection rules that should be configured on
`master`. Configure via GitHub UI or `gh api` (script below).

## Required settings on `master`

| Setting                               | Value                                            |
|---------------------------------------|--------------------------------------------------|
| Require pull request before merging   | yes                                              |
| Required reviewers                    | 1                                                |
| Dismiss stale reviews on push         | yes                                              |
| Require review from Code Owners       | yes                                              |
| Require status checks                 | yes — `lint`, `build-and-smoke (full)`, `build-and-smoke (slim)` |
| Require branches to be up to date     | yes                                              |
| Require linear history                | no (we use merge commits for upstream syncs)     |
| Allow force pushes                    | no                                               |
| Allow deletions                       | no                                               |

## Apply with gh CLI

```bash
gh api -X PUT \
  repos/fulviofreitas/HolyClaude/branches/master/protection \
  -F required_status_checks.strict=true \
  -F 'required_status_checks.contexts[]=lint' \
  -F 'required_status_checks.contexts[]=build-and-smoke (full)' \
  -F 'required_status_checks.contexts[]=build-and-smoke (slim)' \
  -F enforce_admins=false \
  -F required_pull_request_reviews.required_approving_review_count=1 \
  -F required_pull_request_reviews.dismiss_stale_reviews=true \
  -F required_pull_request_reviews.require_code_owner_reviews=true \
  -F restrictions=
```

> Apply manually after the first successful CI run, so the check names
> exist as required-context options in the GitHub UI.
