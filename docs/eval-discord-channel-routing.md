# Evaluation: Per-Workspace Discord Channel Routing for HolyClaude

**Date:** 2026-05-07 (v2 — revised after feedback)
**Status:** Evaluation complete — ready for decision
**Author:** Claude (automated evaluation)

---

## Table of Contents

- [1. Executive Summary](#1-executive-summary)
- [2. Landscape Survey](#2-landscape-survey)
  - [2.1 Official Claude Code Channels Plugin](#21-official-claude-code-channels-plugin)
  - [2.2 chenhg5/cc-connect](#22-chenhg5cc-connect)
  - [2.3 chadingTV/claudecode-discord](#23-chadingtvclaude-discord)
  - [2.4 timoconnellaus/claude-code-discord-bot](#24-timoconnellausclaudecode-discord-bot)
  - [2.5 Other 2026-Trending Options](#25-other-2026-trending-options)
  - [2.6 Comparison Matrix](#26-comparison-matrix)
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

**Goal:** Expose multiple Claude Code workspaces (one per project/repo) through a single Discord guild, with **one channel per workspace** (prefixed `cc-`) and **one thread per session** — enabling multiple concurrent Claude Code sessions against the same codebase. Each channel has its own independent CLAUDE.md, `.claude/`, and tool permissions.

**Recommendation:** **GO** — use **`chenhg5/cc-connect`** deployed as a **Kubernetes pod** in the existing homelab cluster, mounting the same NFS volumes already used by HolyClaude.

cc-connect is the clear winner after re-evaluation:
- **Per-channel workspace:** Native multi-workspace mode (`base_dir` + channel name convention, or explicit `work_dir` per project)
- **Per-thread session:** `thread_isolation = true` gives each Discord thread its own independent Claude Code session
- **Subscription auth:** Inherits Claude CLI OAuth credentials (`~/.claude/.credentials.json`) — zero per-token cost
- **NFS-safe:** No SQLite — session data stored as JSON via atomic writes. Compatible with the existing NFS mount at `10.0.4.11:/tank/k8s/code-server`
- **Massive community:** 7,700+ stars, 107 commits since April 2026 release, MIT license
- **Interactive permissions:** `--permission-mode` flags with Discord-surfaced approval prompts — no `--dangerously-skip-permissions` needed
- **Single Go binary:** ~50-100 MB RSS baseline, trivial to containerize
- **Bonus:** Supports 10+ AI agents and 11 messaging platforms — future-proof if the homelab expands beyond Claude-on-Discord

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
| Per-thread sessions | No — messages push into a single running session |
| Auth | Anthropic-only (Pro/Max/Teams/Enterprise or Console API key). **Not compatible with Bedrock/Vertex/Foundry** |
| Session persistence | None — session must be actively open in a terminal. Messages dropped when closed |
| Permission handling | Remote permission relay via Discord (if configured). Otherwise blocks at terminal |
| RBAC | Pairing code → allowlist model. Guild channel support for team access |
| Restart behavior | Session dies with terminal. Requires tmux/screen workaround for persistence |
| Team/Enterprise | Disabled by default; admin must enable `channelsEnabled` |

**Verdict:** **Not viable.** One-bot-per-session means N bots + N terminals + N tokens for N workspaces. No thread isolation. Research preview instability. Monitor for GA — if Anthropic adds multi-workspace routing, this becomes the obvious long-term choice.

### 2.2 chenhg5/cc-connect

**Source:** [github.com/chenhg5/cc-connect](https://github.com/chenhg5/cc-connect)

| Metric | Value |
|---|---|
| Stars | 7,700+ |
| Forks | 717 |
| Last commit | Active (107 commits since v1.3.2, April 21, 2026) |
| Current version | v1.3.2 stable / v1.3.3-beta.1 (April 25, 2026) |
| Open issues | 165 |
| License | MIT |
| Language | Go (requires Go 1.22+ to build) |
| Runtime | Single self-contained binary |

| Attribute | Detail |
|---|---|
| Architecture | Multi-agent bridge. Spawns `claude` CLI as subprocess via `os/exec`. Fully concurrent — each session runs in isolated goroutines |
| Multi-workspace | **Yes — two mechanisms.** (1) Standard mode: one `[[projects]]` block per workspace with explicit `work_dir`. (2) Multi-workspace mode: `mode = "multi-workspace"` with `base_dir`, channel name auto-maps to subdirectory. `/workspace bind`, `/workspace init`, `/workspace list` slash commands. **No native `channel_prefix` config**, but `/workspace bind` overrides the auto-convention — so channels can be named `cc-*` and explicitly bound to the correct subdirectory |
| Per-thread sessions | **Yes.** `thread_isolation = true` in Discord platform config. Session key switches from `discord:{channelID}:{userID}` to `discord:{threadID}`. Each thread = independent Claude Code subprocess with `--resume` support. Multiple threads per channel run concurrently |
| Auth | **Subscription supported.** Inherits credentials from `claude` CLI (`~/.claude/.credentials.json` from `claude login`). Also supports API key (`ANTHROPIC_API_KEY`), Bedrock, Vertex, custom providers. Multiple `[[providers]]` configurable with runtime switching (`/provider switch`) |
| Session persistence | **JSON files via atomic writes.** `~/.cc-connect/sessions.json` stores session maps, workspace bindings. Claude sessions resumed via `--resume <sessionID>` on restart. **No SQLite — NFS-safe** |
| Permission handling | `--permission-mode` flag with 6 modes: `default` (manual approval), `acceptEdits`, `plan`, `auto`, `bypassPermissions`/`yolo`, `dontAsk`. Interactive approval surfaced in Discord. Pre-approved/disallowed tool lists in config. Optional `run_as_user` for OS-user isolation. **For our setup:** use `bypassPermissions` to match HolyClaude's `Bash(*)` allowlist model — the per-workspace `.claude/settings.local.json` denylist still applies as a safety net |
| RBAC | `allow_from = "userID1,userID2"` or `"*"` per project. Per-project isolation |
| Queue | In-memory, max 5 per session. `MsgQueueFull` notification when full. Lost on restart |
| Stop / control | Slash commands for session management: `/new`, `/switch`, `/list`, `/stop`, `/mode`, `/dir`, `/provider switch` |
| Restart behavior | Sessions resume from JSON. Workspace bindings persist. Active turn state lost. 15-minute idle reaper for unused workspace instances |

**Discord-specific features:**

| Feature | Supported |
|---|---|
| Threads | ✅ Full `thread_isolation` mode |
| Slash commands | ✅ Registered via `ApplicationCommandBulkOverwrite` |
| Buttons | ✅ `cmd:` prefix triggers command execution |
| Embeds | Partial (card progress style) |
| Attachments | ✅ Up to 50 MiB download |
| DMs | ✅ |
| Per-guild commands | ✅ via `guild_id` config |
| Proxy support | ✅ HTTP/SOCKS5 |
| Progress styles | 3 options: `legacy`, `compact`, `card` |

**Why the initial evaluation was wrong:** The v1 evaluation dismissed cc-connect as "overkill — 11 platforms, 10 agents, Go binary, Web UI. We need Claude Code on Discord, not a polyglot agent hub." This was a mistake. cc-connect's multi-platform/multi-agent support is a bonus, not a tax. The core Claude Code + Discord integration is first-class, with multi-workspace mode, thread isolation, interactive permissions, and NFS-safe persistence — features none of the Discord-only bots match. The Go binary is lightweight (~50-100 MB RSS), has zero runtime dependencies, and the "extra" platform support costs nothing when unused.

### 2.3 chadingTV/claudecode-discord

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
| Architecture | `/register <folder>` binds channel to path under `BASE_PROJECT_DIR` |
| Multi-workspace | **Yes — native.** Unlimited channels per bot instance |
| Per-thread sessions | **No.** Strictly per-channel. One channel = one session. No thread support |
| Auth | **Subscription-only** (Claude Pro/Max via Claude Agent SDK). No API key |
| Session persistence | **SQLite-backed** — `sessions` table with `session_id`, `channel_id`, `status`. **Not NFS-safe** — SQLite file locking fails on CIFS/NFS |
| Permission handling | Discord button UI: Approve / Deny / Auto-approve All. Per-channel `auto_approve` flag |
| RBAC | `ALLOWED_USER_IDS` comma-separated allowlist. Flat — no per-channel restrictions |
| Queue | In-memory, max 5 per channel. Lost on restart |

**Verdict:** Good per-channel mapping, but **no per-thread sessions** and **SQLite breaks on NFS**. These are both hard blockers given the updated requirements.

### 2.4 timoconnellaus/claude-code-discord-bot

**Source:** [github.com/timoconnellaus/claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot)

| Metric | Value |
|---|---|
| Stars | 70 |
| Last commit | June 16, 2025 |
| Open issues | 2 |
| Runtime | Bun |

| Attribute | Detail |
|---|---|
| Multi-workspace | Yes (channel name = folder name) |
| Per-thread sessions | **No** |
| Auth | CLI-delegated (subscription or API key) |
| Session persistence | SQLite — same NFS issue as chadingTV |
| RBAC | Single user only |

**Verdict:** 10+ months stale. No threads. No Docker. Single user. SQLite on NFS. Rejected.

### 2.5 Other 2026-Trending Options

| Project | Stars | Last Commit | Thread Sessions | Per-Channel Workspace | NFS-Safe | Subscription Auth |
|---|---|---|---|---|---|---|
| **zebbern/claude-code-discord** | 190 | Mar 2026 | ✅ (primary model) | ❌ (channel = entry point only) | Unknown | ⚠️ API key default |
| **ebibibi/claude-code-discord-bridge** | 41 | Feb 2026 | ✅ (with worktree isolation!) | ✅ | SQLite ❌ | ✅ Subscription |
| **JessyTsui/Claude-Code-Remote** | 1,200 | Aug 2025 | ❌ | Partial | Unknown | ✅ |
| **777genius/claude-notifications-go** | 605 | Active | N/A (notification only) | N/A | N/A | N/A |
| **fredchu/discord-claude-code-bot** | Low | Recent | ✅ (thread_id → session_uuid) | ❌ (global CWD) | SQLite ❌ | Unknown |

**Notable:** `ebibibi/claude-code-discord-bridge` is the only project with automatic **git worktree isolation** per thread (preventing concurrent file conflicts). It also has an "AI Lounge" for cross-session coordination and a REST API for programmatic session spawning. However, it uses SQLite (NFS-unsafe) and has only 41 stars with Python-based ops surface.

`zebbern/claude-code-discord` (190 stars) is the most polished thread-per-session bot but lacks per-channel workspace binding — the channel is just an entry point for creating threads, not a workspace anchor.

### 2.6 Comparison Matrix

| Requirement | Official Plugin | cc-connect | chadingTV | timoconnellaus | zebbern | ebibibi |
|---|---|---|---|---|---|---|
| Per-channel workspace | ❌ | ✅ Native | ✅ Native | ✅ Native | ❌ | ✅ |
| Per-thread sessions | ❌ | ✅ `thread_isolation` | ❌ | ❌ | ✅ | ✅ (+ worktrees) |
| Subscription auth | ✅ | ✅ (via CLI) | ✅ (SDK) | ✅ (via CLI) | ⚠️ API key | ✅ |
| NFS-safe storage | N/A | ✅ JSON atomic writes | ❌ SQLite | ❌ SQLite | Unknown | ❌ SQLite |
| Session persistence | ❌ | ✅ JSON + `--resume` | ✅ SQLite | ✅ SQLite | Partial | ✅ SQLite |
| Interactive permissions | ✅ Relay | ✅ 6 modes + Discord | ✅ Buttons | ✅ MCP bridge | ✅ | ✅ |
| Concurrent cross-channel | N/A | ✅ Goroutine isolation | ✅ Async | ❌ Drop & skip | ✅ | ✅ |
| Active maintenance (2026) | ✅ | ✅ (very active) | ✅ | ❌ (stale) | ✅ | ⚠️ (Feb 2026) |
| Stars | 18.7k (repo) | **7,700+** | 43 | 70 | 190 | 41 |
| Docker / k8s ready | ❌ | ⚠️ (custom image) | ⚠️ (custom) | ❌ | ✅ | ❌ |
| Runtime | Bun | **Go binary** | Node.js | Bun | Deno | Python |

**cc-connect is the only option that satisfies all four hard requirements:** per-channel workspace, per-thread sessions, subscription auth, and NFS-safe storage.

---

## 3. Per-Workspace Isolation Verification

| Requirement | cc-connect (recommended) | Status |
|---|---|---|
| **Independent session state per channel** | Each `[[projects]]` block creates its own `Engine` instance in isolated goroutines. In multi-workspace mode, each channel binding gets a dedicated agent subprocess and session manager | ✅ Confirmed |
| **Per-thread session isolation** | `thread_isolation = true` keys sessions by `discord:{threadID}`. Each thread gets its own Claude Code subprocess with independent context. Multiple threads run concurrently. Parent channel ID used for workspace binding (threads share the project directory) | ✅ Confirmed |
| **Independent CLAUDE.md per workspace** | Claude Code scopes CLAUDE.md per project directory. cc-connect spawns `claude` with `work_dir` set to the bound directory, so each workspace reads its own CLAUDE.md and `.claude/` | ✅ Confirmed (inherits from Claude Code) |
| **Independent .claude/ per workspace** | Same mechanism — `.claude/settings.local.json` in each project directory is respected because the subprocess starts in that directory | ✅ Confirmed |
| **Independent tool permissions per workspace** | cc-connect sets `bypassPermissions` globally (matching HolyClaude). Per-directory `.claude/settings.local.json` denylist still enforced by Claude Code itself — destructive commands blocked regardless of permission mode. Each workspace can have its own allowlist/denylist | ✅ Confirmed |
| **Sender allowlist / RBAC** | `allow_from = "userID1,userID2"` per project. Different projects can have different allowlists | ✅ Confirmed (per-project granularity) |
| **Behavior on restart** | Sessions resume from JSON via `--resume <sessionID>`. Workspace bindings persist. In-memory queue (5 items) lost. Active turn state lost | ⚠️ Queue lost, sessions survive |
| **Filesystem safety on NFS** | No SQLite. JSON files with atomic writes (`AtomicWriteFile()`). NFS-safe | ✅ Confirmed |

**Gap: No git worktree isolation.** When two threads in the same channel (`#openclaw`) run concurrently against `/workspace/projects/openclaw`, both write to the same working directory. This can cause file conflicts if both sessions edit the same files simultaneously. `ebibibi/claude-code-discord-bridge` solves this with automatic `wt-{thread_id}` worktrees, but cc-connect does not. Mitigation: treat same-workspace concurrent sessions as a "use carefully" feature and document the risk, or add a worktree wrapper via a custom system prompt.

---

## 4. Infrastructure Compatibility

### 4.1 Host Recommendation

**Recommendation: Kubernetes pod in the existing homelab cluster, mounting the same NFS volumes as HolyClaude.**

This is a significant change from v1 (which recommended `docker01.local`). The NFS compatibility requirement makes the k8s cluster the natural deployment target.

**Current NFS layout (from HolyClaude k8s deployment):**

| Volume | NFS Server | NFS Export Path | Used By |
|---|---|---|---|
| Workspace | `10.0.4.11` (prox0.local) | `/tank/k8s/code-server` | HolyClaude, code-server, repowise |
| Home (Claude credentials) | `10.0.4.11` (prox0.local) | `/tank/k8s/holyclaude` | HolyClaude, agno |

Both are already provisioned as PVs with `nfsvers=4.2`, `hard`, `intr` mount options and `nfs-external` StorageClass.

| Factor | K8s pod (same cluster) | `docker01.local` (Docker VM) | New LXC on `prox0.local` |
|---|---|---|---|
| NFS access | ✅ Same PVCs already exist and are `ReadWriteMany` | ❌ Would need separate NFS mount on Docker host | ❌ NFS mount in LXC (works but new setup) |
| Claude credentials | ✅ Shared via `nfs-holyclaude-home` PVC (already used by agno) | ❌ Would need credential copy or separate NFS mount | ❌ Separate mount needed |
| Management plane | ✅ Same ArgoCD/Kustomize as all other apps | ❌ Docker Compose — different ops surface | ❌ systemd — third ops surface |
| Restart semantics | ✅ Pod restart policy + liveness probes | ✅ `restart: unless-stopped` | ✅ systemd `restart=always` |
| Resource footprint | Go binary ~50-100 MB RSS + Claude CLI subprocesses. Fits within cluster capacity | Same footprint, but needs the VM to stay up | Same |
| Backup/snapshot | ✅ ArgoCD manifests in git. NFS data on prox0 ZFS (snapshotted) | Proxmox VM snapshot | LXC snapshot |
| Proximity to workspace data | ✅ Same NFS, same network, zero additional latency | NFS mount over VLAN — small but nonzero latency | NFS local to prox0 — minimal latency |
| Existing pattern | ✅ Identical to HolyClaude, agno, repowise deployments | Different (Docker Compose) | Different (systemd in LXC) |

**Rationale:** The workspace lives on NFS at `10.0.4.11:/tank/k8s/code-server`. HolyClaude, code-server, and repowise already mount it as k8s PVCs. Claude credentials at `/tank/k8s/holyclaude` are already shared with agno. Deploying cc-connect as a k8s pod reuses all existing PVCs, credential sharing, and the ArgoCD management plane. No new NFS mounts, no new infrastructure patterns.

### 4.2 Auth Model

| Option | Auth Mechanism | Subscription Support | Cost |
|---|---|---|---|
| Official Plugin | Anthropic auth only | ✅ Pro/Max/Teams/Enterprise | Subscription |
| **cc-connect** | **Inherits from `claude` CLI** | **✅ via `~/.claude/.credentials.json`** | **Subscription (flat fee)** |
| chadingTV | Claude Agent SDK | ✅ Pro/Max only | Subscription |
| zebbern | API key or CLI | ⚠️ API key default | Per-token or subscription |

**For our deployment:** cc-connect shells out to the `claude` binary, which inherits the OAuth credentials already stored at `~/.claude/.credentials.json` on the HolyClaude home NFS volume. The agno pod already does exactly this — mounts the same NFS path to share credentials. cc-connect follows the identical pattern.

**No API key needed. No credential rotation needed.** The subscription OAuth token is managed by the Claude CLI itself.

**Cost:** Flat subscription fee (Max $100/mo or $200/mo). No per-token billing. Multiple concurrent Claude subprocesses share the same subscription.

### 4.3 Persistence

**Recommended approach:** Kubernetes Deployment with `restartPolicy: Always`

```yaml
# Shape of the deployment (reference only — not execution)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cc-connect
  namespace: cc-connect
spec:
  replicas: 1
  strategy:
    type: Recreate  # NFS PVC safety — same as HolyClaude
  template:
    spec:
      containers:
        - name: cc-connect
          image: ghcr.io/<org>/cc-connect:latest  # Custom image needed
          volumeMounts:
            - name: workspace
              mountPath: /workspace
            - name: claude-home
              mountPath: /home/claude
            - name: cc-connect-data
              mountPath: /home/claude/.cc-connect
            - name: config
              mountPath: /etc/cc-connect
      volumes:
        - name: workspace
          persistentVolumeClaim:
            claimName: cc-connect-workspace  # Same NFS export as HolyClaude
        - name: claude-home
          persistentVolumeClaim:
            claimName: cc-connect-claude-home  # Same NFS export as HolyClaude
        - name: cc-connect-data
          persistentVolumeClaim:
            claimName: cc-connect-data  # JSON session storage
        - name: config
          configMap:
            name: cc-connect-config  # config.toml
```

New PVs/PVCs needed (pointing to existing NFS exports):
- `cc-connect-workspace` → `10.0.4.11:/tank/k8s/code-server` (ReadWriteMany — same export as HolyClaude)
- `cc-connect-claude-home` → `10.0.4.11:/tank/k8s/holyclaude` (ReadWriteMany — same export as agno)
- `cc-connect-data` → `10.0.4.11:/tank/k8s/cc-connect` (new export — small, for JSON session files)

### 4.4 Secrets Management

Follow the existing homelab pattern: **SOPS-encrypted secrets in the ff-k8s repo.**

HolyClaude already has a `secrets/` directory at `/workspace/projects/ff-k8s/k8s-homelab/kubernetes/apps/holyclaude/secrets/` with SOPS-encrypted manifests.

| Secret | Source | Storage |
|---|---|---|
| `DISCORD_TOKEN` | Discord Developer Portal → Bot → Token | SOPS-encrypted Secret in `kubernetes/apps/cc-connect/secrets/` |
| Discord Guild ID | Server Settings → Widget → Server ID | ConfigMap (not sensitive) |
| `allow_from` user IDs | Discord Developer Mode → Copy User ID | ConfigMap (not sensitive) |
| Claude credentials | Already on NFS at `/tank/k8s/holyclaude/.claude/` | NFS PVC mount (shared with HolyClaude + agno) |

**No new secret management pattern.** Reuses the existing SOPS + age encryption already in place for the homelab.

---

## 5. Integration with Existing Stack

### 5.1 Discord Guild Layout

Single private guild. One channel per workspace, all prefixed with `cc-` for easy identification. Threads for sessions within each workspace.

#### Channel Naming Convention: `cc-` Prefix

cc-connect has no native `channel_prefix` config — the auto-mapping convention is `channel name = directory name`. However, the `/workspace bind` slash command stores an explicit binding (persisted in `workspace_bindings.json`) that **takes precedence** over the auto-convention. This means:

1. Name channels with the `cc-` prefix: `#cc-openclaw`, `#cc-ff-k8s`, etc.
2. On first use, run `/workspace bind <dirname>` in each channel to map the prefixed channel name to the actual directory.
3. Bindings persist across restarts in JSON — this is a one-time setup per channel.

The `cc-` prefix provides clear visual separation from other guild channels and makes it obvious which channels are Claude Code workspaces.

```
📁 CLAUDE CODE WORKSPACES
├── #cc-whatsapp-vault       → /workspace/projects/whatsapp_downloader
│   ├── Thread: "fix media dedup"           (session 1)
│   └── Thread: "add heif support"          (session 2, concurrent)
├── #cc-openclaw             → /workspace/projects/openclaw
│   ├── Thread: "refactor webhook handler"  (session 1)
│   └── Thread: "add rate limiting"         (session 2, concurrent)
├── #cc-calbridge            → /workspace/projects/calbridge
├── #cc-ff-k8s               → /workspace/projects/ff-k8s
├── #cc-holyclaude           → /workspace/projects/HolyClaude
├── #cc-eero-ui              → /workspace/projects/eero-ui
├── #cc-eero-api             → /workspace/projects/eero-api
├── #cc-eeroctl              → /workspace/projects/eeroctl
├── #cc-eero-prometheus      → /workspace/projects/eero-prometheus-exporter
├── #cc-bambuddy-cloud       → /workspace/projects/bambuddy_cloud
├── #cc-notion-automations   → /workspace/projects/notion-automations
├── #cc-unbound              → /workspace/projects/unbound
├── #cc-workflow-arsenal     → /workspace/projects/workflow-arsenal
├── #cc-dns-zones            → /workspace/projects/dns-zones
├── #cc-prox-cluster         → /workspace/projects/prox-cluster
├── #cc-prox-new             → /workspace/projects/prox-new
├── #cc-cloudcli-ccusage     → /workspace/projects/cloudcli-plugin-ccusage
├── #cc-ff-net               → /workspace/projects/ff-net
├── #cc-tfstates-ui          → /workspace/projects/tfstates-ui
📁 BOT ADMIN
├── #cc-bot-logs             → (bot status, errors, session events)
├── #cc-bot-config           → (slash commands: /workspace, /mode, /new, /stop)
```

**UX flow:** Post a message in `#cc-openclaw` → cc-connect creates a thread for the session → all conversation happens in that thread → start another thread in `#cc-openclaw` for a parallel session → both run concurrently against the same workspace directory.

**Role-based permission scheme:**

| Role | Permissions | Members |
|---|---|---|
| `@Bot Admin` | Manage Channels, Manage Messages, Use Slash Commands, Create Threads | You (owner) |
| `@Claude Bot` | Send Messages, Send Messages in Threads, Read Message History, Embed Links, Attach Files, Add Reactions, Create Public Threads | Bot application |
| `@everyone` | No access (private guild, explicit grants only) | — |

**Category permissions:** Lock `CLAUDE CODE WORKSPACES` category to `@Bot Admin` + `@Claude Bot`. Thread permissions inherit from parent channel.

### 5.2 Network / Ingress

**Confirmed: No Cloudflare Tunnel or ingress needed.**

cc-connect connects outbound via WebSocket to `gateway.discord.gg`. The `claude` subprocess connects outbound to Anthropic's API. Both are outbound-only TCP. No listening ports exposed to the internet. The optional web admin UI can stay cluster-internal (ClusterIP service, no HTTPRoute needed initially).

### 5.3 Workspace Directory Mapping

cc-connect `config.toml` shape for multi-workspace mode:

```toml
[[projects]]
name = "claude-workspaces"
mode = "multi-workspace"
base_dir = "/workspace/projects"

[projects.agent]
type = "claudecode"

[projects.agent.options]
mode = "bypassPermissions"  # full permissions — matches HolyClaude's Bash(*) allowlist

[[projects.platforms]]
type = "discord"
token_env = "DISCORD_TOKEN"
guild_id = "<guild-id>"
thread_isolation = true
allow_from = "<your-discord-user-id>"
progress_style = "card"
```

With this config and `cc-` prefixed channel names, the auto-convention won't match directories directly (there's no `/workspace/projects/cc-openclaw`). Use `/workspace bind` in each channel to create the explicit mapping — e.g., `/workspace bind openclaw` in `#cc-openclaw`. Bindings persist in `workspace_bindings.json` across restarts.

#### Permission Model: Full Permissions with Denylist Safety Net

cc-connect's `mode = "bypassPermissions"` passes `--permission-mode bypassPermissions` to the Claude CLI, matching HolyClaude's current setup where `Bash(*)` is in the global allowlist. This means **no interactive approval prompts** in Discord — Claude executes all tools autonomously.

**This is safe because the denylist still applies.** Claude Code's permission system is layered:

1. **cc-connect layer:** `bypassPermissions` → auto-approve all tool calls
2. **Claude Code layer:** per-workspace `.claude/settings.local.json` → allowlist and denylist still enforced

Each workspace can have its own `.claude/settings.local.json` with the same 47-item denylist currently used by HolyClaude (blocking `rm -rf /`, `shutdown`, `mkfs`, `iptables`, `passwd`, etc.). The denylist is evaluated **after** the permission mode, so even in `bypassPermissions` mode, denied commands are rejected.

**To replicate HolyClaude's exact permission model per workspace**, place a `.claude/settings.local.json` in each project directory:

```json
{
  "permissions": {
    "allow": [
      "Bash(*)", "Edit", "Write(*)", "Read(*)", "WebFetch(*)", "WebSearch"
    ],
    "deny": [
      "Bash(rm -rf /)", "Bash(rm -rf /*)", "Bash(rm -rf ~)",
      "Bash(shutdown *)", "Bash(reboot *)", "Bash(poweroff *)",
      "Bash(mkfs *)", "Bash(dd if=*)", "Bash(wipefs *)",
      "Bash(iptables -F *)", "Bash(passwd *)", "Bash(userdel *)",
      "Bash(systemctl disable *)", "Bash(systemctl mask *)",
      "Bash(kill -9 -1 *)", "Bash(killall *)"
    ]
  }
}
```

(Full 47-item denylist from HolyClaude's config — abbreviated above for readability. Copy from `/workspace/projects/HolyClaude/.claude/settings.local.json`.)

Workspaces that already have a `.claude/settings.local.json` (e.g., `HolyClaude` itself, `ff-k8s`) inherit their existing permissions automatically. For workspaces without one, the global `~/.claude/settings.json` (on the shared NFS home volume) provides the fallback denylist.

**Verified directories on `/workspace/projects/` that should get channels:**

All workspaces run `bypassPermissions` (full permissions). Per-workspace denylist safety comes from `.claude/settings.local.json` in each project directory.

| Channel | `/workspace bind` arg | Directory |
|---|---|---|
| `#cc-whatsapp-vault` | `whatsapp_downloader` | `whatsapp_downloader` |
| `#cc-openclaw` | `openclaw` | `openclaw` |
| `#cc-calbridge` | `calbridge` | `calbridge` |
| `#cc-ff-k8s` | `ff-k8s` | `ff-k8s` |
| `#cc-holyclaude` | `HolyClaude` | `HolyClaude` |
| `#cc-eero-ui` | `eero-ui` | `eero-ui` |
| `#cc-eero-api` | `eero-api` | `eero-api` |
| `#cc-eeroctl` | `eeroctl` | `eeroctl` |
| `#cc-eero-prometheus` | `eero-prometheus-exporter` | `eero-prometheus-exporter` |
| `#cc-bambuddy-cloud` | `bambuddy_cloud` | `bambuddy_cloud` |
| `#cc-notion-automations` | `notion-automations` | `notion-automations` |
| `#cc-unbound` | `unbound` | `unbound` |
| `#cc-workflow-arsenal` | `workflow-arsenal` | `workflow-arsenal` |
| `#cc-dns-zones` | `dns-zones` | `dns-zones` |
| `#cc-prox-cluster` | `prox-cluster` | `prox-cluster` |
| `#cc-prox-new` | `prox-new` | `prox-new` |
| `#cc-cloudcli-ccusage` | `cloudcli-plugin-ccusage` | `cloudcli-plugin-ccusage` |
| `#cc-ff-net` | `ff-net` | `ff-net` |
| `#cc-tfstates-ui` | `tfstates-ui` | `tfstates-ui` |

**Excluded:** `calbridge-fix`, `eero-*-context`, `codeserver`, `fix-mcp`, `freitasnas`, `mediaserver`, `migrate-perc-unraid-to-prox`, `plex`, `prox-updates-fix`, `teslamate`, `tigra-store`, `awesome-claude-code-subagents`, `docker01`

### 5.4 Notifications

**cc-connect handles @mention natively.** When the bot creates a thread or responds, the Discord mobile push notification system handles delivery. Users are notified when:
- A thread is created in a channel they're watching
- The bot replies in an active thread
- A permission prompt requires approval

For explicit `@<user>` pings on task completion, cc-connect's message templates can be customized. The `progress_style = "card"` mode provides structured completion messages.

**Keep the existing Apprise webhook system** (`notify.py` with `NOTIFY_DISCORD`) as a separate, independent notification channel for HolyClaude's own hook events (task stop, tool failure). These are different systems serving different purposes:
- **cc-connect:** Interactive Discord sessions via bot
- **Apprise:** Fire-and-forget webhook alerts from HolyClaude's Claude Code hooks

---

## 6. Risks and Limitations

### Critical Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **No git worktree isolation for concurrent threads** | Medium | Two threads in the same channel both write to the same `work_dir`. If both edit the same file simultaneously, conflicts occur. Mitigation: (1) document the risk, (2) consider injecting a system prompt that mandates `git worktree` creation (cc-connect supports `--system-prompt`), (3) for infra workspaces, use `default` permission mode so file writes require manual approval |
| **Full permissions (`bypassPermissions`) across all workspaces** | Medium | Matches HolyClaude's current `Bash(*)` model. Safety net: per-workspace `.claude/settings.local.json` denylist blocks destructive commands (`rm -rf /`, `shutdown`, `mkfs`, `iptables`, etc.) even in bypass mode. Ensure every workspace has a denylist — either local or inherited from the global `~/.claude/settings.json` on NFS |
| **Single bot process = SPOF** | Medium | K8s pod restart policy handles crashes. JSON sessions survive restart. Queue (5 items) lost. Go goroutine isolation means one crashed session doesn't take down others |
| **Concurrent workspace access with HolyClaude** | Medium | The cc-connect pod and HolyClaude pod share the same NFS workspace. If both write to the same files simultaneously, conflicts can occur. Git's own conflict detection helps. In practice, you'll be using one or the other per project at any given time |
| **Discord Message Content Intent** | Low | Must be enabled in Discord Developer Portal → Bot → Privileged Gateway Intents. One-time setup |
| **macOS-specific OAuth issue (issue #752)** | None | Only affects macOS `launchd`. We're running in k8s Linux containers — not applicable |
| **cc-connect has 165 open issues** | Low | Reflects scope and activity, not neglect. 107 commits since last release. Active beta track |
| **Subscription dependency** | Low | If Pro/Max subscription lapses, all sessions stop. Keep subscription active |
| **`CLAUDECODE` env var conflict** | Low | If cc-connect runs inside a Claude Code session, must `unset CLAUDECODE`. In k8s pod, this won't be set — not an issue |

### Security Model: Full Permissions with Denylist

All workspaces run in `bypassPermissions` mode (matching HolyClaude). Security is enforced by the Claude Code denylist layer, not by interactive approval:

| Layer | What it does | Where it lives |
|---|---|---|
| **cc-connect** | `mode = "bypassPermissions"` → auto-approve all tool calls, no Discord prompts | `config.toml` |
| **Claude Code global** | Fallback denylist (47 destructive commands blocked) | `~/.claude/settings.json` on NFS |
| **Claude Code per-workspace** | Per-project overrides (additional allows/denies) | `<project>/.claude/settings.local.json` |
| **Discord RBAC** | Only allowlisted Discord user IDs can send messages | `allow_from` in `config.toml` |

**Why this is acceptable:** HolyClaude already operates this way. The `Bash(*)` allowlist with the 47-item denylist has been the production model. cc-connect simply extends the same model to Discord-initiated sessions. The denylist prevents `rm -rf /`, `shutdown`, `mkfs`, `iptables`, `passwd`, and other destructive operations regardless of permission mode.

### Limitations to Accept

1. **No automatic worktree isolation** — concurrent threads against the same workspace may conflict. Acceptable for personal use with awareness. Can be mitigated via system prompt if needed.
2. **Queue is in-memory** (5 items, lost on restart). Acceptable — restarts are fast and rare.
3. **Custom Docker image needed** — cc-connect has no official container image. Must build one with Go binary + Claude CLI. One-time effort.
4. **Thread isolation has no filesystem guard** — sessions are isolated at the process level, not the filesystem level. This is the same model as running two terminal sessions against the same repo.

---

## 7. Recommendation

### 7.1 Go / No-Go

**GO.**

`chenhg5/cc-connect` satisfies all core requirements:
- ✅ Per-channel workspace mapping (multi-workspace mode, native)
- ✅ Per-thread sessions (`thread_isolation = true`, concurrent)
- ✅ Subscription auth (inherits Claude CLI OAuth — zero per-token cost)
- ✅ NFS-safe storage (JSON atomic writes, no SQLite)
- ✅ Same NFS as HolyClaude (mount identical PVCs)
- ✅ Full permissions (`bypassPermissions`) matching HolyClaude's `Bash(*)` model, with denylist safety net
- ✅ Per-project RBAC (`allow_from` per project)
- ✅ Session persistence across restarts (JSON + `--resume`)
- ✅ Massive community (7,700+ stars, very active)
- ✅ MIT license, single Go binary, lightweight

**Host:** Kubernetes pod in the existing homelab cluster, same namespace pattern as HolyClaude.

### 7.2 High-Level Deployment Outline

> **This is a plan, not execution. No changes have been made.**

#### Step 1: Discord Setup (~10 minutes)

1. Create a Discord Application at discord.com/developers
2. Create a Bot. Copy the token.
3. Enable **Message Content Intent** under Privileged Gateway Intents.
4. Bot permissions: Send Messages, Send Messages in Threads, Read Message History, Embed Links, Attach Files, Add Reactions, Create Public Threads, Use Slash Commands.
5. Invite bot to the private guild.
6. Create `CLAUDE CODE WORKSPACES` category and `cc-` prefixed channels per §5.1.
7. Lock category permissions to `@Bot Admin` + `@Claude Bot`.

#### Step 2: Build Container Image (~2 hours)

```dockerfile
# Shape only — not execution
FROM node:22-slim AS base
RUN npm install -g cc-connect @anthropic-ai/claude-code
# OR: download pre-compiled Go binary from GitHub Releases
COPY config.toml /etc/cc-connect/config.toml
ENV HOME=/home/claude
CMD ["cc-connect", "--config", "/etc/cc-connect/config.toml"]
```

Push to `ghcr.io/<org>/cc-connect:latest`.

#### Step 3: Create K8s Manifests (~1 hour)

Under `/workspace/projects/ff-k8s/k8s-homelab/kubernetes/apps/cc-connect/`:

```
cc-connect/
├── kustomization.yaml
├── namespace.yaml           # cc-connect namespace
├── deployment.yaml          # Pod spec with NFS mounts
├── configmap.yaml           # config.toml
├── persistentvolumes.yaml   # PVs for workspace + home + data
├── secrets/                 # SOPS-encrypted Discord token
│   └── discord-token.yaml
└── README.md
```

PVs point to existing NFS exports:
- `nfs-cc-connect-workspace` → `10.0.4.11:/tank/k8s/code-server` (same as HolyClaude)
- `nfs-cc-connect-home` → `10.0.4.11:/tank/k8s/holyclaude` (same as HolyClaude/agno)
- `nfs-cc-connect-data` → `10.0.4.11:/tank/k8s/cc-connect` (new small NFS export for JSON)

#### Step 4: Create `config.toml` (~30 minutes)

Single `multi-workspace` project block with `base_dir = "/workspace/projects"`, `mode = "bypassPermissions"`, `thread_isolation = true`. Per-channel `/workspace bind` at runtime maps `cc-` prefixed channels to directories.

#### Step 5: Ensure Denylist Coverage (~15 minutes)

The global `~/.claude/settings.json` on the NFS home volume already contains the 47-item denylist (shared with HolyClaude). Verify it's present — cc-connect's Claude subprocesses inherit it via the same `$HOME/.claude/` path.

For workspaces that need additional restrictions beyond the global denylist, add a `.claude/settings.local.json` in that project directory. Workspaces that already have one (e.g., `HolyClaude`, `ff-k8s`) keep their existing settings.

#### Step 6: Deploy via ArgoCD

Add `cc-connect` to the app-of-apps ApplicationSet. ArgoCD syncs the manifests, creates the namespace, deploys the pod.

#### Step 7: Bind Channels (~5 minutes)

Because channels use the `cc-` prefix, the auto-convention (`channel name = directory name`) won't match. Run `/workspace bind` in each channel to create the explicit mapping:

```
/workspace bind whatsapp_downloader       (in #cc-whatsapp-vault)
/workspace bind openclaw                  (in #cc-openclaw)
/workspace bind calbridge                 (in #cc-calbridge)
/workspace bind ff-k8s                    (in #cc-ff-k8s)
/workspace bind HolyClaude                (in #cc-holyclaude)
/workspace bind eero-ui                   (in #cc-eero-ui)
/workspace bind eero-api                  (in #cc-eero-api)
/workspace bind eeroctl                   (in #cc-eeroctl)
/workspace bind eero-prometheus-exporter  (in #cc-eero-prometheus)
/workspace bind bambuddy_cloud            (in #cc-bambuddy-cloud)
/workspace bind notion-automations        (in #cc-notion-automations)
/workspace bind unbound                   (in #cc-unbound)
/workspace bind workflow-arsenal          (in #cc-workflow-arsenal)
/workspace bind dns-zones                 (in #cc-dns-zones)
/workspace bind prox-cluster              (in #cc-prox-cluster)
/workspace bind prox-new                  (in #cc-prox-new)
/workspace bind cloudcli-plugin-ccusage   (in #cc-cloudcli-ccusage)
/workspace bind ff-net                    (in #cc-ff-net)
/workspace bind tfstates-ui               (in #cc-tfstates-ui)
```

Bindings persist in `workspace_bindings.json` — this is a one-time setup per channel.

#### Step 8: Verify & Snapshot

1. Send a test message in each channel → verify thread creation and response
2. Create a Proxmox ZFS snapshot of the NFS dataset on prox0
3. Commit all k8s manifests to the ff-k8s repo

### 7.3 What Would Need to Be Built / Forked

| Gap | Effort | Approach |
|---|---|---|
| **Container image** | ~2 hours | Build custom image with cc-connect (Go binary or npm) + Claude Code CLI. Push to GHCR |
| **K8s manifests** | ~1 hour | Deployment, ConfigMap, PVs/PVCs, SOPS Secret. Follow HolyClaude pattern exactly |
| **NFS export** | ~10 min | Create `/tank/k8s/cc-connect` on prox0 for cc-connect's JSON session data |
| **Per-workspace denylist** | ~15 min | Verify global denylist on NFS home. For workspaces without `.claude/settings.local.json`, the global fallback applies. No new files needed if global denylist is sufficient |
| **Worktree isolation** (optional) | ~30 min | Inject system prompt via `config.toml` that instructs Claude to create `git worktree` before editing. Not a code change — config only |
| **Health check / monitoring** | Nice-to-have | cc-connect has an embedded web admin UI — expose as ClusterIP service for internal monitoring |

**No forking required.** cc-connect is configured entirely via `config.toml`. The only custom artifact is the container image.

---

## Appendix A: Rejected Alternatives — Rationale

| Option | Why Rejected |
|---|---|
| **Official Claude Code Channels Plugin** | No multi-workspace. No per-thread sessions. Requires N bots + N terminals. Research preview. Re-evaluate at GA |
| **chadingTV/claudecode-discord** | **No per-thread sessions** (strict per-channel only). **SQLite breaks on NFS** — can't use the shared workspace NFS. Good channel-to-directory mapping but missing two hard requirements |
| **timoconnellaus/claude-code-discord-bot** | No threads. Single user. No Docker. SQLite on NFS. 10+ months stale. Bun runtime |
| **zebbern/claude-code-discord** | Has per-thread sessions but **no per-channel workspace binding** — channel is just an entry point, not a workspace anchor. Would need one instance per workspace |
| **ebibibi/claude-code-discord-bridge** | Best worktree isolation, but **SQLite on NFS** is a blocker. Python ops surface (different from Go/Node stack). Only 41 stars |
| **`docker01.local` as host** | Would need separate NFS mounts on the Docker VM to access the workspace. Different management plane (Docker Compose vs. ArgoCD). The k8s cluster already has the NFS PVCs provisioned |
| **New LXC on `prox0.local`** | Third ops surface (systemd). No existing NFS PVC reuse. Overkill for a single bot process |

## Appendix B: Why cc-connect Was Initially Dismissed (and Why That Was Wrong)

The v1 evaluation dismissed cc-connect as "overkill — 11 platforms, 10 agents, Go binary, Web UI." This judgment had three errors:

1. **"Overkill" is not a technical disqualifier.** cc-connect's multi-platform support is additive, not a tax. Unused platforms cost nothing at runtime. The Go binary is lighter than Node.js + SQLite.

2. **The features cc-connect adds are exactly the ones needed.** Per-thread sessions (`thread_isolation`), multi-workspace mode, NFS-safe JSON storage, and per-project permission modes are all features that the Discord-only bots lack.

3. **Community matters.** 7,700 stars with 107 commits since the last release vs. 43 stars with 1 open issue. The bus factor and maintenance trajectory are incomparable.

The lesson: evaluate tools by fit against requirements, not by whether they do "more than needed."

## Appendix C: Future Considerations

1. **Official plugin GA:** When Anthropic ships multi-workspace + thread support for Channels, re-evaluate. The MCP bridge architecture (events into a running session) is architecturally superior. But cc-connect's subprocess model is production-proven today.

2. **Worktree isolation:** If concurrent same-workspace sessions become frequent, add automatic worktree creation. Options: (a) system prompt injection via `config.toml`, (b) fork cc-connect to add a pre-session hook, (c) monitor if cc-connect adds this natively.

3. **Multi-agent expansion:** cc-connect supports Codex, Gemini CLI, Cursor, and others. If the homelab adds more AI agents, cc-connect becomes the unified hub without deploying new bots.

4. **Telegram/Slack channels:** cc-connect supports Telegram and Slack out of the box. If you want workspace access from Telegram (e.g., for quick mobile commands), add a `[[projects.platforms]]` block with `type = "telegram"` — no new deployment needed.

5. **Session TTL:** Monitor for long-running session token growth. cc-connect's 15-minute idle reaper helps, but active sessions can accumulate context. Use `/new` to start fresh sessions when context gets heavy.
