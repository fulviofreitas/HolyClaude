#!/usr/bin/env bash
# =============================================================================
# CloudCLI patch-anchor drift report
# =============================================================================
# Diffs the minified-bundle and server-source anchors that our Dockerfile
# patches and upstream-merged-fix canaries rely on, comparing two vendored
# CloudCLI tarballs. Emits a Markdown table for embedding in the cloudcli-sync
# PR body so a reviewer can tell at a glance whether a bump rotated minified
# symbols (anchors changed -> patches need refreshing) or just rotated the
# bundle filename (anchors unchanged -> no work needed).
#
# Catalog rows:
#   * ACTIVE patches: anchor present = patch will apply
#   * CANARY (retired): anchor present = upstream still handles natively;
#     if it disappears we need to resurrect the corresponding fork patch
#
# Usage:
#   cloudcli-anchor-report.sh OLD_TGZ NEW_TGZ
#
# Emits to stdout. Exits 0 even on partial failures (workflow uses this
# advisorily; the actual patch-rot signal is the bundle hash diff).
# =============================================================================
set -euo pipefail

OLD_TGZ="${1:?usage: cloudcli-anchor-report.sh OLD_TGZ NEW_TGZ}"
NEW_TGZ="${2:?usage: cloudcli-anchor-report.sh OLD_TGZ NEW_TGZ}"

old_root="$(mktemp -d)"
new_root="$(mktemp -d)"
trap 'rm -rf "$old_root" "$new_root"' EXIT

tar -xzf "$OLD_TGZ" -C "$old_root"
tar -xzf "$NEW_TGZ" -C "$new_root"

# Catalog. Each row: name|file-glob (relative to tar root)|grep -oE regex|description
# Regexes are loose enough to survive identifier rotation but tight enough to
# pin the right region. Shell strips one level of backslashes -- use `\$` for
# literal `$` inside character classes.
anchors=(
  "shell-scroll|package/dist/assets/index-*.js|const [A-Za-z_\$]+=\(\)=>\{[A-Za-z_\$]+\.current\?\.focus\(\)\}|ACTIVE -- P1: shell focus callback (issue #35)"
  "setClaudeModel-canary|package/dist/assets/index-*.js|claudeModel:[A-Za-z\$]+,setClaudeModel:[A-Za-z\$]+,codexModel:[A-Za-z\$]+|CANARY -- upstream-native model-state setters (retired #36 patches)"
  "ws-binary-canary|package/dist-server/server/modules/websocket/services/plugin-websocket-proxy.service.js|clientWs\.send\(data, \{ binary: isBinary \}\)|CANARY -- upstream-native binary frame forwarding (retired #11 patch)"
)

# Resolve a glob inside a tar root to the first matching path, or empty.
resolve_path() {
  local root="$1" glob="$2"
  # shellcheck disable=SC2086
  ls "$root"/$glob 2>/dev/null | head -n1 || true
}

# Find the first regex match in a file; emit up to ~120 chars or a status tag.
probe_anchor() {
  local file="$1" regex="$2"
  if [ -z "$file" ] || [ ! -f "$file" ]; then
    printf '(file missing)'
    return
  fi
  local hit
  hit="$(grep -oE "$regex" "$file" 2>/dev/null | head -n1 || true)"
  if [ -z "$hit" ]; then
    printf '(absent)'
  else
    # Markdown-escape pipes; truncate long minified blobs.
    printf '%s' "$hit" | sed 's/|/\\|/g' | cut -c1-120
  fi
}

# Classify status for the report.
classify() {
  local old="$1" new="$2"
  if [ "$old" = "$new" ]; then
    printf 'unchanged'
  elif [ "$new" = "(absent)" ] || [ "$new" = "(file missing)" ]; then
    printf '**gone in target** -- patch/canary will not apply'
  elif [ "$old" = "(absent)" ] || [ "$old" = "(file missing)" ]; then
    printf '**new in target** -- anchor only appears upstream'
  else
    printf '**rotated** -- identifiers shifted'
  fi
}

old_bundle="$(resolve_path "$old_root" 'package/dist/assets/index-*.js')"
new_bundle="$(resolve_path "$new_root" 'package/dist/assets/index-*.js')"

echo "### Patch-anchor drift report"
echo
echo "| Anchor | Old | New | Status |"
echo "| --- | --- | --- | --- |"

for row in "${anchors[@]}"; do
  IFS='|' read -r name glob regex desc <<<"$row"
  old_file="$(resolve_path "$old_root" "$glob")"
  new_file="$(resolve_path "$new_root" "$glob")"
  old_hit="$(probe_anchor "$old_file" "$regex")"
  new_hit="$(probe_anchor "$new_file" "$regex")"
  status="$(classify "$old_hit" "$new_hit")"
  printf '| `%s` -- %s | `%s` | `%s` | %s |\n' "$name" "$desc" "$old_hit" "$new_hit" "$status"
done

echo
if [ -n "$old_bundle" ] && [ -n "$new_bundle" ]; then
  echo "Bundle: \`$(basename "$old_bundle")\` -> \`$(basename "$new_bundle")\`"
fi
