"""Shared, dependency-free installer operations for Linux and macOS."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import urllib.request

HOST_NAME = "local.firefox_agent_bridge"
SERVER_NAME = "firefox_agent_bridge"
BEGIN = "# >>> firefox-agent-bridge managed MCP >>>"
END = "# <<< firefox-agent-bridge managed MCP <<<"
MANAGED = re.compile(r"(?m)^" + re.escape(BEGIN) + r"\n.*?^" + re.escape(END) + r"\n?", re.S)
RUNTIME_FILES = ["bridge", "extension", "launch.py", "manage.py", "install.py", "installer_lib.py", "VERSION", "REPOSITORY", "LICENSE"]


def default_home():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Firefox Agent Bridge"
    return Path.home() / ".local/share/firefox-agent-bridge"


def state_home():
    return Path(os.environ.get("FIREFOX_AGENT_HOME", default_home())).expanduser().resolve()


def atomic_write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def native_manifest(home=None, platform=None):
    home = Path(home or Path.home())
    platform = platform or sys.platform
    if platform == "linux":
        base = home / ".mozilla/native-messaging-hosts"
    elif platform == "darwin":
        base = home / "Library/Application Support/Mozilla/NativeMessagingHosts"
    else:
        raise RuntimeError("Supported systems: Linux and macOS. Windows is not supported yet.")
    return base / (HOST_NAME + ".json")


def codex_config(home=None):
    return Path(os.environ["CODEX_HOME"]).expanduser() / "config.toml" if os.environ.get("CODEX_HOME") else Path(home or Path.home()) / ".codex/config.toml"


def find_firefox(explicit=None):
    candidates = [explicit, os.environ.get("FIREFOX_BINARY"), shutil.which("firefox"),
                  str(Path.home() / ".local/opt/firefox/firefox"), "/opt/firefox/firefox",
                  "/Applications/Firefox.app/Contents/MacOS/firefox",
                  str(Path.home() / "Applications/Firefox.app/Contents/MacOS/firefox")]
    for item in candidates:
        if item and Path(item).is_file() and os.access(item, os.X_OK):
            path = Path(item).resolve()
            if "/snap/" in str(path) or "/flatpak/" in str(path):
                continue
            return str(path)
    raise RuntimeError("Install a standard Firefox build from https://www.mozilla.org/firefox/new/ then run this command again. Snap/Flatpak builds require portal setup and are not supported by this installer. You can specify --firefox /path/to/firefox.")


def host_text(app, python):
    launcher = app / "bridge/launch-host.sh"
    value = {"name": HOST_NAME, "description": "Firefox Agent Bridge", "path": str(launcher), "type": "stdio",
             "allowed_extensions": ["firefox-agent-bridge@local.codex"]}
    return launcher, "#!/bin/sh\nexec " + shlex.quote(python) + " -u " + shlex.quote(str(app / "bridge/native_host.py")) + " \"$@\"\n", json.dumps(value, indent=2) + "\n"


def register_host(app, python, home=None):
    launcher, script, manifest = host_text(Path(app), python)
    target = native_manifest(home)
    if target.exists() and json.loads(target.read_text()).get("path") != str(launcher):
        raise RuntimeError(f"A different Firefox bridge already owns {target}. Remove that installation first; it has not been overwritten.")
    atomic_write(launcher, script, 0o700)
    atomic_write(target, manifest)
    return target


def mcp_block(app, root, python):
    return (BEGIN + "\n" + f"[mcp_servers.{SERVER_NAME}]\n" +
            f"command = {json.dumps(python)}\nargs = [{json.dumps(str(app / 'bridge/mcp_server.py'))}]\n" +
            "tool_timeout_sec = 40\n" + f"[mcp_servers.{SERVER_NAME}.env]\n" +
            f"FIREFOX_AGENT_HOME = {json.dumps(str(root))}\n" + END + "\n")


def prepare_mcp(config, app, root, python):
    old = config.read_text() if config.exists() else ""
    if old.count(BEGIN) != old.count(END) or old.count(BEGIN) > 1:
        raise RuntimeError("The managed MCP block is damaged. Repair its markers before installing.")
    remainder = MANAGED.sub("", old)
    if re.search(r"(?m)^\s*\[\s*mcp_servers\.(?:" + SERVER_NAME + '|"' + SERVER_NAME + r'"|\x27' + SERVER_NAME + r"\x27)(?:\.|\s*\])", remainder):
        raise RuntimeError(f"MCP server {SERVER_NAME} already exists outside this installer's block. It has not been changed.")
    block = mcp_block(app, root, python)
    new = MANAGED.sub(lambda _: block, old) if BEGIN in old else old + ("\n" if old and not old.endswith("\n\n") else "") + block
    try:
        import tomllib
    except ImportError:
        pass
    else:
        tomllib.loads(new)
    return old, new


def backup_config(config, original):
    if config.exists():
        backup = config.with_name(config.name + ".firefox-agent-backup-" + str(time.time_ns()))
        atomic_write(backup, original)
        return backup
    return None


def shell_wrapper(app, root, python):
    return ("#!/bin/sh\n# Managed by firefox-agent-bridge\n" +
            "export FIREFOX_AGENT_HOME=" + shlex.quote(str(root)) + "\nexec " + shlex.quote(python) + " " +
            shlex.quote(str(app / "manage.py")) + " \"$@\"\n")


def install_shortcuts(app, root, python, home):
    wrapper = home / ".local/bin/firefox-agent"
    if wrapper.exists() and "Managed by firefox-agent-bridge" not in wrapper.read_text():
        raise RuntimeError(f"Command already exists and belongs to another installation: {wrapper}")
    script = shell_wrapper(app, root, python)
    if sys.platform == "darwin":
        bundle = home / "Applications/Firefox Agent.app/Contents"
        binary = bundle / "MacOS/firefox-agent"
        if bundle.parent.exists() and (not binary.is_file() or "Managed by firefox-agent-bridge" not in binary.read_text()):
            raise RuntimeError(f"Application already exists and belongs to another installation: {bundle.parent}")
    atomic_write(wrapper, script, 0o755)
    if sys.platform == "darwin":
        import plistlib
        atomic_write(bundle / "MacOS/firefox-agent", script, 0o755)
        data = {"CFBundleName": "Firefox Agent", "CFBundleIdentifier": "local.firefox-agent-bridge.launcher",
                "CFBundleExecutable": "firefox-agent", "CFBundlePackageType": "APPL", "CFBundleVersion": "1.0"}
        atomic_write(bundle / "Info.plist", plistlib.dumps(data).decode())
        return [str(wrapper), str(bundle.parent)]
    desktop = home / ".local/share/applications/firefox-agent-bridge.desktop"
    def desktop_quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    entry = ("[Desktop Entry]\nType=Application\nName=Firefox Agent\nName[ru]=Firefox для Codex\n" +
             "Comment=Control Firefox from Codex\nExec=" + desktop_quote(wrapper) + "\n" +
             "Icon=" + str(app / "extension/icons/bridge.svg") + "\nTerminal=false\nCategories=Network;WebBrowser;\n")
    atomic_write(desktop, entry, 0o644)
    return [str(wrapper), str(desktop)]


def extract_archive(data, destination):
    """Extract only regular files; reject traversal, links and oversized archives."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        entries = archive.getmembers()
        if len(entries) > 2000 or sum(m.size for m in entries) > 32 * 1024 * 1024:
            raise RuntimeError("Source archive is too large")
        top = None
        for member in entries:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name or not path.parts:
                raise RuntimeError("Unsafe source archive path")
            top = top or path.parts[0]
            if path.parts[0] != top or not (member.isdir() or member.isfile()):
                raise RuntimeError("Source archive contains links or unexpected entries")
            output = destination.joinpath(*path.parts[1:])
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
            elif len(path.parts) > 1:
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as stream:
                    output.write_bytes(stream.read())
    if not (destination / "setup.py").is_file():
        raise RuntimeError("This is not a Firefox Agent Bridge archive")


def download(url, limit=8 * 1024 * 1024):
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("Download exceeds its size limit")
    return data


def source_digest(source):
    digest = hashlib.sha256()
    for item in RUNTIME_FILES:
        path = source / item
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in files:
            if file.is_file() and "__pycache__" not in file.parts and file.name != "launch-host.sh":
                if file.is_symlink():
                    raise RuntimeError("Runtime sources must not contain symlinks")
                digest.update(str(file.relative_to(source)).encode())
                digest.update(file.read_bytes())
    return digest.hexdigest()[:12]


def copy_runtime(source, destination):
    for item in RUNTIME_FILES:
        path = source / item
        if path.is_dir():
            shutil.copytree(path, destination / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "launch-host.sh"))
        else:
            shutil.copy2(path, destination / item)


def switch_release(root, release):
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise RuntimeError("The current application path is not a managed symlink")
    temporary = root / (".current-" + str(time.time_ns()))
    try:
        temporary.symlink_to(release.relative_to(root), target_is_directory=True)
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)


def read_state(root=None):
    root = root or state_home()
    path = root / "installation.json"
    if not path.is_file():
        raise RuntimeError("Installation metadata is missing. Re-run the installer.")
    state = json.loads(path.read_text())
    if state.get("application") != "firefox-agent-bridge" or state.get("root") != str(root):
        raise RuntimeError("Unexpected installation metadata")
    return state
