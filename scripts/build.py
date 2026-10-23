#!/usr/bin/env python3
"""Build portable source and temporary-extension archives without dependencies."""
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
version = (ROOT / "VERSION").read_text().strip()
manifest = json.loads((ROOT / "extension/manifest.json").read_text())
if manifest["version"] != version:
    raise SystemExit("VERSION and extension/manifest.json do not match")
output = ROOT / "dist"
output.mkdir(exist_ok=True)
excluded = {".git", "__pycache__", ".venv", "dist", "profile", "runtime", "releases", "current"}
files = []
for path in sorted(ROOT.rglob("*")):
    rel = path.relative_to(ROOT)
    if any(part in excluded for part in rel.parts):
        continue
    if path.is_symlink():
        raise SystemExit(f"Refusing to package a symlink: {rel}")
    if path.is_file() and path.name not in {"launch-host.sh", "INSTALLED.txt", "TEST_RESULTS.txt", "installation.json"} and not path.name.endswith((".pyc", ".log")) and not path.name.startswith(".env"):
        files.append(path)
name = f"firefox-agent-bridge-{version}"
products = []
source = output / (name + ".tar.gz")
with tarfile.open(source, "w:gz") as archive:
    for path in files:
        archive.add(path, arcname=name + "/" + str(path.relative_to(ROOT)))
products.append(source)
for filename, selected, prefix in [(name + ".zip", files, ROOT.parent),
                                  ("firefox-agent-extension-" + version + ".zip", [p for p in files if p.is_relative_to(ROOT / "extension")], ROOT / "extension")]:
    # is_relative_to is available in Python 3.9+.
    target = output / filename
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in selected:
            arcname = str(path.relative_to(prefix))
            archive.write(path, arcname)
    products.append(target)
(output / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in products))
for path in products:
    print(path.name, path.stat().st_size, "bytes")
