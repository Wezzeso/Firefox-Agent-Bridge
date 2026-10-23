# Security and privacy

Firefox Agent Bridge gives an MCP client control over explicitly authorized browser tabs. Use it only with a client you trust. Browser content is untrusted input and must never be treated as instructions that override the user's task.

## Scope

- The launcher creates a dedicated profile. Startup authorizes its initial HTTP(S) tab and child tabs. A manual grant is required for other tabs.
- Grants follow a tab across navigation. Stop, native disconnect and extension restart revoke grants. Already dispatched actions cannot be undone by stopping.
- Native Messaging is restricted to the packaged add-on ID. The Unix socket's directory is owned by the user with mode `0700`; the socket is `0600`. Processes running as that same user can use current grants.
- The installer uses per-user native host registration and a clearly marked Codex MCP block. Existing unrelated settings are preserved, and previous configurations are backed up privately.
- A short-lived, loopback-only Marionette bootstrap connection loads the add-on in the dedicated profile. Its listener is closed as soon as the bootstrap session is established; the connection is closed after setup. It is not used for ongoing page control.
- The extension requires `<all_urls>` to inspect frames and capture tabs. Background checks enforce the additional tab grant boundary. Private windows and non-HTTP(S) page actions are excluded.

## Data

Page text, screenshots and tool arguments are sent to the MCP client and its model. The application itself has no analytics or remote content upload endpoint. Native socket traffic stays local. Firefox and websites have their own network behavior.

Password input values are redacted from snapshots. This does not guarantee removal of every secret: page text, non-password fields and screenshots can contain private data. Do not publish profiles, runtime files, authentication material or screenshots without reviewing them.

The popup keeps a bounded, in-memory action log containing method, tab ID, time and success status; it does not store input text. Updates download public project source from GitHub. Bootstrap downloads use HTTPS and reject path traversal, symlinks and oversized archives.

## Limitations

DOM actions use synthetic events (`isTrusted=false`). Some sites will ignore them. Native browser UI, CAPTCHA, WebAuthn, file pickers, closed Shadow DOM, canvas controls and arbitrary hardware input are outside this project's scope. No automatic retry is made after a mutating command times out; inspect state before repeating it.

`firefox-agent uninstall` preserves the dedicated profile. `--purge` is an explicit permanent deletion and is rejected while the profile lock is present. Close the running Firefox Agent window after uninstalling to unload its temporary extension.

## Reporting vulnerabilities

Use GitHub's **Report a vulnerability** feature if the repository enables private vulnerability reporting. Otherwise, open an issue that says you need a private reporting channel, without including exploit details or private data. Do not include cookies, tokens, credentials, personal page content or a browser profile in a public issue.
