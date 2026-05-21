#!/bin/bash
# ==============================================================================
# HolyClaude — CloudCLI plugin seeding (runs every boot from entrypoint.sh)
# ==============================================================================
# cloudcli hard-codes its plugin directory to ~/.claude-code-ui/plugins (see
# dist-server/server/utils/plugin-loader.js — no env/config override). In the
# k8s deployment a shared PVC mounts over /home/claude, masking any plugins
# baked into the image. The dir-scan then finds only stale, source-only plugin
# copies on the PVC and every plugin crashes at boot with:
#     Cannot find module '.../<plugin>/dist/server.js'
#
# This script reconciles the PVC plugin dir on every boot:
#   1. drops dangling symlinks (the dir-scan would try to launch them)
#   2. seeds the pre-built bundled plugins from the image staging dir
#      (/usr/local/share/holyclaude/plugins) — overwrite, they are managed
#   3. compiles any remaining plugin (e.g. user-added) that lacks dist/server.js
#
# Best-effort by design: it must NEVER abort container startup, so it has no
# `set -e` and entrypoint.sh invokes it with `|| true`.
# ==============================================================================

CLAUDE_HOME="${CLAUDE_HOME:-/home/claude}"
CLAUDE_USER="${CLAUDE_USER:-claude}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
PLUGINS_SRC="/usr/local/share/holyclaude/plugins"
PLUGINS_DST="${CLAUDE_HOME}/.claude-code-ui/plugins"

echo "[seed-plugins] reconciling CloudCLI plugins in ${PLUGINS_DST}"
mkdir -p "${PLUGINS_DST}" || { echo "[seed-plugins] WARN cannot create ${PLUGINS_DST}; skipping"; exit 0; }

# ---------- 1. Drop dangling symlinks ----------
# cloudcli's plugin discovery is a directory scan; a dangling symlink (e.g. a
# leftover dev-mount into /workspace) makes it attempt to launch a dead path.
for entry in "${PLUGINS_DST}"/* "${PLUGINS_DST}"/.[!.]*; do
    [ -e "${entry}" ] || [ -L "${entry}" ] || continue
    if [ -L "${entry}" ] && [ ! -e "${entry}" ]; then
        echo "[seed-plugins] removing dangling symlink: $(basename "${entry}")"
        rm -f "${entry}" || true
    fi
done

# ---------- 2. Seed pre-built bundled plugins (overwrite) ----------
if [ -d "${PLUGINS_SRC}" ]; then
    for src in "${PLUGINS_SRC}"/*/; do
        [ -d "${src}" ] || continue
        name="$(basename "${src}")"
        echo "[seed-plugins] seeding bundled plugin: ${name}"
        rm -rf "${PLUGINS_DST:?}/${name}" || true
        cp -a "${src%/}" "${PLUGINS_DST}/" || echo "[seed-plugins] WARN copy failed: ${name}"
    done
else
    echo "[seed-plugins] WARN staging dir ${PLUGINS_SRC} not found"
fi

chown -R "${PUID}:${PGID}" "${PLUGINS_DST}" 2>/dev/null || true

# ---------- 3. Compile any plugin missing its server bundle ----------
# Bundled plugins arrive pre-built so this is a no-op for them; it recovers
# user-added plugins that were copied onto the shared PVC as source only.
for dir in "${PLUGINS_DST}"/*/; do
    [ -d "${dir}" ] || continue
    name="$(basename "${dir}")"
    [ -f "${dir}package.json" ] || continue
    [ -f "${dir}dist/server.js" ] && continue
    echo "[seed-plugins] building plugin '${name}' (no dist/server.js)"
    if runuser -u "${CLAUDE_USER}" -- bash -c \
        "cd '${dir}' && npm install --no-audit --no-fund --loglevel=error && npm run build"; then
        echo "[seed-plugins] built '${name}'"
    else
        echo "[seed-plugins] WARN build failed for '${name}' — cloudcli will skip it"
    fi
done

echo "[seed-plugins] done"
exit 0
