# Development

No runtime dependencies beyond Python 3.9+ and standard Firefox 128+. Supported installer targets are Linux and macOS; live browser verification currently runs on Linux.

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
FIREFOX_AGENT_LIVE_TEST=1 python3 -m unittest discover -s tests -p test_live_install.py -v
python3 scripts/build.py
```

The real installation test uses a disposable HOME, dedicated profile and private socket. Never run destructive installer tests against your normal home directory. Advanced browser tests additionally use geckodriver from Mozilla and run with `python3 tests/test_firefox.py --geckodriver /absolute/path/to/geckodriver`.

## Repository setup

Before first publishing, configure canonical repository links:

```sh
python3 scripts/configure_repository.py OWNER/REPOSITORY
```

Commit the source to `main`. Do not commit profiles, runtime state, generated native launchers, installed settings, test reports containing local paths or credentials. `.gitignore` and the release builder exclude these files.

## Release

1. Update `VERSION`, the extension manifest, MCP server version, background protocol version, changelog and `REF` in `install.sh` together.
2. Run the full test suite and `python3 scripts/build.py`.
3. Commit and push `main`. A change to `VERSION` triggers release publication automatically. The first source push includes `VERSION` and publishes the first release. A manually pushed matching `vX.Y.Z` tag can also trigger publication.

The release workflow checks the version, tests the installer and real browser, builds the assets, creates the version tag at the tested commit and publishes a GitHub Release. Bootstrap is pinned to that tag. `REF` must match the version being published; the one-command URL becomes available after this workflow succeeds. Updates reuse the canonical installer from `main` and install its pinned release.

Keep GitHub Actions pinned to full commit IDs. Do not add access to secrets on pull-request workflows. Live browser tests do not use real accounts or authorized websites.
