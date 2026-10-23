#!/usr/bin/env python3
"""Install just this native host under the current Linux user's Mozilla directory."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parent
NAME = "local.firefox_agent_bridge"


def main():
    parser = argparse.ArgumentParser(description="Установить локальный мост Firefox (Linux, Python 3.9+). Без sudo.")
    parser.add_argument("--home", type=Path, default=Path.home(), help="Отдельный HOME для тестирования")
    parser.add_argument("--uninstall", action="store_true", help="Удалить только manifest этого native host")
    args = parser.parse_args()
    if sys.platform not in ["linux", "darwin"]:
        parser.error("Этот установщик поддерживает Linux и macOS.")
    if sys.version_info < (3, 9):
        parser.error("Нужен Python 3.9 или новее.")
    from installer_lib import native_manifest
    destination = native_manifest(args.home.expanduser().resolve())
    if args.uninstall:
        if destination.exists():
            current = json.loads(destination.read_text())
            if current.get("path") != str(ROOT / "bridge/launch-host.sh"):
                parser.error("Manifest указывает на другую установку; удалите его вручную.")
            destination.unlink()
        print(f"Удалён manifest: {destination}")
        print("Отключите расширение Firefox. Настройки MCP этим скриптом не меняются.")
        return
    # Pin the interpreter selected by the user, rather than depending on Firefox's PATH.
    launcher = ROOT / "bridge/launch-host.sh"
    launcher.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -u " + shlex.quote(str(ROOT / "bridge/native_host.py")) + " \"$@\"\n")
    launcher.chmod(0o700)
    manifest = {"name": NAME, "description": "Local Firefox Agent Bridge", "path": str(launcher),
                "type": "stdio", "allowed_extensions": ["firefox-agent-bridge@local.codex"]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and json.loads(destination.read_text()).get("path") != str(launcher):
        parser.error("Уже установлена другая копия моста. Сначала удалите её manifest или используйте прежнюю папку.")
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, destination)
    command = ["codex", "mcp", "add", "firefox-local", "--", sys.executable, str(ROOT / "bridge/mcp_server.py")]
    print(f"Мост зарегистрирован: {destination}")
    print(f"Firefox: about:debugging#/runtime/this-firefox → Загрузить временное дополнение → {ROOT / 'extension/manifest.json'}")
    print("Подключение MCP к Codex (выполните отдельно):\n" + shlex.join(command))
    print("Затем перезапустите Codex или обновите подключение MCP. Для текущего чата также доступен bridge/cli.py.")


if __name__ == "__main__":
    main()
