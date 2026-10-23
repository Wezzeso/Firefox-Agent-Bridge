#!/usr/bin/env python3
"""The installed firefox-agent command."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from installer_lib import (BEGIN, MANAGED, SERVER_NAME, atomic_write, backup_config, download,
                           find_firefox, read_state, shell_wrapper, state_home)

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP / "bridge"))
from common import request


def doctor(installed_only=False):
    root = state_home()
    state = read_state(root)
    checks = []
    checks.append(("Firefox", bool(find_firefox(state["firefox"]))))
    manifest = Path(state["nativeManifest"])
    checks.append(("Native Messaging", manifest.is_file() and json.loads(manifest.read_text()).get("path") == str(root / "current/bridge/launch-host.sh")))
    checks.append(("Launcher", (Path.home() / ".local/bin/firefox-agent").is_file()))
    if state.get("mcpConfig"):
        path = Path(state["mcpConfig"])
        text = path.read_text() if path.exists() else ""
        checks.append(("Codex MCP", BEGIN in text and f"mcp_servers.{SERVER_NAME}" in text))
    for name, ok in checks:
        print(("OK  " if ok else "FAIL") + name)
    if not installed_only:
        try:
            value = request("bridge.status")
            print(f"OK  Browser connected; controlled tabs: {value['controlledTabs']}")
        except RuntimeError:
            print("INFO Firefox Agent is closed. Start it with: firefox-agent")
    return 0 if all(ok for _, ok in checks) else 1


def uninstall(purge=False):
    root = state_home()
    state = read_state(root)
    # Preflight all ownership checks before changing anything.
    manifest = Path(state["nativeManifest"])
    if manifest.exists() and json.loads(manifest.read_text()).get("path") != str(root / "current/bridge/launch-host.sh"):
        raise RuntimeError("Native host now belongs to another installation; nothing was removed.")
    command = Path.home() / ".local/bin/firefox-agent"
    if command.exists() and command.read_text() != shell_wrapper(root / "current", root, state["python"]):
        raise RuntimeError("Launcher now belongs to another installation; nothing was removed.")
    bundle = Path.home() / "Applications/Firefox Agent.app"
    if sys.platform == "darwin" and bundle.exists():
        binary = bundle / "Contents/MacOS/firefox-agent"
        if not binary.is_file() or binary.read_text() != shell_wrapper(root / "current", root, state["python"]):
            raise RuntimeError("Application bundle now belongs to another installation; nothing was removed.")
    if purge and (root / "profile/lock").is_symlink():
        raise RuntimeError("Close the Firefox Agent window before --purge. Its profile is currently in use.")
    config = Path(state["mcpConfig"]) if state.get("mcpConfig") else None
    text = config.read_text() if config and config.exists() else None
    if text is not None and (text.count(BEGIN) > 1 or text.count(BEGIN) != text.count("# <<< firefox-agent-bridge managed MCP <<<")):
        raise RuntimeError("MCP markers were edited. Repair them before uninstalling.")
    try:
        request("bridge.stop")
    except RuntimeError:
        pass
    if text is not None and BEGIN in text:
        backup_config(config, text)
        atomic_write(config, MANAGED.sub("", text))
    manifest.unlink(missing_ok=True)
    command.unlink(missing_ok=True)
    for item in state.get("shortcuts", []):
        path = Path(item)
        if path == command:
            continue
        if path == Path.home() / ".local/share/applications/firefox-agent-bridge.desktop":
            path.unlink(missing_ok=True)
        elif path == Path.home() / "Applications/Firefox Agent.app" and path.exists():
            shutil.rmtree(path)
    (root / "current").unlink(missing_ok=True)
    if (root / "releases").exists():
        shutil.rmtree(root / "releases")
    if purge:
        shutil.rmtree(root)
        print("Uninstalled. Dedicated Firefox profile deleted (--purge).")
    else:
        state["uninstalled"] = True
        atomic_write(root / "installation.json", json.dumps(state, indent=2) + "\n")
        print(f"Uninstalled. Profile preserved at: {root / 'profile'}")
    print("Close the Firefox Agent window to unload the running temporary extension. Reload Codex MCP to refresh its tools.")
    return 0


def update():
    state = read_state()
    repository = state["repository"]
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or repository.startswith("YOUR_"):
        raise RuntimeError("The repository URL is not configured in this package.")
    script = download(f"https://raw.githubusercontent.com/{repository}/main/install.sh", limit=64 * 1024)
    with tempfile.TemporaryDirectory(prefix="firefox-agent-update-") as temp:
        path = Path(temp) / "install.sh"
        path.write_bytes(script)
        command = ["sh", str(path), "--no-launch", "--prefix", str(state_home()), "--firefox", state["firefox"]]
        if not state.get("mcpConfig"):
            command.append("--skip-mcp")
        result = subprocess.call(command)
        if result == 0:
            print("Update installed. Close and reopen Firefox Agent to load the new runtime.")
        return result


def main():
    if len(sys.argv) == 1:
        sys.argv.append("start")
    elif sys.argv[1].startswith(("http://", "https://")):
        sys.argv[1:2] = ["start", "--url", sys.argv[1]]
    parser = argparse.ArgumentParser(description="Firefox Agent Bridge — start, inspect, update or uninstall.")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Open the dedicated Firefox with the extension loaded")
    start.add_argument("--url", default="https://example.com/")
    start.add_argument("--headless", action="store_true")
    for name in ["status", "tabs", "stop", "update", "mcp"]:
        commands.add_parser(name)
    diagnosis = commands.add_parser("doctor")
    diagnosis.add_argument("--installed-only", action="store_true", help=argparse.SUPPRESS)
    remove = commands.add_parser("uninstall")
    remove.add_argument("--purge", action="store_true", help="Also permanently delete the dedicated profile")
    raw = commands.add_parser("request")
    raw.add_argument("method")
    raw.add_argument("params", nargs="?", default="{}", help="JSON object or - for stdin")
    args = parser.parse_args()
    if args.command == "mcp":
        os.execv(sys.executable, [sys.executable, str(APP / "bridge/mcp_server.py")])
    if args.command == "start":
        command = [sys.executable, str(APP / "launch.py"), "--url", args.url]
        if args.headless:
            command.append("--headless")
        return subprocess.call(command)
    if args.command == "doctor":
        return doctor(args.installed_only)
    if args.command == "uninstall":
        return uninstall(args.purge)
    if args.command == "update":
        return update()
    if args.command == "request":
        params = json.loads(sys.stdin.read() if args.params == "-" else args.params)
        value = request(args.method, params)
    else:
        value = request({"status": "bridge.status", "tabs": "tabs.list", "stop": "bridge.stop"}[args.command])
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Firefox Agent: {exc}", file=sys.stderr)
        sys.exit(1)
