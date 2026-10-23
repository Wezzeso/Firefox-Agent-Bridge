#!/usr/bin/env python3
"""Agent-friendly command-line client."""
import argparse
import base64
import json
from pathlib import Path
import sys

from common import request


def main():
    parser = argparse.ArgumentParser(description="Firefox Agent Bridge (local only)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("tabs")
    commands.add_parser("stop")
    raw = commands.add_parser("request")
    raw.add_argument("method")
    raw.add_argument("params", nargs="?", default="{}", help="JSON object, or - to read from stdin")
    shot = commands.add_parser("screenshot")
    shot.add_argument("tab_id", type=int)
    shot.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "request":
            params = json.loads(sys.stdin.read() if args.params == "-" else args.params)
            result = request(args.method, params)
        elif args.command == "screenshot":
            data = request("page.screenshot", {"tabId": args.tab_id})
            image = base64.b64decode(data["dataUrl"].split(",", 1)[1], validate=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(image)
            result = {"path": str(args.output.resolve()), "mimeType": data["mimeType"]}
        else:
            method = {"status": "bridge.status", "tabs": "tabs.list", "stop": "bridge.stop"}[args.command]
            result = request(method)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
