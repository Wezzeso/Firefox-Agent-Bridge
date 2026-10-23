#!/usr/bin/env python3
"""Start a dedicated Firefox profile, load this add-on, then close bootstrap control.

Only the packaged popup is scripted during bootstrap. Ongoing page control uses
Native Messaging. No geckodriver or other third-party Python package is needed.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit
import fcntl

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "bridge"))
from common import private_directory, request, socket_path
from installer_lib import find_firefox, read_state, state_home

EXTENSION_ID = "firefox-agent-bridge@local.codex"


class Marionette:
    def __init__(self, connection):
        self.connection = connection
        self.counter = 0
        greeting = self.receive()
        if greeting.get("marionetteProtocol") != 3:
            raise RuntimeError("Unsupported Marionette protocol")

    def read(self, size):
        result = bytearray()
        while len(result) < size:
            chunk = self.connection.recv(size - len(result))
            if not chunk:
                raise RuntimeError("Firefox bootstrap connection closed")
            result.extend(chunk)
        return bytes(result)

    def receive(self):
        header = bytearray()
        while True:
            char = self.read(1)
            if char == b":":
                break
            if not char.isdigit() or len(header) >= 10:
                raise RuntimeError("Invalid Marionette frame")
            header.extend(char)
        length = int(header)
        if length > 16 * 1024 * 1024:
            raise RuntimeError("Oversized Marionette frame")
        return json.loads(self.read(length))

    def call(self, method, params=None):
        self.counter += 1
        payload = json.dumps([0, self.counter, method, params or {}], ensure_ascii=False).encode()
        self.connection.sendall(str(len(payload)).encode() + b":" + payload)
        response = self.receive()
        if not isinstance(response, list) or response[:2] != [1, self.counter]:
            raise RuntimeError("Unexpected Marionette response")
        if response[2]:
            error = response[2]
            raise RuntimeError(error.get("message", str(error)))
        return response[3]


def main():
    parser = argparse.ArgumentParser(description="Firefox для Codex: отдельный профиль с автоматической загрузкой расширения.")
    parser.add_argument("--firefox")
    parser.add_argument("--url", default="https://example.com/", help="Первая разрешённая вкладка HTTP(S)")
    parser.add_argument("--headless", action="store_true", help="Для проверки без окна")
    args = parser.parse_args()
    if sys.platform not in ["linux", "darwin"]:
        parser.error("Нужны Linux или macOS и установленный Firefox.")
    storage = state_home() if os.environ.get("FIREFOX_AGENT_HOME") else ROOT
    installed = read_state(storage) if (storage / "installation.json").exists() else None
    firefox = find_firefox(args.firefox or (installed or {}).get("firefox"))
    parsed = urlsplit(args.url)
    if parsed.scheme not in ["http", "https"] or not parsed.hostname or parsed.username or parsed.password:
        parser.error("Нужен обычный HTTP(S) URL без учётных данных.")
    runtime = storage / "runtime"
    private_directory(runtime)
    launch_lock = open(runtime / "launch.lock", "a")
    try:
        fcntl.flock(launch_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Firefox Agent is already starting. Try again in a moment.")
    try:
        state = request("bridge.status")
        if state.get("connected"):
            print("Мост Firefox уже работает. Доступные вкладки:")
            print(json.dumps(request("tabs.list"), ensure_ascii=False, indent=2))
            return 0
    except RuntimeError:
        pass
    profile = storage / "profile"
    private_directory(profile)
    # Never modify or reuse an existing ordinary Firefox profile.
    marker = profile / ".firefox-agent-profile"
    if not marker.exists() and any(profile.iterdir()):
        raise RuntimeError("Отказ использовать посторонний профиль Firefox.")
    marker.write_text("Firefox Agent Bridge dedicated profile\n")
    lock = profile / "lock"
    if lock.is_symlink() and (profile / ".parentlock").exists():
        raise RuntimeError("Профиль агента уже открыт, но мост не ответил. Закройте только окно Firefox для Codex и запустите его снова.")
    identity_file = runtime / "extension-uuid"
    if identity_file.exists():
        identity = str(uuid.UUID(identity_file.read_text().strip()))
    else:
        identity = str(uuid.uuid4())
        identity_file.write_text(identity + "\n")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        bootstrap_port = reservation.getsockname()[1]
    preferences = {
        "marionette.port": bootstrap_port,
        "extensions.webextensions.uuids": json.dumps({EXTENSION_ID: identity}),
        "browser.shell.checkDefaultBrowser": False,
        "browser.aboutwelcome.enabled": False,
        "browser.startup.homepage": "about:blank",
        "startup.homepage_welcome_url": "about:blank",
        "browser.startup.page": 1,
    }
    (profile / "user.js").write_text("// Generated only for the dedicated Firefox Agent profile.\n" + "".join(
        f"user_pref({json.dumps(key)}, {json.dumps(value)});\n" for key, value in preferences.items()))
    if not installed:
        subprocess.run([sys.executable, str(ROOT / "install.py")], check=True, stdout=subprocess.DEVNULL)
    command = [firefox, "--new-instance", "--no-remote", "--profile", str(profile),
               "--marionette", "--remote-allow-system-access"]
    if args.headless:
        command.append("--headless")
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    connection = None
    client = None
    listener_closed = False
    session_started = False
    try:
        until = time.monotonic() + 35
        while time.monotonic() < until:
            if process.poll() is not None:
                raise RuntimeError(f"Firefox завершился с кодом {process.returncode}")
            try:
                connection = socket.create_connection(("127.0.0.1", bootstrap_port), timeout=.5)
                connection.settimeout(35)
                break
            except OSError:
                time.sleep(.1)
        if connection is None:
            raise RuntimeError("Firefox не запустил подключение установки за 35 секунд.")
        client = Marionette(connection)
        client.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
        session_started = True
        client.call("Marionette:AcceptConnections", {"value": False})
        listener_closed = True
        client.call("Addon:Install", {"path": str(ROOT / "extension"), "temporary": True})
        client.call("WebDriver:Navigate", {"url": f"moz-extension://{identity}/popup.html"})
        result = client.call("WebDriver:ExecuteAsyncScript", {"args": [args.url], "script": """
          const done = arguments[arguments.length - 1];
          const target = arguments[0];
          (async () => {
            for (let i=0; i<120; i++) {
              const state = await browser.runtime.sendMessage({action:'status'});
              if (state?.connected) break;
              await new Promise(resolve=>setTimeout(resolve,100));
            }
            const [setup] = await browser.tabs.query({active:true,currentWindow:true});
            const tab = await browser.tabs.create({url:target,active:true});
            for (let i=0; i<200; i++) {
              const current = await browser.tabs.get(tab.id);
              if (current.status==='complete' && /^https?:/.test(current.url)) break;
              await new Promise(resolve=>setTimeout(resolve,100));
            }
            const state = await browser.runtime.sendMessage({action:'grant'});
            if (!state?.ok || !state.connected) throw new Error(state?.error || 'Native host offline');
            done({tabId:tab.id,setupTabId:setup.id});
          })().catch(error=>done({error:String(error)}));
        """})
        # Marionette returns a {value: ...} wrapper for script commands.
        if isinstance(result, dict) and "value" in result:
            result = result["value"]
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError(str(result))
        client.call("WebDriver:DeleteSession")
        session_started = False
        connection.close()
        connection = None
        common = request("bridge.status")
        tabs = request("tabs.list")
        if not common.get("connected") or result["tabId"] not in [t["tabId"] for t in tabs]:
            raise RuntimeError("Проверка живого моста не прошла.")
        metadata = {"pid": process.pid, "profile": str(profile), "tabId": result["tabId"],
                    "bootstrapPort": bootstrap_port, "bootstrapListenerClosed": listener_closed,
                    "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (runtime / "session.json").write_text(json.dumps(metadata, indent=2) + "\n")
        with socket.socket() as probe:
            probe.settimeout(.5)
            if probe.connect_ex(("127.0.0.1", bootstrap_port)) == 0:
                raise RuntimeError("Подключение установки осталось открытым.")
        print("Firefox для Codex готов. Расширение загружено, локальный мост подключён.")
        print("Разрешённая вкладка:")
        print(json.dumps(tabs, ensure_ascii=False, indent=2))
        print("Подключение первоначальной установки закрыто; дальнейшие команды идут через Native Messaging.")
        return 0
    except Exception:
        # Do not leave an unattended bootstrap listener after a failed setup.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        raise
    finally:
        if client and session_started and connection:
            try:
                client.call("WebDriver:DeleteSession")
            except Exception:
                pass
        if connection:
            connection.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Firefox Agent: {exc}", file=sys.stderr)
        sys.exit(1)
