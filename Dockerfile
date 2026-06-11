# ==============================================================================
# HolyClaude — Pre-configured Docker Environment for Claude Code CLI + CloudCLI
# https://github.com/coderluii/holyclaude
#
# Build variants:
#   docker build -t holyclaude .                        # full (default)
#   docker build --build-arg VARIANT=slim -t holyclaude:slim .
# ==============================================================================

FROM node:24-bookworm-slim

LABEL org.opencontainers.image.source=https://github.com/CoderLuii/HolyClaude

# ---------- Build args ----------
ARG S6_OVERLAY_VERSION=3.2.0.2
ARG TARGETARCH
ARG VARIANT=full

# ---------- Environment ----------
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    DISPLAY=:99 \
    DBUS_SESSION_BUS_ADDRESS=disabled: \
    CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage" \
    CHROME_PATH=/usr/bin/chromium \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# ---------- s6-overlay v3 (multi-arch) ----------
RUN apt-get update && apt-get install -y --no-install-recommends xz-utils curl ca-certificates && rm -rf /var/lib/apt/lists/*
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp/
RUN S6_ARCH=$(case "$TARGETARCH" in arm64) echo "aarch64";; *) echo "x86_64";; esac) && \
    curl -fsSL -o /tmp/s6-overlay-arch.tar.xz \
      "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${S6_ARCH}.tar.xz" && \
    tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz && \
    tar -C / -Jxpf /tmp/s6-overlay-arch.tar.xz && \
    rm /tmp/s6-overlay-*.tar.xz

# ---------- System packages (always installed) ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core utilities
    git curl wget jq ripgrep fd-find unzip zip tree tmux fzf bat bubblewrap \
    # Build tools
    build-essential pkg-config python3 python3-pip python3-venv \
    # Browser (Playwright/Puppeteer)
    chromium \
    # Fonts
    fonts-liberation2 fonts-dejavu-core fonts-noto-core fonts-noto-color-emoji fonts-inter \
    # Locale support
    locales \
    # Debugging tools
    strace lsof iproute2 procps htop \
    # Database CLI tools
    postgresql-client redis-tools sqlite3 \
    # SSH client (NOT server)
    openssh-client \
    # Xvfb for headless Chrome
    xvfb \
    # Image processing
    imagemagick \
    # Sudo
    sudo \
    && rm -rf /var/lib/apt/lists/*

# ---------- ImageMagick security patch (deb12u9) ----------
# node:24-bookworm-slim ships imagemagick deb12u8; Debian Security released
# deb12u9 with fixes for CVE-2026-25971 (critical), CVE-2026-33900/33901/
# 33905/33908 (high), CVE-2026-33899/34238/40310/40311 (medium).
# This step upgrades the five affected packages explicitly so the fix lands
# regardless of base-image layer-cache age.
RUN apt-get update && apt-get install -y --only-upgrade --no-install-recommends \
    imagemagick \
    imagemagick-6-common \
    imagemagick-6.q16 \
    libmagickcore-6.q16-6 \
    libmagickwand-6.q16-6 \
    && rm -rf /var/lib/apt/lists/*

# ---------- bubblewrap setuid (Codex CLI sandbox on restricted kernels) ----------
RUN chmod u+s /usr/bin/bwrap

# ---------- Full-only system packages ----------
RUN if [ "$VARIANT" = "full" ]; then \
    apt-get update && apt-get install -y --no-install-recommends \
      pandoc ffmpeg libvips-dev \
    && rm -rf /var/lib/apt/lists/*; \
    fi

# ---------- Azure CLI (full only) ----------
RUN if [ "$VARIANT" = "full" ]; then \
    curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    && rm -rf /var/lib/apt/lists/*; \
    fi

# ---------- GitHub CLI ----------
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y gh && rm -rf /var/lib/apt/lists/*

# ---------- Chromium + GitHub CLI security patch ----------
# Trivy post-Phase-2 re-scan exposed two alert clusters sharing the same
# --only-upgrade fix idiom used for ImageMagick (Phase 1):
#
#   chromium / chromium-common: 250 alerts (2 critical, 3 high, ~245 note)
#     installed 147.0.7727.137-1~deb12u1; fixed in 148.0.7778.96-1~deb12u1
#     already in Debian Security (bookworm-security).
#
#   gh (/usr/bin/gh): ~8 note-severity alerts tracing to Go 1.26.2 stdlib
#     CVEs; a newer build is already in cli.github.com/packages stable main.
#
# Placement: after the gh install block so BOTH apt sources (Debian Security
# for chromium and cli.github.com/packages for gh) are already configured
# when this layer runs. Splitting into two layers would add no benefit and
# would double the apt-get update cost.
#
# Once node:24-bookworm-slim ships 148.x and a gh built on a patched Go
# stdlib, this layer becomes a no-op and can be removed.
RUN apt-get update && apt-get install -y --only-upgrade --no-install-recommends \
    chromium \
    chromium-common \
    gh \
    && rm -rf /var/lib/apt/lists/*

# ---------- bat symlink (Debian names it batcat) ----------
RUN ln -sf /usr/bin/batcat /usr/local/bin/bat 2>/dev/null || true

# ---------- Locale configuration ----------
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen

# ---------- Create claude user ----------
# node:24-bookworm-slim already has UID 1000 as 'node' — rename it to 'claude'
RUN usermod -l claude -d /home/claude -m node && \
    groupmod -n claude node && \
    echo "claude ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/claude && \
    chmod 0440 /etc/sudoers.d/claude

# ---------- Claude Code CLI ----------
# Install at /usr/local/bin — a system PATH dir OUTSIDE the /home/claude
# shared PVC. The native installer (curl https://claude.ai/install.sh) lands
# the binary in ~/.local/bin; once a shared volume mounts over /home/claude
# at runtime that path is masked, leaving CloudCLI's bundled
# @anthropic-ai/claude-agent-sdk — which raw-spawns the bare name "claude" —
# unable to resolve it ("native binary not found at claude"). A system-path
# install survives the volume mount. Version pinned to the latest release.
WORKDIR /workspace
ARG CLAUDE_CODE_VERSION=2.1.146
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# ~/.local/bin stays on PATH for tools that only install there (cursor-agent,
# junie). Those live inside the shared PVC; the claude binary above does not.
ENV PATH="/home/claude/.local/bin:${PATH}"

# ---------- npm global upgrade (fix transitive vulnerabilities) ----------
RUN npm i -g npm@11.14.0

# ---------- npm global packages (slim — always installed) ----------
RUN npm i -g \
    typescript tsx \
    pnpm@11.0.8 \
    vite esbuild \
    eslint prettier \
    serve nodemon concurrently \
    dotenv-cli

# ---------- npm global packages (full only) ----------
RUN if [ "$VARIANT" = "full" ]; then \
    npm i -g \
      wrangler vercel netlify-cli \
      pm2 \
      prisma drizzle-kit \
      eas-cli \
      lighthouse @lhci/cli \
      sharp-cli json-server http-server \
      @marp-team/marp-cli @cloudflare/next-on-pages; \
    fi

# ---------- Python packages (slim — always installed) ----------
RUN pip install --no-cache-dir --break-system-packages \
    requests httpx beautifulsoup4 lxml \
    Pillow \
    pandas numpy \
    openpyxl python-docx \
    jinja2 pyyaml python-dotenv markdown \
    rich click tqdm \
    playwright \
    apprise

# ---------- Python packages (full only) ----------
RUN if [ "$VARIANT" = "full" ]; then \
    pip install --no-cache-dir --break-system-packages \
      reportlab weasyprint cairosvg fpdf2 PyMuPDF pdfkit img2pdf \
      xlsxwriter xlrd \
      matplotlib seaborn \
      python-pptx \
      fastapi uvicorn \
      httpie; \
    fi

# ---------- AI CLI providers ----------
RUN npm i -g @google/gemini-cli @openai/codex task-master-ai
USER claude
RUN curl -fsSL https://cursor.com/install | bash
USER root

# ---------- Junie CLI (full only) ----------
USER claude
RUN if [ "$VARIANT" = "full" ]; then \
    curl -fsSL https://junie.jetbrains.com/install.sh | bash; \
    fi
USER root

# ---------- OpenCode CLI (full only) ----------
RUN if [ "$VARIANT" = "full" ]; then \
    npm i -g opencode-ai; \
    fi

# Renamed package: siteboon/claudecodeui v1.27.x was the last @siteboon/claude-code-ui
# release; @cloudcli-ai/cloudcli is the upstream-renamed continuation. Same
# bin and module shape; install path under /usr/local/lib/node_modules
# changes from @siteboon/claude-code-ui to @cloudcli-ai/cloudcli.
COPY vendor/artifacts/cloudcli-ai-cloudcli-1.34.0.tgz /tmp/vendor/cloudcli-ai-cloudcli-1.34.0.tgz

# ---------- CloudCLI (web UI for Claude Code) ----------
RUN npm i -g /tmp/vendor/cloudcli-ai-cloudcli-1.34.0.tgz && rm -f /tmp/vendor/cloudcli-ai-cloudcli-1.34.0.tgz
RUN touch /usr/local/lib/node_modules/@cloudcli-ai/cloudcli/.env

# Retired patch: WebSocket frame-type forwarding (issue #11).
# CloudCLI 1.34.0 extracted the plugin WS proxy into
# dist-server/server/modules/websocket/services/plugin-websocket-proxy.service.js
# and the compiled output already forwards binary frames natively
# (`upstream.on('message', (data, isBinary) => clientWs.send(data, { binary: isBinary }))`).
# The original patch targeted server/index.js, which no longer carries the
# WS handlers, so it was silently no-opping in 1.34.0 anyway.

# patch: preserve Shell tab scroll position across periodic refresh (issue #35)
# Resolve the content-hashed bundle at build time so this survives version
# bumps as long as the minified xterm focus-callback shape holds.
RUN CLOUDCLI_BUNDLE="$(ls /usr/local/lib/node_modules/@cloudcli-ai/cloudcli/dist/assets/index-*.js | head -n1)" && \
    test -n "$CLOUDCLI_BUNDLE" && \
    grep -q 'const Y=()=>{k.current?.focus()}' "$CLOUDCLI_BUNDLE" && \
    perl -pi -e 's/const Y=\(\)=>\{k\.current\?\.focus\(\)\}/const Y=()=>{const _vp=k.current?.buffer?.active?.viewportY??0;k.current?.focus();k.current?.scrollToLine(_vp)}/g' "$CLOUDCLI_BUNDLE" && \
    echo "[patch] Shell scroll position fix applied" || \
    echo "[patch] WARNING: Shell scroll pattern not found in vendored CloudCLI bundle, skipping patch"

# Retired patches (issue #36, model switching):
#   * server/routes/commands.js — expose newModel field in spawn args
#   * bundle — setClaudeModel in context spread + destructure, SSE
#     case"model" handler, custom-model dropdown option
# CloudCLI 1.34.0 wires setClaudeModel/setCursorModel/setCodexModel through
# the model-state context natively, and the old SSE case"model" / OPTIONS.map
# anchors no longer exist. Re-introduce only if the native flow regresses.

# ---------- CloudCLI plugins (staged outside the home) ----------
# cloudcli hard-codes its plugin dir to ~/.claude-code-ui/plugins. In the
# k8s deployment a shared PVC mounts over /home/claude and masks anything
# baked there, so plugins installed into the home at build time are invisible
# at runtime — the dir-scan finds only stale source-only copies migrated onto
# the PVC and every plugin fails with `dist/server.js` MODULE_NOT_FOUND.
# Build the bundled plugins (compiled `dist/` + node_modules) into an image
# staging dir instead; entrypoint.sh -> seed-plugins.sh copies them into the
# PVC on every boot. See ff-k8s docs/reference/lessons-learned.md.
RUN mkdir -p /usr/local/share/holyclaude/plugins && \
    git clone --depth 1 https://github.com/cloudcli-ai/cloudcli-plugin-starter.git /usr/local/share/holyclaude/plugins/project-stats && \
    npm --prefix /usr/local/share/holyclaude/plugins/project-stats install && \
    npm --prefix /usr/local/share/holyclaude/plugins/project-stats run build && \
    git clone --depth 1 https://github.com/cloudcli-ai/cloudcli-plugin-terminal.git /usr/local/share/holyclaude/plugins/web-terminal && \
    npm --prefix /usr/local/share/holyclaude/plugins/web-terminal install && \
    npm --prefix /usr/local/share/holyclaude/plugins/web-terminal run build && \
    rm -rf /usr/local/share/holyclaude/plugins/*/.git

# ---------- Store variant for bootstrap ----------
RUN echo "${VARIANT}" > /etc/holyclaude-variant

# ---------- Copy config files ----------
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY scripts/bootstrap.sh /usr/local/bin/bootstrap.sh
COPY scripts/seed-plugins.sh /usr/local/bin/seed-plugins.sh
COPY scripts/notify.py /usr/local/bin/notify.py
COPY config/settings.json /usr/local/share/holyclaude/settings.json
COPY config/claude-memory-full.md /usr/local/share/holyclaude/claude-memory-full.md
COPY config/claude-memory-slim.md /usr/local/share/holyclaude/claude-memory-slim.md
RUN chmod +x /usr/local/bin/entrypoint.sh \
    /usr/local/bin/bootstrap.sh \
    /usr/local/bin/seed-plugins.sh \
    /usr/local/bin/notify.py

# ---------- s6-overlay service definitions ----------
COPY s6-overlay/s6-rc.d/cloudcli/type /etc/s6-overlay/s6-rc.d/cloudcli/type
COPY s6-overlay/s6-rc.d/cloudcli/run /etc/s6-overlay/s6-rc.d/cloudcli/run
COPY s6-overlay/s6-rc.d/xvfb/type /etc/s6-overlay/s6-rc.d/xvfb/type
COPY s6-overlay/s6-rc.d/xvfb/run /etc/s6-overlay/s6-rc.d/xvfb/run
RUN chmod +x /etc/s6-overlay/s6-rc.d/cloudcli/run \
    /etc/s6-overlay/s6-rc.d/xvfb/run && \
    touch /etc/s6-overlay/s6-rc.d/user/contents.d/cloudcli && \
    touch /etc/s6-overlay/s6-rc.d/user/contents.d/xvfb

# ---------- Working directory ----------
WORKDIR /workspace

# ---------- Health check ----------
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -sf http://localhost:3001/ || exit 1

# ---------- s6-overlay as PID 1 ----------
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
