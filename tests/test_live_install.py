"""End-to-end install.sh -> installer -> launcher -> real Firefox -> native host.

Requires a standard Firefox binary; no geckodriver, Selenium or Python packages.
"""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer_lib import find_firefox


@unittest.skipUnless(os.environ.get("FIREFOX_AGENT_LIVE_TEST") == "1", "Set FIREFOX_AGENT_LIVE_TEST=1 to run a real Firefox install")
class LiveInstallTest(unittest.TestCase):
    def test_one_command_installs_launches_and_controls_firefox(self):
        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass
        with tempfile.TemporaryDirectory(prefix="ffagent-live-") as temp:
            base = Path(temp)
            home = base / "home"
            home.mkdir(mode=0o700)
            prefix = home / "app"
            firefox = find_firefox()
            supplied = os.environ.get("FIREFOX_AGENT_RELEASE_ARCHIVE")
            fixture = Path(supplied).resolve() if supplied else base / "source.tar.gz"
            if not supplied:
                with tarfile.open(fixture, "w:gz") as archive:
                    for path in ROOT.rglob("*"):
                        if path.is_file() and not any(part in [".git", "__pycache__", "dist"] for part in path.relative_to(ROOT).parts):
                            archive.add(path, arcname="firefox-agent-bridge/" + str(path.relative_to(ROOT)))
            env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
                   "FIREFOX_AGENT_HOME": str(prefix), "FIREFOX_AGENT_SOCKET": str(base / "ipc/bridge.sock"),
                   "FIREFOX_AGENT_REPOSITORY": "example/firefox-agent-bridge", "FIREFOX_AGENT_SOURCE_ARCHIVE": str(fixture),
                   "FIREFOX_AGENT_TEST_ROOT": "1"}
            server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT / "tests")))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            session = None
            try:
                result = subprocess.run(["sh", str(ROOT / "install.sh"), "--headless", "--prefix", str(prefix), "--firefox", firefox],
                                        env=env, capture_output=True, text=True, timeout=80)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout)
                session = json.loads((prefix / "runtime/session.json").read_text())
                wrapper = home / ".local/bin/firefox-agent"
                def command(*args):
                    result = subprocess.run([str(wrapper), *args], env=env, capture_output=True, text=True, timeout=35)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return json.loads(result.stdout)
                status = command("status")
                self.assertTrue(status["connected"])
                self.assertEqual(status["version"], (ROOT / "VERSION").read_text().strip())
                source = command("tabs")[0]["tabId"]
                tab = command("request", "tabs.create", json.dumps({"sourceTabId": source,
                    "url": f"http://127.0.0.1:{server.server_port}/fixture.html"}))["tabId"]
                def page(method, **params):
                    return command("request", "page." + method, json.dumps({"tabId": tab, **params}))
                page("wait", selector="#name")
                page("fill", selector="#name", text="One command 🦊")
                page("check", selector="#agree", checked=True)
                page("click", selector="#save")
                self.assertIn("Сохранено: One command 🦊 / kz / true", page("snapshot")["text"])
                messages = [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "firefox_screenshot", "arguments": {"tabId": tab}}},
                ]
                mcp = subprocess.run([str(wrapper), "mcp"], env=env, input="".join(json.dumps(m)+"\n" for m in messages),
                                     text=True, capture_output=True, timeout=35)
                self.assertEqual(mcp.returncode, 0, mcp.stderr)
                replies = {r["id"]: r for r in map(json.loads, mcp.stdout.splitlines())}
                self.assertEqual(replies[2]["result"]["content"][0]["type"], "image")
                import socket
                with socket.socket() as sock:
                    self.assertNotEqual(sock.connect_ex(("127.0.0.1", session["bootstrapPort"])), 0)
                self.assertTrue(command("stop")["stopped"])
                self.assertEqual(command("tabs"), [])
                print("PASS: one-command bootstrap, native messaging, Unicode form, MCP screenshot and emergency stop")
            finally:
                if session:
                    os.kill(session["pid"], signal.SIGTERM)
                    for _ in range(50):
                        if not (prefix / "profile/lock").is_symlink():
                            break
                        time.sleep(.1)
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
