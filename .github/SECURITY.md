# Security Policy

## Overview

HolyClaude runs AI coding agents inside a Docker container with elevated capabilities. This document explains the security model, what the container can access, and how to report vulnerabilities.

## Container Capabilities

HolyClaude requires the following Docker capabilities:

| Capability | Why | Risk |
|-----------|-----|------|
| `SYS_ADMIN` | Chromium sandboxing (Linux namespaces) | Standard for any Chromium-in-Docker setup |
| `SYS_PTRACE` | Debugging tools (strace, lsof) | Allows process inspection within the container |
| `seccomp=unconfined` | Chromium syscall requirements | Removes syscall filtering for the container |

These are required for Chromium to function and are standard across Playwright, Puppeteer, and CI/CD browser testing setups. They do **not** grant the container access to the host system beyond what Docker normally allows.

## Permission Modes

| Mode | Default? | What it means |
|------|----------|--------------|
| `allowEdits` | **Yes** | Claude can edit files freely, asks before running shell commands |
| `bypassPermissions` | No | Claude runs any command without confirmation |

The default `allowEdits` mode is safe for most users. `bypassPermissions` is documented for power users who understand the implications.

## Credential Storage

- API keys and authentication tokens are stored in `./data/claude/` on the host (bind-mounted to `~/.claude/` in the container)
- Credentials never leave the container — HolyClaude does not proxy, intercept, or transmit credentials to any third party
- The container communicates directly with AI provider APIs (Anthropic, Google, OpenAI) using your credentials

## Network Access

The container has unrestricted outbound network access. This is required for:
- AI provider API calls (Anthropic, Google, OpenAI)
- npm/pip package installations
- Git operations (clone, push, pull)
- Any web requests Claude Code makes during development tasks

## Exposing HolyClaude to the Internet

**Do not port-forward HolyClaude to the public internet.** CloudCLI exposes a full shell and holds your AI provider credentials. A simple password is not sufficient protection — basic auth gets brute-forced, and one compromise means an attacker has arbitrary code execution, access to your workspace, and a paid Claude Code instance running on your credentials.

If you need to reach HolyClaude from outside your local network, use:

- **[Tailscale](https://tailscale.com)** — WireGuard mesh VPN, zero open ports, identity-based auth. Recommended for personal and small-team use.
- **[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)** — Outbound-only tunnel to Cloudflare's edge, optional Cloudflare Access SSO in front. Recommended when you need a public hostname or shared access.

Both options are free for personal use, encrypt the connection end-to-end, and never require opening a port on your router. See the [Remote Access & Exposure](../README.md#shield-remote-access--exposure) section of the README for full details.

## Reporting a Vulnerability

If you discover a security vulnerability in HolyClaude:

1. **Do not** open a public GitHub issue
2. Use [GitHub Security Advisories](https://github.com/CoderLuii/HolyClaude/security/advisories/new) to report privately
3. Include: description, steps to reproduce, and potential impact
4. You will receive a response within 48 hours

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest | Yes |
| < 1.0.0 | No |
