![Codex and Firefox connected by a bridge](docs/assets/banner.webp)

# Firefox Agent Bridge

Control Firefox from Codex through a local Native Messaging bridge and 20 MCP tools. One command installs the app, registers MCP, adds a launcher and opens a dedicated Firefox profile.

Requires **Linux or macOS, Python 3.9+, standard Firefox 128+, and Codex**. No sudo, pip packages, Node.js, Docker or API key. Windows and container-packaged Firefox are not supported by this installer.

```sh
curl -fsSL https://raw.githubusercontent.com/Wezzeso/Firefox-Agent-Bridge/main/install.sh | sh
```

Reload MCP or restart Codex once, then ask it to use Firefox Agent Bridge. Launch again from Applications (**Firefox Agent**) or run `firefox-agent`.

Prefer to inspect the source first? Download this repository, extract it, then run `python3 setup.py`. `--no-launch` installs without starting Firefox.

| Command | Action |
| --- | --- |
| `firefox-agent` | Start the dedicated browser |
| `firefox-agent doctor` | Diagnose the installation |
| `firefox-agent tabs` | List authorized tabs |
| `firefox-agent stop` | Revoke all tab grants |
| `firefox-agent update` | Update without deleting the profile |
| `firefox-agent uninstall` | Remove the app and integration; keep the profile |

Stop inside Firefox with **Alt+Shift+X** or the extension's stop button. The initial tab and its children are authorized during startup; other tabs require manual grants. Synthetic DOM events cannot automate every site. CAPTCHAs, system dialogs, WebAuthn, file inputs, closed Shadow DOM and trusted hardware input are unsupported.

The app uses a dedicated profile and a private Unix socket. The bootstrap Marionette listener is closed after installation. MCP content is sent to its connected client/model. See [security and privacy](SECURITY.md), [tool examples](docs/tools.md), and the [Russian README](README.md).

To remove the dedicated profile permanently, close its Firefox window first and explicitly use `firefox-agent uninstall --purge`.

[MIT license](LICENSE). Not affiliated with Mozilla or OpenAI.
