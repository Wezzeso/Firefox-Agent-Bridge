import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
import common
import mcp_server
from native_host import read_exact


class CoreTests(unittest.TestCase):
    def test_partial_framing_and_eof(self):
        class Partial(io.BytesIO):
            def read(self, n):
                return super().read(min(1, n))
        self.assertEqual(read_exact(Partial("Привет".encode()), 12), "Привет".encode())
        with self.assertRaises(EOFError):
            read_exact(Partial(b"x"), 2)

    def test_mcp_handshake_and_tools(self):
        response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                      "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        result = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]
        self.assertEqual(len(result["tools"]), 20)
        self.assertTrue(all("method" not in t for t in result["tools"]))
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_schema_rejects_ambiguous_target_and_bad_arguments(self):
        tool = next(t for t in mcp_server.TOOLS if t["name"] == "firefox_click")
        for args in [{"tabId": 1}, {"tabId": 1, "elementId": "x", "selector": "button"},
                     {"tabId": True, "selector": "button"}, {"tabId": 1, "selector": "button", "eval": "x"}]:
            with self.assertRaises(ValueError):
                mcp_server.validate(args, tool["inputSchema"])
        mcp_server.validate({"tabId": 1, "selector": "button"}, tool["inputSchema"])

    def test_native_host_roundtrip_permissions_and_exclusivity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ipc/bridge.sock"
            env = {**os.environ, "FIREFOX_AGENT_SOCKET": str(path)}
            host = subprocess.Popen([sys.executable, str(ROOT / "bridge/native_host.py")],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                def receive():
                    length = struct.unpack("=I", read_exact(host.stdout, 4))[0]
                    return json.loads(read_exact(host.stdout, length))
                self.assertEqual(receive()["type"], "ready")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                other = subprocess.run([sys.executable, str(ROOT / "bridge/native_host.py")], env=env,
                                       capture_output=True, timeout=3)
                self.assertNotEqual(other.returncode, 0)
                self.assertIn(b"Another Firefox profile", other.stderr)
                self.assertTrue(path.exists())
                result = []
                def client():
                    with socket.socket(socket.AF_UNIX) as sock:
                        sock.settimeout(3)
                        sock.connect(str(path))
                        sock.sendall(common.encode({"method": "page.fill", "params": {"text": "Привет"}}) + b"\n")
                        result.append(json.loads(sock.makefile("rb").readline()))
                thread = threading.Thread(target=client)
                thread.start()
                command = receive()
                self.assertEqual(command["params"]["text"], "Привет")
                self.assertIn("deadline", command)
                payload = common.encode({"type": "response", "id": command["id"], "ok": True, "result": {"text": "Готово"}})
                frame = struct.pack("=I", len(payload)) + payload
                for chunk in [frame[:1], frame[1:3], frame[3:]]:
                    host.stdin.write(chunk)
                    host.stdin.flush()
                thread.join(4)
                self.assertEqual(result[0]["result"]["text"], "Готово")
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.settimeout(3)
                    sock.connect(str(path))
                    sock.sendall(b'{"method":"arbitrary.eval"}\n')
                    self.assertFalse(json.loads(sock.makefile("rb").readline())["ok"])
                host.stdin.close()
                host.wait(timeout=4)
                self.assertFalse(path.exists())
            finally:
                if host.poll() is None:
                    host.kill()
                    host.wait()
                host.stdout.close()
                host.stderr.close()


if __name__ == "__main__":
    unittest.main()
