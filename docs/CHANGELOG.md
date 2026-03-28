# Changelog

All notable changes to HolyClaude will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.4] - 03/28/2026

### Added
- Azure CLI (`az`) in full variant
- Ollama setup documentation (`docs/ollama.md`) for running HolyClaude with local or cloud models without an Anthropic subscription

## [1.1.3] - 03/27/2026

### Added
- Junie CLI (JetBrains AI coding agent) in full variant
- OpenCode CLI (open source AI coding agent) in full variant
- Environment variable passthrough to CloudCLI for AI provider keys, timezone, and display (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `OLLAMA_HOST`, `TZ`, `DISPLAY`, etc.)

### Fixed
- Web Terminal plugin stuck on "Connecting..." spinner (WebSocket frame type not preserved in plugin proxy, both relay directions patched)
- `NODE_OPTIONS` from Docker Compose now correctly merged with internal flags instead of being silently overridden
- `TZ` and `DISPLAY` environment variables now properly forwarded to CloudCLI process
- Default permission mode corrected from `allowEdits` to `acceptEdits` in settings.json

Thanks to [@RobertWalther](https://github.com/RobertWalther) for the WebSocket fix and [@kewogc](https://github.com/kewogc) for reporting the settings error.

## [1.1.2] - 03/26/2026

### Added
- Docker HEALTHCHECK instruction for container health monitoring
- Bootstrap now backs up existing `settings.json` and `CLAUDE.md` before overwriting on re-bootstrap
- Expanded CONTRIBUTING.md with build commands, testing steps, file map, and PR checklist

## [1.1.1] - 03/26/2026

### Fixed
- Workspace bind mount permissions on first run when Docker creates the directory as root
- Workspace directory now tracked via `.gitkeep` to prevent root ownership on fresh clones

### Added
- Configurable host-side port and bind-mount paths via `.env` file (`HOLYCLAUDE_HOST_PORT`, `HOLYCLAUDE_HOST_CLAUDE_DIR`, `HOLYCLAUDE_HOST_WORKSPACE_DIR`)

Thanks to [@Sunwood-ai-labs](https://github.com/Sunwood-ai-labs) for this contribution.

## [1.1.0] - 03/25/2026

### Added
- Apprise notification engine with support for 100+ services (Discord, Telegram, Slack, Email, Gotify, and more)
- Individual `NOTIFY_*` environment variables for easy per-service configuration
- Catch-all `NOTIFY_URLS` for any Apprise-supported service

### Changed
- Notification backend replaced from Pushover to Apprise

### Removed
- **BREAKING:** `PUSHOVER_APP_TOKEN` and `PUSHOVER_USER_KEY` environment variables removed. Migrate to `NOTIFY_PUSHOVER=pover://user_key@app_token`. See [configuration docs](configuration.md#notifications-apprise) for details.

## [1.0.0] - 03/21/2026

Initial public release.
