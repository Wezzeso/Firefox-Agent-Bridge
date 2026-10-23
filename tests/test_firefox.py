"""Real Firefox + native messaging integration. Only geckodriver is a test dependency.

Run: python3 tests/test_firefox.py --geckodriver /absolute/path/to/geckodriver
Creates an isolated HOME/profile. Does not use your open Firefox or logged-in sites.
"""
import argparse
import base64
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
import common


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geckodriver", required=True)
    parser.add_argument("--firefox", default=shutil.which("firefox"))
    args = parser.parse_args()
    checks = []
    def checked(label):
        checks.append(label)
        print("PASS", label, flush=True)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "tests")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/fixture.html"
    with tempfile.TemporaryDirectory(prefix="ff-agent-test-") as temp:
        temp = Path(temp)
        test_home = temp / "home"
        test_home.mkdir(mode=0o700)
        ipc = temp / "ipc/bridge.sock"
        previous = os.environ.get("FIREFOX_AGENT_SOCKET")
        os.environ["FIREFOX_AGENT_SOCKET"] = str(ipc)
        env = {**os.environ, "HOME": str(test_home), "MOZ_HEADLESS": "1"}
        subprocess.run([sys.executable, str(ROOT / "install.py"), "--home", str(test_home)], check=True, capture_output=True)
        driver_port = port()
        log = (temp / "geckodriver.log").open("w+")
        driver = subprocess.Popen([str(Path(args.geckodriver).resolve()), "--port", str(driver_port), "--allow-system-access"],
                                  env=env, stdout=log, stderr=log)
        session = None
        base = f"http://127.0.0.1:{driver_port}"
        def wd(method, path, data=None):
            payload = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(base + path, data=payload, method=method, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    result = json.load(r)["value"]
            except urllib.error.HTTPError as exc:
                raise RuntimeError(exc.read().decode()) from exc
            return result
        def route(path):
            return f"/session/{session}" + path
        def js(script, arguments=None, async_=False):
            return wd("POST", route("/execute/async" if async_ else "/execute/sync"), {"script": script, "args": arguments or []})
        def expect_error(fn, substring):
            try:
                fn()
            except RuntimeError as exc:
                assert substring.lower() in str(exc).lower(), str(exc)
            else:
                raise AssertionError("Expected rejection: " + substring)
        try:
            for _ in range(100):
                try:
                    wd("GET", "/status")
                    break
                except (OSError, RuntimeError):
                    time.sleep(.1)
            result = wd("POST", "/session", {"capabilities": {"alwaysMatch": {"moz:firefoxOptions": {
                "binary": str(Path(args.firefox).resolve()), "args": ["-headless", "--no-remote"],
                "prefs": {"browser.shell.checkDefaultBrowser": False, "datareporting.policy.dataSubmissionEnabled": False,
                          "extensions.webextensions.uuids": json.dumps({"firefox-agent-bridge@local.codex": "9814522d-39d2-4446-a731-16a9df415d23"})}
            }}}})
            session = result["sessionId"]
            print("Firefox", result["capabilities"]["browserVersion"], flush=True)
            wd("POST", route("/moz/addon/install"), {"path": str(ROOT / "extension"), "temporary": True})
            for _ in range(80):
                if ipc.exists():
                    break
                time.sleep(.1)
            assert common.request("bridge.status")["connected"]
            assert common.request("tabs.list") == []
            checked("Native host connected; no tabs exposed before grant")
            wd("POST", route("/url"), {"url": "moz-extension://9814522d-39d2-4446-a731-16a9df415d23/popup.html"})
            popup_handle = wd("GET", route("/window"))
            # The packaged popup sends the same grant message used by its button.
            result = js("""
              const done=arguments[arguments.length-1];
              browser.tabs.create({url:arguments[0],active:true}).then(async tab=>{
                for(let i=0;i<100;i++) {
                  const current=await browser.tabs.get(tab.id);
                  if(current.status==='complete' && current.url.startsWith('http')) break;
                  await new Promise(r=>setTimeout(r,50));
                }
                const state=await browser.runtime.sendMessage({action:'grant'});
                done({tabId:tab.id,state});
              }).catch(e=>done({error:String(e)}));
            """, [url], True)
            assert result.get("state", {}).get("ok"), result
            tab = result["tabId"]
            common.request("page.wait", {"tabId": tab, "selector": "#name"})
            tabs = common.request("tabs.list")
            assert [t["tabId"] for t in tabs] == [tab]
            checked("Popup grant exposes only selected tab")
            def call(method, **params):
                return common.request("page." + method, {"tabId": tab, **params})
            snapshot = call("snapshot")
            assert "never-return-this" not in json.dumps(snapshot)
            def ref(label, snap=None):
                return next(e["elementId"] for e in (snap or snapshot)["elements"] if e["name"] == label)
            call("fill", elementId=ref("Имя"), text="Агент 🦊")
            call("select", elementId=ref("Страна"), value="fr")
            call("check", elementId=ref("Согласен"), checked=True)
            call("click", elementId=ref("Сохранить"))
            after = call("snapshot")
            assert "Сохранено: Агент 🦊 / fr / true" in after["text"], after["text"]
            checked("Snapshot redaction; Unicode input; select; checkbox; submit")
            snapshot = after
            expect_error(lambda: call("click", elementId=ref("Отключён")), "отключ")
            expect_error(lambda: call("click", selector="button"), "неоднознач")
            change = ref("Сменить текст")
            call("click", elementId=change)
            expect_error(lambda: call("click", elementId=change), "изменился")
            checked("Disabled, ambiguous and changed targets rejected")
            snapshot = call("snapshot")
            call("click", elementId=ref("Shadow action"))
            assert "Shadow clicked" in call("snapshot")["text"]
            checked("Open Shadow DOM interaction")
            frames = call("frames")
            frame = next(f for f in frames if f["frameId"] != 0)
            frame_snapshot = call("snapshot", frameId=frame["frameId"])
            frame_ref = frame_snapshot["elements"][0]["elementId"]
            call("click", frameId=frame["frameId"], elementId=frame_ref)
            assert "Frame clicked" in call("snapshot", frameId=frame["frameId"])["text"]
            checked("Cross-origin iframe snapshot and click")
            snapshot = call("snapshot")
            call("click", elementId=ref("Перекрыть"))
            expect_error(lambda: call("click", elementId=ref("Сохранить")), "перекрыт")
            call("click", selector="#cover")
            checked("Covered target rejected")
            shot = call("screenshot")
            assert base64.b64decode(shot["dataUrl"].split(",")[1]).startswith(b"\xff\xd8")
            checked("Real Firefox viewport screenshot")
            snapshot = call("snapshot")
            stale = ref("Сохранить")
            call("navigate", url=url + "?new-document=1")
            call("wait", selector="#name")
            expect_error(lambda: call("click", elementId=stale), "устарел")
            expect_error(lambda: call("navigate", url="file:///etc/passwd"), "HTTP")
            checked("Navigation expires element IDs; non-web navigation rejected")
            call("fill", selector="#name", text="Enter test")
            call("press", selector="#name", key="Enter")
            assert "Сохранено: Enter test" in call("snapshot")["text"]
            call("scroll", y=500)
            assert call("snapshot")["viewport"]["scrollY"] > 0
            checked("Enter submission and scrolling")
            # MCP itself uses the real bridge, including image content.
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "firefox_screenshot", "arguments": {"tabId": tab}}},
            ]
            mcp = subprocess.run([sys.executable, str(ROOT / "bridge/mcp_server.py")],
                                 input="".join(json.dumps(m)+"\n" for m in messages), text=True, capture_output=True, check=True)
            replies = [json.loads(line) for line in mcp.stdout.splitlines()]
            assert replies[1]["result"]["content"][0]["type"] == "image"
            checked("MCP initialization and screenshot image result")
            child = common.request("tabs.create", {"sourceTabId": tab, "url": url})["tabId"]
            assert child in [t["tabId"] for t in common.request("tabs.list")]
            common.request("tabs.close", {"tabId": child})
            checked("New tabs inherit grant; authorized tab closes")
            # A blocked MCP tool must not prevent a stop request on the same stdio connection.
            process = subprocess.Popen([sys.executable, str(ROOT / "bridge/mcp_server.py")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                wait_message = {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {
                    "name": "firefox_wait", "arguments": {"tabId": tab, "selector": "#missing-mcp", "timeoutMs": 15000}}}
                stop_message = {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "firefox_stop", "arguments": {}}}
                process.stdin.write(json.dumps(wait_message) + "\n")
                process.stdin.flush()
                time.sleep(.4)
                start = time.monotonic()
                process.stdin.write(json.dumps(stop_message) + "\n")
                process.stdin.flush()
                import select
                ready, _, _ = select.select([process.stdout], [], [], 3)
                assert ready, "MCP stop blocked behind page wait"
                first_reply = json.loads(process.stdout.readline())
                assert first_reply["id"] == 11 and not first_reply["result"]["isError"], first_reply
                assert time.monotonic() - start < 3
                process.stdin.close()
                process.wait(timeout=4)
                assert common.request("tabs.list") == []
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                process.stderr.close()
            checked("MCP emergency stop bypasses another tool on the same stdio connection")
            # Re-grant through the packaged popup to test independent CLI stop as well.
            wd("POST", route("/window"), {"handle": popup_handle})
            state = js("""
                const tabId=arguments[0],done=arguments[arguments.length-1];
                browser.tabs.update(tabId,{active:true}).then(()=>browser.runtime.sendMessage({action:'grant'})).then(done);
            """, [tab], True)
            assert state["ok"], state
            wait_error = []
            def waiting():
                try:
                    call("wait", selector="#never-exists", timeoutMs=15000)
                except RuntimeError as exc:
                    wait_error.append(str(exc))
            thread = threading.Thread(target=waiting)
            thread.start()
            time.sleep(.4)
            start = time.monotonic()
            assert common.request("bridge.stop")["stopped"]
            assert time.monotonic() - start < 2
            thread.join(3)
            assert wait_error, "Wait should cancel on stop"
            assert common.request("tabs.list") == []
            expect_error(lambda: call("snapshot"), "Нет доступа")
            checked("Emergency stop bypasses queue, cancels wait, revokes all grants")
            # Public CLI handles offline status clearly; browser is closed below.
            print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False, indent=2), flush=True)
        except Exception:
            log.flush()
            log.seek(0)
            print(log.read()[-16000:], file=sys.stderr)
            raise
        finally:
            if session:
                try:
                    wd("DELETE", route(""))
                except Exception:
                    pass
            driver.terminate()
            try:
                driver.wait(timeout=5)
            except subprocess.TimeoutExpired:
                driver.kill()
                driver.wait()
            log.close()
            if previous is None:
                os.environ.pop("FIREFOX_AGENT_SOCKET", None)
            else:
                os.environ["FIREFOX_AGENT_SOCKET"] = previous
            server.shutdown()


if __name__ == "__main__":
    main()
