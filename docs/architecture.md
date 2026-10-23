# Architecture

```text
Codex / MCP client
       │ stdio JSON-RPC
       ▼
Python MCP server ────── firefox-agent CLI
       │ private Unix socket (same local user)
       ▼
Python Native Messaging host
       │ Firefox native message framing
       ▼
Extension background script
       │ checks tab grant + command deadline
       ▼
Packaged content script / Firefox tabs API
```

The launcher briefly creates a loopback Marionette connection to load the temporary extension into a dedicated profile, authorize the startup tab and verify readiness. The listener is closed immediately after the session is established. The bootstrap connection is then closed; ongoing commands use the native bridge.

The extension serializes ordinary commands. Emergency stop bypasses that queue and changes a generation token so old commands cannot run after a revoke/regrant cycle. The MCP server handles requests concurrently so a pending wait does not block the same client's stop tool.

Each content-script document gets a random token. Snapshot element IDs belong to that document and the latest snapshot. Mutations check connectivity, labels/link fingerprints, visibility, enabled state and coverage as appropriate. Navigation expires document IDs. A timeout is reported without automatically repeating an action.

## Installed files

On Linux the default root is `~/.local/share/firefox-agent-bridge`; on macOS it is `~/Library/Application Support/Firefox Agent Bridge`.

```text
root/
  current -> releases/version-sourcehash/
  releases/                 # immutable application versions
  profile/                  # browser data, retained across updates
  runtime/                  # launcher lock and session metadata
  installation.json
```

The shell command lives in `~/.local/bin`. The installer creates a Linux desktop entry or a macOS application bundle, registers the native host in Mozilla's per-user directory, and adds one marked MCP block to `$CODEX_HOME/config.toml` or `~/.codex/config.toml`.

Known ownership conflicts are rejected before writing registrations. MCP edits are backed up. A failed self-check restores registrations and the previous `current` symlink. Old runtime releases are retained for diagnosis and removed on uninstall; the browser profile is separate.

Only one Firefox profile may own the default native socket per OS user. The `FIREFOX_AGENT_SOCKET` override exists for isolated tests. Test processes do not use real profiles or logged-in accounts.

Primary protocol references: [Mozilla Native Messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging), [Mozilla manifest locations](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_manifests), [MCP stdio](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports), [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
