# Changelog

## 1.1.0

- One-command bootstrap; installs the native bridge, Codex MCP configuration and application launcher.
- Dedicated Firefox profile with automatic temporary extension loading at startup.
- Versioned runtime installation, configuration backups and rollback on failed installation checks.
- `firefox-agent` commands for start, status, tabs, stop, diagnosis, update and uninstall.
- Updates preserve the profile. Uninstall preserves browser data unless `--purge` is explicit.
- Linux and macOS installer paths, tests, GitHub CI and release archives with SHA-256 checksums.
- Closing and navigating an authorized tab works during transient page loading states.

## 1.0.0

- Firefox extension, local Unix-socket native host, CLI and 20-tool MCP server.
- Tab grants, emergency stop, snapshots with element IDs, forms, iframe and open Shadow DOM support.
