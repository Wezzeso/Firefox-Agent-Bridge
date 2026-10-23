#!/usr/bin/env python3
"""Configure public repository links without touching installed user state."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if len(sys.argv) != 2 or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", sys.argv[1]):
    sys.exit("Usage: python3 scripts/configure_repository.py OWNER/REPOSITORY")
previous = (ROOT / "REPOSITORY").read_text().strip()
for name in ["REPOSITORY", "install.sh", "README.md", "README.en.md"]:
    path = ROOT / name
    if path.exists():
        path.write_text(path.read_text().replace(previous, sys.argv[1]))
print("Repository:", sys.argv[1])
