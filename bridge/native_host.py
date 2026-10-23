#!/usr/bin/env python3
"""Firefox native messaging host; stdout is reserved for framed JSON."""
import fcntl
import json
import os
import queue
import socket
import stat
import struct
import sys
import threading
import time
import uuid

from common import MAX_REQUEST, MAX_RESPONSE, METHODS, TIMEOUT, encode, private_directory, socket_path


def read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            raise EOFError("Native messaging pipe closed")
        data.extend(chunk)
    return bytes(data)


class Host:
    def __init__(self):
        self.stopped = threading.Event()
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)

    def send(self, value):
        payload = encode(value)
        if len(payload) >= 1024 * 1024:
            raise ValueError("Native message exceeds Firefox's 1 MiB limit")
        with self.write_lock:
            sys.stdout.buffer.write(struct.pack("=I", len(payload)) + payload)
            sys.stdout.buffer.flush()

    def reader(self):
        try:
            while not self.stopped.is_set():
                size = struct.unpack("=I", read_exact(sys.stdin.buffer, 4))[0]
                if size > MAX_RESPONSE:
                    raise ValueError("Oversized browser message")
                value = json.loads(read_exact(sys.stdin.buffer, size))
                if not isinstance(value, dict):
                    raise ValueError("Invalid browser message")
                if value.get("type") == "response":
                    with self.pending_lock:
                        waiter = self.pending.get(value.get("id"))
                    if waiter:
                        try:
                            waiter.put_nowait(value)
                        except queue.Full:
                            pass
        except (EOFError, ValueError, OSError, struct.error):
            pass
        finally:
            self.stopped.set()
            with self.pending_lock:
                for waiter in self.pending.values():
                    try:
                        waiter.put_nowait({"ok": False, "error": "Firefox disconnected; inspect the page before retrying."})
                    except queue.Full:
                        pass

    def client(self, conn):
        ident = None
        try:
            with conn:
                conn.settimeout(TIMEOUT + 5)
                try:
                    with conn.makefile("rb") as stream:
                        raw = stream.readline(MAX_REQUEST + 1)
                    if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST:
                        raise ValueError("Invalid or oversized command")
                    value = json.loads(raw)
                    if not isinstance(value, dict) or value.get("method") not in METHODS:
                        raise ValueError("Unknown command")
                    params = value.get("params", {})
                    if not isinstance(params, dict):
                        raise ValueError("params must be an object")
                    ident = uuid.uuid4().hex
                    waiter = queue.Queue(maxsize=1)
                    with self.pending_lock:
                        self.pending[ident] = waiter
                    self.send({"type": "command", "id": ident, "method": value["method"], "params": params,
                               "deadline": int((time.time() + TIMEOUT) * 1000)})
                    try:
                        response = waiter.get(timeout=TIMEOUT + 2)
                    except queue.Empty:
                        response = {"ok": False, "error": "Command timed out. Outcome may be unknown; inspect before retrying. Commands are never automatically retried."}
                except (ValueError, OSError) as exc:
                    response = {"ok": False, "error": str(exc)}
                payload = encode(response) + b"\n"
                if len(payload) > MAX_RESPONSE:
                    payload = encode({"ok": False, "error": "Browser response too large"}) + b"\n"
                conn.sendall(payload)
        except OSError:
            pass
        finally:
            with self.pending_lock:
                self.pending.pop(ident, None)
            self.slots.release()

    def run(self):
        path = socket_path()
        private_directory(path.parent)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(str(path) + ".lock", flags, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Firefox profile already owns this bridge. Disable its extension first.") from exc
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise RuntimeError("Refusing to replace an unexpected socket path")
                path.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                old_umask = os.umask(0o177)
                try:
                    server.bind(str(path))
                finally:
                    os.umask(old_umask)
                os.chmod(path, 0o600)
                server.listen(8)
                server.settimeout(0.5)
                threading.Thread(target=self.reader, daemon=True).start()
                self.send({"type": "ready", "version": "1.0.0"})
                try:
                    while not self.stopped.is_set():
                        try:
                            conn, _ = server.accept()
                        except socket.timeout:
                            continue
                        if not self.slots.acquire(blocking=False):
                            conn.close()
                            continue
                        threading.Thread(target=self.client, args=(conn,), daemon=True).start()
                finally:
                    path.unlink(missing_ok=True)
        finally:
            os.close(lock_fd)


if __name__ == "__main__":
    try:
        Host().run()
    except Exception as exc:
        print(f"Firefox Agent Bridge: {exc}", file=sys.stderr)
        sys.exit(1)
