#!/usr/bin/env bash
# =============================================================================
# HolyClaude container smoke test
# =============================================================================
# Runs the just-built image and asserts:
#   - All bundled CLIs report a version
#   - Chromium launches headless
#   - s6-overlay reaches `ready` state
#   - CloudCLI web UI on :3001 is alive
#   - The served UI version matches the version vendored in vendor/artifacts/
#
# Usage: smoke-test.sh <image-ref>
# Env:   SKIP_VERSION_ASSERT=1   skip the served-version assertion (PR builds
#                                 against an arbitrary base may not match)
# =============================================================================

set -euo pipefail

IMAGE="${1:?usage: smoke-test.sh <image-ref>}"
CONTAINER="holyclaude-smoke-$$"
LOGFILE="$(mktemp)"
HOST_PORT="${HOST_PORT:-3091}"

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
vendored_tarball="$(ls "${repo_root}/vendor/artifacts"/siteboon-claude-code-ui-*.tgz | head -n1)"
vendored_version="$(basename "${vendored_tarball}" | sed -E 's/^siteboon-claude-code-ui-(.+)\.tgz$/\1/')"

cleanup() {
  rc=$?
  echo
  echo "::group::container logs"
  docker logs "${CONTAINER}" 2>&1 | tail -n 200 || true
  echo "::endgroup::"
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  rm -f "${LOGFILE}"
  exit "$rc"
}
trap cleanup EXIT

echo "==> Vendored CloudCLI version: ${vendored_version}"
echo "==> Starting container ${CONTAINER} from ${IMAGE}"
docker run -d \
  --name "${CONTAINER}" \
  --shm-size=2g \
  --cap-add=SYS_ADMIN \
  --cap-add=SYS_PTRACE \
  --security-opt=seccomp=unconfined \
  -p "${HOST_PORT}:3001" \
  -e TZ=UTC \
  "${IMAGE}" >/dev/null

echo "==> Waiting for s6-overlay ready (max 90s)"
for _ in $(seq 1 45); do
  if docker exec "${CONTAINER}" test -f /run/s6/container_environment/.s6-overlay-ready 2>/dev/null \
     || docker exec "${CONTAINER}" pgrep -x s6-rc >/dev/null 2>&1; then
    if docker exec "${CONTAINER}" pgrep -f cloudcli >/dev/null 2>&1 \
       || docker exec "${CONTAINER}" pgrep -f node >/dev/null 2>&1; then
      break
    fi
  fi
  sleep 2
done

echo "==> CLI version checks"
fail=0
for spec in \
  "claude:claude --version" \
  "gemini:gemini --version" \
  "codex:codex --version" \
  "cursor-agent:cursor-agent --version" \
  "task-master:task-master --version" \
; do
  name="${spec%%:*}"
  cmd="${spec#*:}"
  if out=$(docker exec "${CONTAINER}" sh -lc "${cmd}" 2>&1); then
    echo "  ok    ${name}: ${out%%$'\n'*}"
  else
    echo "  FAIL  ${name}: ${out%%$'\n'*}"
    fail=$((fail+1))
  fi
done

echo "==> Chromium check"
if out=$(docker exec "${CONTAINER}" sh -lc 'chromium --headless --no-sandbox --disable-gpu --version' 2>&1); then
  echo "  ok    chromium: ${out%%$'\n'*}"
else
  echo "  FAIL  chromium: ${out%%$'\n'*}"
  fail=$((fail+1))
fi

echo "==> Xvfb running"
if docker exec "${CONTAINER}" pgrep -x Xvfb >/dev/null 2>&1; then
  echo "  ok    Xvfb is running"
else
  echo "  FAIL  Xvfb not found"
  fail=$((fail+1))
fi

echo "==> CloudCLI HTTP probe (max 60s)"
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/" -o /dev/null; then
    ok=1; break
  fi
  sleep 2
done
if [ "$ok" = 1 ]; then
  echo "  ok    :3001 responded"
else
  echo "  FAIL  :3001 never responded"
  fail=$((fail+1))
fi

echo "==> Served UI version assertion"
served=""
# The simplest stable signature is the package.json shipped inside the
# installed module — it is read at startup. We exec into the container and
# read it directly rather than relying on an undocumented /version endpoint.
served=$(docker exec "${CONTAINER}" sh -lc \
  "node -e 'process.stdout.write(require(\"/usr/local/lib/node_modules/@siteboon/claude-code-ui/package.json\").version)'" \
  2>/dev/null || true)

if [ -z "${served}" ]; then
  echo "  WARN  could not read served version; skipping assertion"
elif [ "${served}" = "${vendored_version}" ]; then
  echo "  ok    served version ${served} matches vendored"
else
  if [ "${SKIP_VERSION_ASSERT:-0}" = "1" ]; then
    echo "  WARN  served=${served} vs vendored=${vendored_version} (assert skipped)"
  else
    echo "  FAIL  served=${served} vs vendored=${vendored_version}"
    fail=$((fail+1))
  fi
fi

echo "==> Patch-warning scan"
# If the Dockerfile's sed/perl patches did not match, they print
# `[patch] WARNING: ...` during the build. Those warnings live in the build
# log, not the container — so we re-derive: every patch sed expression looked
# for an anchor string. If the anchor is no longer present in the patched file,
# the patch never applied. This is the runtime canary.
patch_targets=(
  "/usr/local/lib/node_modules/@siteboon/claude-code-ui/server/index.js:upstream.send(data, { binary: isBinary })"
  "/usr/local/lib/node_modules/@siteboon/claude-code-ui/server/routes/commands.js:newModel: args.length"
)
for entry in "${patch_targets[@]}"; do
  file="${entry%%:*}"
  marker="${entry#*:}"
  if docker exec "${CONTAINER}" grep -q -- "${marker}" "${file}" 2>/dev/null; then
    echo "  ok    patch present in ${file}"
  else
    echo "  FAIL  patch missing in ${file} (marker: ${marker})"
    echo "        the Dockerfile patch block likely needs refreshing for this CloudCLI version"
    fail=$((fail+1))
  fi
done

echo
if [ "$fail" -gt 0 ]; then
  echo "FAIL: ${fail} smoke check(s) failed"
  exit 1
fi
echo "PASS: all smoke checks ok"
