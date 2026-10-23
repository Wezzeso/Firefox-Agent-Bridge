"""Local IPC; no HTTP listener, third-party package, or API key."""
import json
import os
from pathlib import Path
import socket
import stat

MAX_REQUEST = 512 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
TIMEOUT = 25
METHODS = frozenset([
    "bridge.status", "bridge.stop", "tabs.list", "tabs.create", "tabs.activate", "tabs.close",
    "page.navigate", "page.reload", "page.back", "page.forward", "page.frames", "page.snapshot",
    "page.screenshot", "page.click", "page.fill", "page.select", "page.check", "page.press",
    "page.scroll", "page.wait",
])


def socket_path():
    return Path(os.environ.get("FIREFOX_AGENT_SOCKET", f"/tmp/firefox-agent-{os.getuid()}/bridge.sock"))


def private_directory(path):
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError(f"IPC directory must belong to you and have mode 0700: {path}")


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def request(method, params=None):
    if method not in METHODS:
        raise ValueError(f"Unknown method: {method}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    payload = encode({"method": method, "params": params}) + b"\n"
    if len(payload) > MAX_REQUEST:
        raise ValueError("Request exceeds 512 KiB")
    path = socket_path()
    try:
        private_directory(path.parent)
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError("Unexpected IPC socket owner or permissions")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT + 10)
            sock.connect(str(path))
            sock.sendall(payload)
            with sock.makefile("rb") as stream:
                data = stream.readline(MAX_RESPONSE + 1)
        if not data.endswith(b"\n") or len(data) > MAX_RESPONSE:
            raise RuntimeError("Invalid or oversized response from Firefox")
        response = json.loads(data)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Firefox command failed"))
        return response.get("result")
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise RuntimeError("Firefox bridge is offline. Install the native host, load the extension, then open its popup.") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError("Timed out. Outcome may be unknown: inspect the page before repeating an action.") from exc
