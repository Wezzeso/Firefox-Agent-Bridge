#!/bin/sh
# One-command bootstrap. Source and runtime need only Python's standard library.
set -eu
REPOSITORY="Wezzeso/Firefox-Agent-Bridge"
REF="v1.1.0"
case "$(uname -s)" in Linux|Darwin) ;; *) echo "Requires Linux or macOS. Windows is not supported yet." >&2; exit 1;; esac
if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Install Python 3.9+ from https://www.python.org/downloads/ then run this command again." >&2
  exit 1
fi
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else "Python 3.9+ is required")'
TASK_TEMP=$(mktemp -d "${TMPDIR:-/tmp}/firefox-agent.XXXXXXXX")
trap 'rm -rf "$TASK_TEMP"' EXIT HUP INT TERM
# Environment overrides are useful for pinned revisions and offline installer tests.
export FIREFOX_AGENT_SOURCE_REPOSITORY="${FIREFOX_AGENT_REPOSITORY:-$REPOSITORY}"
export FIREFOX_AGENT_SOURCE_REF="${FIREFOX_AGENT_REF:-$REF}"
"$PYTHON" - "$TASK_TEMP" <<'PY'
import io, os, re, sys, tarfile, urllib.request
from pathlib import Path, PurePosixPath
base = Path(sys.argv[1])
repo = os.environ['FIREFOX_AGENT_SOURCE_REPOSITORY']
ref = os.environ['FIREFOX_AGENT_SOURCE_REF']
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or repo.startswith('YOUR_'):
    sys.exit('Publisher must set the GitHub repository first. Local install: python3 setup.py')
if not re.fullmatch(r'[A-Za-z0-9_.-]+', ref):
    sys.exit('Invalid source revision')
fixture = os.environ.get('FIREFOX_AGENT_SOURCE_ARCHIVE')
if fixture:
    data = Path(fixture).read_bytes()
else:
    url = f'https://codeload.github.com/{repo}/tar.gz/{ref}'
    print('Downloading Firefox Agent Bridge...', flush=True)
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(8 * 1024 * 1024 + 1)
if len(data) > 8 * 1024 * 1024:
    sys.exit('Source download is too large')
destination = base / 'source'
destination.mkdir()
with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
    members = archive.getmembers()
    if len(members) > 2000 or sum(m.size for m in members) > 32 * 1024 * 1024:
        sys.exit('Source archive is too large')
    top = None
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or '\\' in member.name or not path.parts:
            sys.exit('Unsafe source archive path')
        top = top or path.parts[0]
        if path.parts[0] != top or not (member.isfile() or member.isdir()):
            sys.exit('Source archive contains unsafe entries')
        target = destination.joinpath(*path.parts[1:])
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif len(path.parts)>1:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as stream:
                target.write_bytes(stream.read())
if not (destination/'setup.py').is_file():
    sys.exit('Invalid project source archive')
PY
"$PYTHON" "$TASK_TEMP/source/setup.py" "$@"
