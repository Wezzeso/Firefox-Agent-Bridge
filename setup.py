#!/usr/bin/env python3
"""Install all components using only the Python standard library."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import stat

from installer_lib import (atomic_write, backup_config, codex_config, copy_runtime, default_home,
                           find_firefox, host_text, install_shortcuts, native_manifest, prepare_mcp,
                           register_host, source_digest, switch_release)

SOURCE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Install Firefox Agent Bridge, native host, Codex MCP and launcher.")
    parser.add_argument("--no-launch", action="store_true", help="Install without opening Firefox")
    parser.add_argument("--headless", action="store_true", help="Start Firefox without a visible window")
    parser.add_argument("--firefox", help="Explicit standard Firefox executable")
    parser.add_argument("--prefix", type=Path, default=default_home(), help="Persistent application/state directory")
    parser.add_argument("--skip-mcp", action="store_true", help="Use with another MCP client")
    args = parser.parse_args()
    if sys.platform not in ["linux", "darwin"] or sys.version_info < (3, 9):
        parser.error("Requires Linux or macOS and Python 3.9+. Windows is not supported yet.")
    if os.getuid() == 0 and not os.environ.get("FIREFOX_AGENT_TEST_ROOT"):
        parser.error("Run as your normal user, without sudo.")
    firefox = find_firefox(args.firefox)
    version_result = subprocess.run([firefox, "--version"], text=True, capture_output=True, timeout=15)
    match = re.search(r"Firefox\s+(\d+)", version_result.stdout + version_result.stderr)
    if version_result.returncode != 0 or not match or int(match.group(1)) < 128:
        raise RuntimeError("Firefox 128+ is required.")
    if args.prefix.expanduser().is_symlink():
        raise RuntimeError("Installation prefix must not be a symlink")
    root = args.prefix.expanduser().resolve()
    # A prefix is an application-owned directory; never adopt arbitrary nonempty data.
    if root.exists() and not (root / "installation.json").is_file() and any(root.iterdir()):
        raise RuntimeError(f"Refusing to adopt an unmanaged nonempty directory: {root}")
    if root.is_symlink():
        raise RuntimeError("Installation prefix must not be a symlink")
    version = (SOURCE / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Invalid VERSION")
    app = root / "current"
    home = Path.home()
    python = str(Path(sys.executable).absolute())
    manifest = native_manifest(home)
    expected_launcher, _, _ = host_text(app, python)
    if manifest.exists() and json.loads(manifest.read_text()).get("path") != str(expected_launcher):
        raise RuntimeError(f"A different bridge owns {manifest}. Remove its native host before installing this package.")
    command_path = home / ".local/bin/firefox-agent"
    if command_path.exists() and "Managed by firefox-agent-bridge" not in command_path.read_text():
        raise RuntimeError(f"An unmanaged command already exists: {command_path}. It has not been overwritten.")
    config = codex_config(home)
    old_config, new_config = prepare_mcp(config, app, root, python) if not args.skip_mcp else (None, None)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    releases = root / "releases"
    releases.mkdir(exist_ok=True)
    release = releases / (version + "-" + source_digest(SOURCE))
    created_release = not release.exists()
    if created_release:
        staging = releases / (".staging-" + str(os.getpid()))
        try:
            staging.mkdir()
            copy_runtime(SOURCE, staging)
            os.replace(staging, release)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    previous = os.readlink(app) if app.is_symlink() else None
    saved_manifest = manifest.read_bytes() if manifest.exists() else None
    state_path = root / "installation.json"
    saved_state = state_path.read_bytes() if state_path.exists() else None
    config_existed = config.exists()
    wrote_config = False
    files_to_restore = [command_path]
    mac_bundle = home / "Applications/Firefox Agent.app"
    bundle_existed = mac_bundle.exists()
    if sys.platform == "linux":
        files_to_restore.append(home / ".local/share/applications/firefox-agent-bridge.desktop")
    elif sys.platform == "darwin":
        files_to_restore += [mac_bundle / "Contents/MacOS/firefox-agent", mac_bundle / "Contents/Info.plist"]
    previous_files = {path: path.read_bytes() if path.exists() else None for path in files_to_restore}
    previous_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in files_to_restore if path.exists()}
    try:
        switch_release(root, release)
        register_host(app, python, home)
        shortcuts = install_shortcuts(app, root, python, home)
        if new_config is not None and new_config != old_config:
            if (config.read_text() if config.exists() else "") != old_config:
                raise RuntimeError("Codex configuration changed during installation. No MCP edit was made; run the installer again.")
            backup_config(config, old_config)
            atomic_write(config, new_config)
            wrote_config = True
        state = {"application": "firefox-agent-bridge", "root": str(root), "version": version,
                 "python": python, "firefox": firefox, "repository": (SOURCE / "REPOSITORY").read_text().strip(),
                 "mcpConfig": str(config) if not args.skip_mcp else None, "nativeManifest": str(manifest),
                 "shortcuts": shortcuts, "release": str(release)}
        atomic_write(state_path, json.dumps(state, indent=2) + "\n")
        subprocess.run([python, str(app / "manage.py"), "doctor", "--installed-only"],
                       env={**os.environ, "FIREFOX_AGENT_HOME": str(root)}, check=True)
    except Exception:
        if previous is None:
            app.unlink(missing_ok=True)
        else:
            switch_release(root, root / previous)
        for target, content in [(manifest, saved_manifest), (state_path, saved_state), *previous_files.items()]:
            if content is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write(target, content.decode(), previous_modes.get(target, 0o600))
        if sys.platform == "darwin" and not bundle_existed and mac_bundle.exists():
            shutil.rmtree(mac_bundle)
        if wrote_config and config.exists() and config.read_text() == new_config:
            if config_existed:
                atomic_write(config, old_config)
            else:
                config.unlink(missing_ok=True)
        if created_release:
            shutil.rmtree(release)
        raise
    print(f"\nInstalled Firefox Agent Bridge {version}.")
    print("Next time: open Firefox Agent from Applications, or run:")
    print("  firefox-agent")
    if str(command_path.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"This shell has no ~/.local/bin in PATH. You can use: {command_path}")
    print("Reload Codex MCP / restart Codex once to discover its new tools. CLI control works immediately.")
    if not args.no_launch:
        command = [python, str(app / "manage.py"), "start"] + (["--headless"] if args.headless else [])
        result = subprocess.run(command, env={**os.environ, "FIREFOX_AGENT_HOME": str(root)})
        if result.returncode:
            print("Installed successfully, but Firefox did not start. Run firefox-agent doctor, then firefox-agent.", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        sys.exit(1)
