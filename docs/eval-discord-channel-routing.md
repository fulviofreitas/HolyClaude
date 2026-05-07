# Evaluation: Per-Workspace Discord Channel Routing for HolyClaude

**Date:** 2026-05-07
**Status:** Evaluation complete — ready for decision
**Author:** Claude (automated evaluation)

---

## Table of Contents

- [1. Executive Summary](#1-executive-summary)
- [2. Landscape Survey](#2-landscape-survey)
  - [2.1 Official Claude Code Channels Plugin](#21-official-claude-code-channels-plugin)
  - [2.2 chadingTV/claudecode-discord](#22-chadingtvclaude-discord)
  - [2.3 timoconnellaus/claude-code-discord-bot](#23-timoconnellausclaudecode-discord-bot)
  - [2.4 Other 2026-Trending Options](#24-other-2026-trending-options)
  - [2.5 Comparison Matrix](#25-comparison-matrix)
- [3. Per-Workspace Isolation Verification](#3-per-workspace-isolation-verification)
- [4. Infrastructure Compatibility](#4-infrastructure-compatibility)
  - [4.1 Host Recommendation](#41-host-recommendation)
  - [4.2 Auth Model](#42-auth-model)
  - [4.3 Persistence](#43-persistence)
  - [4.4 Secrets Management](#44-secrets-management)
- [5. Integration with Existing Stack](#5-integration-with-existing-stack)
  - [5.1 Discord Guild Layout](#51-discord-guild-layout)
  - [5.2 Network / Ingress](#52-network--ingress)
  - [5.3 Workspace Directory Mapping](#53-workspace-directory-mapping)
  - [5.4 Notifications](#54-notifications)
- [6. Risks and Limitations](#6-risks-and-limitations)
- [7. Recommendation](#7-recommendation)
  - [7.1 Go / No-Go](#71-go--no-go)
  - [7.2 High-Level Deployment Outline](#72-high-level-deployment-outline)
  - [7.3 What Would Need to Be Built / Forked](#73-what-would-need-to-be-built--forked)

---

## 1. Executive Summary

**Goal:** Expose multiple Claude Code workspaces (one per project/repo) through a single Discord guild, with one channel per workspace (`#whatsapp-vault`, `#openclaw`, `#calbridge`, etc.) — each channel having its own independent session, conversation history, CLAUDE.md, and tool permissions.

**Recommendation:** **GO** — use **`chadingTV/claudecode-discord`** on **`docker01.local`** (Docker VM on `prox.local`).

This option provides the cleanest channel-to-directory mapping with SQLite-backed session persistence, Discord-native tool approval buttons (no `--dangerously-skip-permissions`), queue management, subscription-based auth (zero per-token cost), and active maintenance (last commit April 2026). It runs as a single Docker container with outbound-only WebSocket to Discord — no ingress tunnels required.

---

## 2. Landscape Survey

### 2.1 Official Claude Code Channels Plugin

**Source:** `anthropics/claude-plugins-official` (18.7k stars on parent repo)
**Status:** Research Preview (launched March 20, 2026)
**Runtime:** Bun (hard requirement)

| Attribute | Detail |
|---|---|
| Architecture | MCP server bridging into an existing, locally-running Claude Code session |
| Multi-workspace | **No native support.** One bot token = one active session. Workaround: separate `DISCORD_STATE_DIR` + separate bot token + separate terminal per workspace |
| Auth | Anthropic-only (Pro/Max/Teams/Enterprise or Console API key). **Not compatible with Bedrock/Vertex/Foundry** |
| Session persistence | None — session must be actively open in a terminal. Messages dropped when closed |
| Permission handling | Remote permission relay via Discord (if configured). Otherwise blocks at terminal |
| RBAC | Pairing code → allowlist model. Guild channel support for team access |
| Restart behavior | Session dies with terminal. Requires tmux/screen workaround for persistence |
| Team/Enterprise | Disabled by default; admin must enable `channelsEnabled` |

**Verdict for our use case:** **Not viable as primary solution.** The one-bot-per-session constraint means we'd need 10+ Discord bots, 10+ terminals (tmux sessions), and 10+ bot tokens to cover all workspaces. The research preview status adds instability risk. However, it's worth monitoring for GA — if Anthropic adds native multi-workspace routing, this becomes the obvious choice.

### 2.2 chadingTV/claudecode-discord

**Source:** [github.com/chadingTV/claudecode-discord](https://github.com/chadingTV/claudecode-discord)

| Metric | Value |
|---|---|
| Stars | 43 |
| Last commit | April 21, 2026 (v1.3.0) |
| Open issues | 1 |
| License | MIT |
| Language | TypeScript (36.7%), Swift (20.4%), C# (20.3%), Python (14.0%) |
| Runtime | Node.js 20+ with `better-sqlite3` |

| Attribute | Detail |
|---|---|
| Architecture | One Discord channel = one project directory. `/register <folder>` binds channel to path under `BASE_PROJECT_DIR` |
| Multi-workspace | **Yes — native.** Unlimited channels per bot instance, each pointing to a different subdirectory. Multi-machine via separate bot instances sharing one guild |
| Auth | **Subscription-only** (Claude Pro/Max via Claude Agent SDK). No API key needed or supported. Zero per-token cost |
| Session persistence | **SQLite-backed.** `sessions` table stores `session_id`, `channel_id`, `status`. Sessions resume on restart via stored session IDs |
| Permission handling | Discord button UI: Approve / Deny / Auto-approve All. Safe tools (Read, Glob) auto-approved. Per-channel `auto_approve` flag persisted in SQLite |
| RBAC | `ALLOWED_USER_IDS` comma-separated allowlist. Guild ID validation. No per-channel user restrictions |
| Queue | In-memory, max 5 per channel. `/queue list` and `/queue clear` commands. **Queue lost on restart** |
| Stop button | Yes — Discord button + `/stop` slash command |
| Restart behavior | Sessions resume from SQLite. Queue (in-memory) is lost. `.bot.lock` prevents duplicate instances |

**Known limitations:**
- In-memory queue lost on restart
- No RBAC differentiation (all allowlisted users are equals)
- O(N²) token growth in long sessions (open issue #4, no fix merged)
- Single `BASE_PROJECT_DIR` per instance (projects outside that tree need absolute path workarounds)
- Native `better-sqlite3` module requires C++ build toolchain

### 2.3 timoconnellaus/claude-code-discord-bot

**Source:** [github.com/timoconnellaus/claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot)

| Metric | Value |
|---|---|
| Stars | 70 |
| Last commit | June 16, 2025 |
| Open issues | 2 |
| License | MIT |
| Language | TypeScript (96.3%) |
| Runtime | **Bun** (hard requirement) |

| Attribute | Detail |
|---|---|
| Architecture | Channel name = folder name. `path.join(BASE_FOLDER, channel.name)` — zero-config mapping |
| Multi-workspace | **Yes — native.** Channel name is the directory name. Simple and elegant |
| Auth | Delegates to Claude Code CLI (subscription or API key, whatever the CLI is configured with) |
| Session persistence | **SQLite-backed** (`sessions.db`). `--resume <session_id>` on subsequent messages. 30-day auto-purge |
| Permission handling | **MCP bridge** — dangerous tools (Bash, Write, Edit) trigger Discord reaction-based approval. No `--dangerously-skip-permissions`. 30-second timeout with configurable default |
| RBAC | **Single user only.** `ALLOWED_USER_ID` accepts exactly one Discord user ID |
| Queue | **None.** Concurrent messages to active channels are silently dropped |
| Stop button | Not documented |
| Restart behavior | Sessions resume from SQLite. No queue to lose |

**Known limitations:**
- **Single user only** — no multi-user, no teams
- **No Docker support** — no Dockerfile, no Compose
- **No queue** — messages dropped during active processing
- Model hard-coded to `sonnet`
- 5-minute hard timeout per process
- `#general` channel permanently excluded
- Bun runtime required (not Node.js)
- Last significant commit: June 2025 — **10+ months stale**

### 2.4 Other 2026-Trending Options

| Project | Stars | Last Commit | Auth | Multi-Workspace | Unique Angle |
|---|---|---|---|---|---|
| **zebbern/claude-code-discord** | 190 | Mar 2026 | API key or subscription | No (per-instance) | Most feature-complete single-workspace bot. Thread-per-session, RBAC, mid-session model switching, sandbox config. `USER_ID` for @mention |
| **chenhg5/cc-connect** | 7,700 | Active | API key per provider | Yes (multi-project) | 10 AI agents × 11 messaging platforms. Go binary. Web admin UI. Overkill for Claude-only use |
| **ebibibi/claude-code-discord-bridge** | 41 | Feb 2026 | Subscription | Partial | Python-based. Git worktree per session. AI Lounge for cross-session coordination. REST API (`POST /api/spawn`). CI/CD integration |
| **JessyTsui/Claude-Code-Remote** | 1,200 | Aug 2025 | Env-based | Yes (session map) | Email/Telegram primary, Discord webhook notifications only |
| **777genius/claude-notifications-go** | 605 | Active | None (hooks) | N/A | Notification-only. Rich Discord embeds. Not interactive |

**Notable:** `zebbern/claude-code-discord` (190 stars) is the most polished single-workspace bot but lacks native multi-workspace routing, which is a hard requirement. `cc-connect` (7,700 stars) is the heavyweight but is an agent-agnostic polyglot hub — massive overkill for "Claude Code on Discord."

### 2.5 Comparison Matrix

| Requirement | Official Plugin | chadingTV | timoconnellaus | zebbern |
|---|---|---|---|---|
| Multi-workspace (1 channel = 1 dir) | ❌ (workaround only) | ✅ Native | ✅ Native | ❌ (per-instance) |
| Session persistence on restart | ❌ (requires tmux) | ✅ SQLite | ✅ SQLite | Partial |
| No `--dangerously-skip-permissions` | ✅ Permission relay | ✅ Discord buttons | ✅ MCP reaction bridge | ✅ Interactive prompts |
| Multi-user RBAC | ✅ Allowlist | ⚠️ Flat allowlist | ❌ Single user | ✅ Role-based |
| Queue / concurrency | N/A | ✅ 5-item queue | ❌ Drop & skip | ✅ Thread-based |
| Subscription auth (zero API cost) | ✅ | ✅ | ✅ (via CLI) | ⚠️ API key default |
| Docker support | ❌ | ⚠️ (needs custom) | ❌ | ✅ Docker Compose |
| Active maintenance (2026) | ✅ (Anthropic) | ✅ (Apr 2026) | ❌ (Jun 2025) | ✅ (Mar 2026) |
| Runtime | Bun | Node.js 20+ | Bun | Deno |

---

## 3. Per-Workspace Isolation Verification

| Requirement | chadingTV (recommended) | Status |
|---|---|---|
| **Independent session state per channel** | Each channel gets its own `session_id` in SQLite, backed by a separate Claude Agent SDK session. Sessions are keyed by `channel_id`. | ✅ Confirmed |
| **Independent CLAUDE.md per workspace** | Claude Code already scopes `CLAUDE.md` per project directory. The bot spawns sessions rooted at the registered directory, so each workspace reads its own `CLAUDE.md` and `.claude/` | ✅ Confirmed (inherits from Claude Code) |
| **Independent .claude/ per workspace** | Same mechanism — `.claude/settings.local.json` in each project directory is respected because the session is started in that directory | ✅ Confirmed |
| **Independent tool permissions per workspace** | Per-directory `.claude/settings.local.json` allowlists/denylists apply. Additionally, per-channel `auto_approve` flag in SQLite controls whether tool approvals are prompted | ✅ Confirmed |
| **Sender allowlist / RBAC** | `ALLOWED_USER_IDS` env var (comma-separated Discord user IDs). Guild ID validation. All messages from non-listed users silently ignored | ✅ Confirmed (flat model, no roles) |
| **Behavior on restart** | Sessions resume from SQLite via stored `session_id`. Channel-to-directory bindings persist. `auto_approve` flags persist. In-memory queue is lost | ⚠️ Partial — queue lost, sessions survive |

---

## 4. Infrastructure Compatibility

### 4.1 Host Recommendation

**Recommendation: `docker01.local` (Docker VM on `prox.local`)**

| Factor | `docker01.local` (Docker VM) | New LXC on `prox0.local` |
|---|---|---|
| Docker native | ✅ Already a Docker host | ❌ Would need Docker-in-LXC (nesting) or direct install |
| Restart semantics | `restart: unless-stopped` in Compose | systemd unit in LXC — works, but another management surface |
| Resource footprint | Bot is lightweight (~200MB RSS). Fits easily alongside existing containers | Dedicated LXC is overkill for a single bot process |
| Proximity to OpenClaw | OpenClaw is on `prox0.local` LXC 400/401 — network hop either way | Same hypervisor, marginally lower latency |
| Workspace access | Bind-mount `/workspace/projects` from host → container | Would need NFS/SMB or rsync — adds complexity and latency |
| Backup/snapshot | Proxmox snapshot of `docker01` VM covers the bot + all other containers | Separate LXC snapshot — cleaner isolation but more operational overhead |
| Existing patterns | HolyClaude already runs on Docker with Compose. Same operational model | Introducing a new deployment pattern for one bot |

**Rationale:** The bot is a lightweight Node.js process with a SQLite database. It doesn't justify its own LXC. `docker01.local` already runs Docker workloads, the workspace directories are already accessible, and the deployment model (Docker Compose + `restart: unless-stopped`) matches the existing HolyClaude pattern exactly. Proximity to OpenClaw on `prox0.local` is irrelevant — the bot talks to Discord via outbound WebSocket and to Claude via the Agent SDK; there's no local inter-service traffic.

### 4.2 Auth Model

| Option | Auth Mechanism | Cost Model | Supported By |
|---|---|---|---|
| Official Plugin | Anthropic auth (Pro/Max/Teams/Enterprise or Console API key) | Subscription or per-token (Console key) | Official plugin only |
| chadingTV | **Subscription only** (Claude Pro/Max via Claude Agent SDK) | Flat subscription fee, zero per-token | chadingTV only |
| timoconnellaus | Claude Code CLI (subscription or API key, whatever CLI is configured with) | Depends on CLI config | timoconnellaus |
| zebbern | API key (`ANTHROPIC_API_KEY`) or subscription via CLI | Per-token or subscription | zebbern |

**For our deployment:** chadingTV uses subscription-only auth. This means:
- Authenticate the Claude Code CLI on `docker01.local` once via `claude auth login` (OAuth flow).
- Credentials stored at `~/.claude/.credentials.json` inside the container.
- The bot inherits the auth — no API key to manage or rotate.
- **Cost:** Flat subscription fee (Pro: $20/mo, Max: $100/mo or $200/mo). No per-token billing regardless of usage volume.

### 4.3 Persistence

**Recommended approach:** Docker Compose with `restart: unless-stopped`

```yaml
# Shape of the deployment (not execution — for reference only)
services:
  claude-discord-bot:
    build: ./claudecode-discord
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - DISCORD_GUILD_ID=${DISCORD_GUILD_ID}
      - ALLOWED_USER_IDS=${ALLOWED_USER_IDS}
      - BASE_PROJECT_DIR=/workspace/projects
    volumes:
      - /path/to/workspace/projects:/workspace/projects
      - /path/to/claude-credentials:/home/node/.claude
      - ./data/bot-db:/app/data  # SQLite persistence
```

**Why not tmux/screen?** Those are session-dependent workarounds, not proper service management. Docker Compose with restart policy handles crashes, host reboots, and OOM kills automatically.

**Why not systemd?** Would work in an LXC, but since we're recommending `docker01.local`, Docker Compose is the native idiom.

### 4.4 Secrets Management

Follow the existing pattern observed in the HolyClaude stack: `.env` files at the deployment root, gitignored, with secrets passed as environment variables to containers.

| Secret | Source | Storage |
|---|---|---|
| `DISCORD_TOKEN` | Discord Developer Portal → Bot → Token | `.env` on `docker01.local` |
| `DISCORD_GUILD_ID` | Discord → Server Settings → Widget → Server ID | `.env` on `docker01.local` |
| `ALLOWED_USER_IDS` | Discord → User Settings → Advanced → Developer Mode → Copy User ID | `.env` on `docker01.local` |
| Claude credentials | `claude auth login` OAuth flow | `~/.claude/.credentials.json` (bind-mounted volume) |

**No new secret management pattern needed.** The existing `.env` + Docker volume bind mount pattern used by HolyClaude is sufficient. The `.env` file is already gitignored at the workspace level.

---

## 5. Integration with Existing Stack

### 5.1 Discord Guild Layout

Single private guild. Recommended category and channel structure:

```
📁 CLAUDE CODE WORKSPACES
├── #whatsapp-vault          → /workspace/projects/whatsapp_downloader
├── #openclaw                → /workspace/projects/openclaw
├── #calbridge               → /workspace/projects/calbridge
├── #ff-k8s                  → /workspace/projects/ff-k8s
├── #holyclaude              → /workspace/projects/HolyClaude
├── #eero-ui                 → /workspace/projects/eero-ui
├── #eero-api                → /workspace/projects/eero-api
├── #eeroctl                 → /workspace/projects/eeroctl
├── #eero-prometheus         → /workspace/projects/eero-prometheus-exporter
├── #bambuddy-cloud          → /workspace/projects/bambuddy_cloud
├── #notion-automations      → /workspace/projects/notion-automations
├── #unbound                 → /workspace/projects/unbound
├── #workflow-arsenal         → /workspace/projects/workflow-arsenal
├── #dns-zones               → /workspace/projects/dns-zones
├── #prox-cluster            → /workspace/projects/prox-cluster
├── #prox-new                → /workspace/projects/prox-new
├── #cloudcli-ccusage        → /workspace/projects/cloudcli-plugin-ccusage
📁 BOT ADMIN
├── #bot-logs                → (bot status, errors, queue events)
├── #bot-config              → (slash commands: /register, /unregister, /queue)
```

**Role-based permission scheme:**

| Role | Permissions | Members |
|---|---|---|
| `@Bot Admin` | Manage Channels, Manage Messages, Use Slash Commands in all channels | You (owner) |
| `@Claude Bot` | Send Messages, Read Message History, Embed Links, Attach Files, Add Reactions, Use External Emojis | Bot application |
| `@everyone` | No access to any channel (private guild, explicit grants only) | — |

**Category permissions:** Lock the `CLAUDE CODE WORKSPACES` category to `@Bot Admin` + `@Claude Bot` only. This prevents accidental access if you ever invite someone to the guild.

### 5.2 Network / Ingress

**Confirmed: No Cloudflare Tunnel or ingress needed.**

The Discord bot connects outbound via WebSocket to `gateway.discord.gg`. The Claude Agent SDK connects outbound to Anthropic's API. Both are outbound-only TCP connections initiated from the container. No listening ports need to be exposed to the internet.

The only exposed port is the one Docker may bind for container-to-host communication (if any), which stays on the local Docker network.

### 5.3 Workspace Directory Mapping

Verified directories on `/workspace/projects/` that should each get a channel:

| Channel Name | Directory Path | Notes |
|---|---|---|
| `#whatsapp-vault` | `/workspace/projects/whatsapp_downloader` | WhatsApp media downloader |
| `#openclaw` | `/workspace/projects/openclaw` | OpenClaw project |
| `#calbridge` | `/workspace/projects/calbridge` | Calendar bridge |
| `#ff-k8s` | `/workspace/projects/ff-k8s` | Kubernetes homelab (contains terraform-proxmox, terraform-talos subdirs) |
| `#holyclaude` | `/workspace/projects/HolyClaude` | This project |
| `#eero-ui` | `/workspace/projects/eero-ui` | Eero dashboard UI |
| `#eero-api` | `/workspace/projects/eero-api` | Eero API wrapper |
| `#eeroctl` | `/workspace/projects/eeroctl` | Eero CLI tool |
| `#eero-prometheus` | `/workspace/projects/eero-prometheus-exporter` | Eero Prometheus exporter |
| `#bambuddy-cloud` | `/workspace/projects/bambuddy_cloud` | Bambu Lab cloud integration |
| `#notion-automations` | `/workspace/projects/notion-automations` | Notion workflow automations |
| `#unbound` | `/workspace/projects/unbound` | DNS resolver config |
| `#workflow-arsenal` | `/workspace/projects/workflow-arsenal` | Workflow tools collection |
| `#dns-zones` | `/workspace/projects/dns-zones` | DNS zone management |
| `#prox-cluster` | `/workspace/projects/prox-cluster` | Proxmox cluster config |
| `#prox-new` | `/workspace/projects/prox-new` | New Proxmox setup |
| `#cloudcli-ccusage` | `/workspace/projects/cloudcli-plugin-ccusage` | CloudCLI usage plugin |
| `#ff-net` | `/workspace/projects/ff-net` | Network config |
| `#tfstates-ui` | `/workspace/projects/tfstates-ui` | Terraform state viewer |

**Note:** Channel names in Discord are lowercase with hyphens (Discord enforces this). The `/register` command in chadingTV maps channel IDs (not names) to directories, so the channel name doesn't need to match the directory name exactly — it's just a human-readable label.

**Directories intentionally excluded:**
- `calbridge-fix` — temporary fix branch, not a standalone project
- `eero-*-context` — context dumps, not active projects
- `codeserver`, `fix-mcp`, `freitasnas`, `mediaserver`, `migrate-perc-unraid-to-prox`, `plex`, `prox-updates-fix`, `teslamate`, `tigra-store` — inactive, infrastructure-only, or no active Claude Code work
- `awesome-claude-code-subagents`, `docker01` — reference/utility directories

### 5.4 Notifications

**Recommendation: Enable @mention on task completion.**

chadingTV's bot sends Discord push notifications when Claude needs approval or completes tasks. These appear as mobile notifications even when the phone is locked — functionally equivalent to `USER_ID`-based @mention.

For explicit @mention behavior (pinging a specific user), this would need a minor customization: the bot's completion handler would need to prepend `<@USER_ID>` to the completion message. This is a ~5-line change in the session manager's `onComplete` callback.

**Additionally:** Keep the existing Apprise notification system in HolyClaude (`notify.py` with `NOTIFY_DISCORD` webhook) as a separate, independent notification channel for Claude Code hook events (stop, error). These serve different purposes:
- **Bot notifications:** Interactive session events (approval requests, completions, queue updates)
- **Apprise webhook:** Fire-and-forget alerts from Claude Code hooks (task complete, tool failure)

---

## 6. Risks and Limitations

### Critical Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Official Plugin is research preview** | Medium | We're not using it as primary. Monitor for GA and re-evaluate when multi-workspace support lands |
| **Anthropic auth only (official plugin)** | Low | Not using official plugin. chadingTV uses subscription auth which is Anthropic-native anyway |
| **Permission prompts block sessions** | High | chadingTV solves this with Discord button approval UI. Per-channel `auto_approve` for trusted workspaces. **Do NOT use `--dangerously-skip-permissions` globally** |
| **Single bot process = SPOF** | Medium | Docker `restart: unless-stopped` handles crashes. SQLite sessions survive restart. In-memory queue is lost (acceptable — queue is small, 5 items max) |
| **No crash isolation per workspace** | Medium | A panic in one session's handler could theoretically affect others. The Claude Agent SDK runs sessions in separate async contexts, but they share a process. **Acceptable risk for personal use** |
| **Discord Message Content Intent** | Low | Must be enabled in Discord Developer Portal → Bot → Privileged Gateway Intents → Message Content Intent. One-time setup, but forgetting it causes empty messages |
| **O(N²) token growth in long sessions** | Medium | Open issue #4. Workaround: periodically `/clear` channels with long session histories. Monitor for upstream fix |
| **Subscription dependency** | Low | If Claude Pro/Max subscription lapses, all sessions stop. No API key fallback. Keep subscription active |

### Security Trade-offs Per Workspace

| Workspace | Recommended Permission Mode | Justification |
|---|---|---|
| `#ff-k8s` | **Manual approval** (auto_approve OFF) | Terraform/Talos — destructive infrastructure changes possible |
| `#prox-cluster`, `#prox-new` | **Manual approval** | Proxmox infrastructure |
| `#dns-zones` | **Manual approval** | DNS changes can cause outages |
| `#holyclaude` | **Manual approval** | Changes to this project affect the bot itself |
| `#openclaw`, `#calbridge`, `#eero-*` | **Auto-approve OK** | Application code, lower blast radius |
| `#whatsapp-vault` | **Auto-approve OK** | Data processing, no infrastructure risk |
| `#bambuddy-cloud`, `#notion-automations` | **Auto-approve OK** | Application code |
| `#unbound` | **Manual approval** | DNS resolver config — mistakes cause DNS resolution failures |

### Limitations to Accept

1. **No per-channel user restrictions** — all allowlisted users can interact with all channels. Acceptable for personal/small-team use.
2. **Queue is in-memory** — lost on restart. Acceptable given small queue size (5) and fast restart (~3 seconds).
3. **Single `BASE_PROJECT_DIR`** — all projects must be under one root. Our `/workspace/projects` is already this root. ✅
4. **`better-sqlite3` native module** — requires C++ toolchain in the Docker build. Standard for Node.js Docker images.

---

## 7. Recommendation

### 7.1 Go / No-Go

**GO.**

`chadingTV/claudecode-discord` satisfies all core requirements:
- ✅ One channel = one directory (native design)
- ✅ Independent sessions, CLAUDE.md, permissions per workspace
- ✅ No `--dangerously-skip-permissions` (Discord button approval)
- ✅ Session persistence across restarts (SQLite)
- ✅ Subscription auth (zero per-token cost)
- ✅ Sender allowlist
- ✅ Active maintenance (April 2026)
- ✅ MIT license

**Host:** `docker01.local` (Docker VM on `prox.local`)

### 7.2 High-Level Deployment Outline

> **This is a plan, not execution. No changes have been made.**

#### Step 1: Discord Setup (~10 minutes)

1. Create a Discord Application at [discord.com/developers](https://discord.com/developers/applications)
2. Create a Bot under the application. Copy the bot token.
3. Enable **Message Content Intent** under Privileged Gateway Intents.
4. Generate an OAuth2 invite URL with permissions: Send Messages, Read Message History, Embed Links, Attach Files, Add Reactions, Use Slash Commands.
5. Invite the bot to the private guild.
6. Create the `CLAUDE CODE WORKSPACES` category and channels per §5.1.
7. Lock category permissions to `@Bot Admin` + `@Claude Bot`.

#### Step 2: Prepare `docker01.local` (~15 minutes)

1. Clone the repo:
   ```bash
   cd /opt/stacks  # or wherever Docker projects live on docker01
   git clone https://github.com/chadingTV/claudecode-discord.git
   cd claudecode-discord
   ```

2. Create `.env`:
   ```env
   DISCORD_TOKEN=<bot-token-from-step-1>
   DISCORD_GUILD_ID=<guild-id>
   ALLOWED_USER_IDS=<your-discord-user-id>
   BASE_PROJECT_DIR=/workspace/projects
   ```

3. Authenticate Claude Code CLI:
   ```bash
   # Inside the container or on the host where Claude CLI is installed
   claude auth login
   # Complete OAuth flow — credentials stored at ~/.claude/.credentials.json
   ```

4. Create `docker-compose.yml` (shape):
   ```yaml
   services:
     claude-discord:
       build: .
       restart: unless-stopped
       env_file: .env
       volumes:
         - /path/to/workspace/projects:/workspace/projects
         - /path/to/claude-credentials:/home/node/.claude
         - ./data:/app/data
   ```

5. Start:
   ```bash
   docker compose up -d
   ```

#### Step 3: Register Channels (~5 minutes)

In each Discord channel, run:
```
/register whatsapp_downloader    (in #whatsapp-vault)
/register openclaw               (in #openclaw)
/register calbridge              (in #calbridge)
/register ff-k8s                 (in #ff-k8s)
...etc
```

#### Step 4: Configure Per-Channel Permissions

In infrastructure channels (`#ff-k8s`, `#prox-cluster`, `#dns-zones`, `#holyclaude`, `#unbound`):
```
/auto-approve off
```

In application channels (`#openclaw`, `#calbridge`, `#eero-*`, `#whatsapp-vault`, `#bambuddy-cloud`):
```
/auto-approve on
```

#### Step 5: Snapshot / Backup

Take a Proxmox snapshot of the `docker01` VM after successful deployment. This captures the bot container, SQLite database, registered channel mappings, and Claude credentials in a single recoverable state.

### 7.3 What Would Need to Be Built / Forked

| Gap | Effort | Approach |
|---|---|---|
| **@mention on completion** | ~1 hour | Fork or PR: add `<@USER_ID>` prefix to completion messages in session manager's `onComplete` callback. ~5 lines of code |
| **Dockerfile** | ~2 hours | The repo includes install scripts but no Docker image. Write a `Dockerfile` (Node.js 20 base, `npm install`, `npm run build`, copy dist). Straightforward |
| **Per-channel user restrictions** | Not needed now | If needed later: add a `channel_allowed_users` table to SQLite, gate messages per channel. Medium effort (~1 day) |
| **Persistent queue** | Nice-to-have | Move queue from in-memory `Map` to SQLite `message_queue` table. Low priority — queue loss on restart is acceptable for 5-item queues |
| **Health check endpoint** | Nice-to-have | Add an HTTP `/health` endpoint for Docker `HEALTHCHECK` directive. ~30 minutes |

**No forking is strictly required for initial deployment.** The Dockerfile is the only blocking item, and it's a standard Node.js containerization task. The @mention customization is desirable but not blocking.

---

## Appendix A: Rejected Alternatives — Rationale

| Option | Why Rejected |
|---|---|
| **Official Claude Code Channels Plugin** | No native multi-workspace. Requires N bots + N terminals for N workspaces. Research preview instability. Re-evaluate at GA |
| **timoconnellaus/claude-code-discord-bot** | Single-user only. No Docker. No queue. 10+ months stale. Bun runtime adds operational complexity. Elegant concept but unmaintained |
| **zebbern/claude-code-discord** | No native multi-workspace routing. Would require one bot instance per workspace — same N-instance problem as official plugin. Best for single-project use |
| **chenhg5/cc-connect** | Overkill. 11 platforms, 10 agents, Go binary, Web UI. We need Claude Code on Discord, not a polyglot agent hub |
| **ebibibi/claude-code-discord-bridge** | Python-based (different ops surface). Thread-per-session model doesn't match our channel-per-workspace requirement. Interesting for CI/CD integration but not our use case |
| **New LXC on `prox0.local`** | Overkill for a single bot process. Introduces new deployment pattern (systemd in LXC vs. Docker Compose). No meaningful proximity benefit |

## Appendix B: Future Considerations

1. **Official plugin GA:** When Anthropic ships multi-workspace support for Claude Code Channels, re-evaluate migrating from chadingTV to the official solution. The official plugin's MCP bridge architecture (events pushed into a running session with full local context) is architecturally superior to spawning new sessions per message.

2. **cc-connect as a long-term hub:** If the homelab grows to include multiple AI agents (Codex, Gemini CLI, Cursor) across multiple platforms (Discord, Telegram, Slack), cc-connect's 7.7k-star Go binary becomes the natural convergence point. Monitor for Discord improvements.

3. **Session TTL / turn limits:** Track chadingTV issue #4 (O(N²) token growth). If not resolved upstream, implement a cron job or Discord slash command (`/reset-session`) that clears sessions older than N turns.

4. **Monitoring:** After deployment, add a lightweight uptime check — e.g., a cron job that queries the bot's SQLite for the last activity timestamp, and alerts via Apprise if the bot hasn't processed a message in >1 hour during active hours.
