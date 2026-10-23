"""Installer regressions run in disposable homes; never change a real user's config."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import installer_lib as lib


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefox-agent-installer-")
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "user home"
        self.home.mkdir()
        self.prefix = self.home / "app 'quoted' $special"
        self.fake_firefox = self.base / "Firefox executable"
        self.fake_firefox.write_text("#!/bin/sh\necho 'Mozilla Firefox 158.0.1'\n")
        self.fake_firefox.chmod(0o755)
        self.config = self.home / ".codex/config.toml"
        self.config.parent.mkdir()
        self.original = 'model = "keep-this"\n\n[mcp_servers.existing]\ncommand = "keep-this-too"\n'
        self.config.write_text(self.original)
        self.env = {**os.environ, "HOME": str(self.home), "CODEX_HOME": str(self.config.parent),
                    "FIREFOX_AGENT_HOME": str(self.prefix), "FIREFOX_AGENT_TEST_ROOT": "1",
                    "FIREFOX_AGENT_SOCKET": str(self.base / "ipc/bridge.sock")}

    def tearDown(self):
        self.temp.cleanup()

    def install(self, source=ROOT, success=True):
        result = subprocess.run([sys.executable, str(source / "setup.py"), "--no-launch", "--prefix", str(self.prefix),
                                 "--firefox", str(self.fake_firefox)], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def command(self, *args):
        return subprocess.run([str(self.home / ".local/bin/firefox-agent"), *args], env=self.env, text=True, capture_output=True)

    def clone_source(self):
        target = self.base / "modified source"
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "dist", "profile", "runtime"))
        return target

    def test_one_command_is_idempotent_preserves_config_and_handles_spaces(self):
        self.install()
        first = self.config.read_text()
        self.assertTrue(first.startswith(self.original))
        self.assertEqual(first.count(lib.BEGIN), 1)
        self.assertEqual(self.command("doctor", "--installed-only").returncode, 0)
        self.assertTrue((self.prefix / "current").is_symlink())
        self.install()
        self.assertEqual(self.config.read_text(), first)
        manifest = lib.native_manifest(self.home)
        self.assertEqual(json.loads(manifest.read_text())["path"], str(self.prefix / "current/bridge/launch-host.sh"))
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)

    def test_update_preserves_profile_and_uninstall_keeps_user_data(self):
        self.install()
        profile = self.prefix / "profile"
        profile.mkdir()
        sentinel = profile / "user-data.txt"
        sentinel.write_text("preserve this profile")
        previous = (self.prefix / "current").resolve()
        source = self.clone_source()
        with (source / "bridge/cli.py").open("a") as stream:
            stream.write("\n# Changed release for the update regression.\n")
        self.install(source)
        self.assertNotEqual((self.prefix / "current").resolve(), previous)
        self.assertTrue(previous.is_dir())
        self.assertEqual(sentinel.read_text(), "preserve this profile")
        result = self.command("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_text(), "preserve this profile")
        self.assertEqual(self.config.read_text(), self.original + "\n")
        self.assertFalse(lib.native_manifest(self.home).exists())
        self.assertFalse((self.home / ".local/bin/firefox-agent").exists())
        self.install()
        self.assertEqual(sentinel.read_text(), "preserve this profile")

    def test_existing_mcp_table_is_not_overwritten(self):
        self.config.write_text(self.original + f"\n[mcp_servers.{lib.SERVER_NAME}]\ncommand = \"someone-else\"\n")
        before = self.config.read_text()
        self.install(success=False)
        self.assertEqual(self.config.read_text(), before)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(lib.native_manifest(self.home).exists())

    def test_existing_native_host_is_not_overwritten(self):
        target = lib.native_manifest(self.home)
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"path": "/someone/elses/native-host"}))
        self.install(success=False)
        self.assertEqual(json.loads(target.read_text())["path"], "/someone/elses/native-host")
        self.assertEqual(self.config.read_text(), self.original)

    def test_failed_self_check_rolls_back_every_registered_component(self):
        source = self.clone_source()
        path = source / "manage.py"
        path.write_text(path.read_text().replace('return 0 if all(ok for _, ok in checks) else 1', 'return 1'))
        self.install(source, success=False)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(lib.native_manifest(self.home).exists())
        self.assertFalse((self.home / ".local/bin/firefox-agent").exists())
        self.assertFalse((self.prefix / "current").exists())

    def test_mac_native_host_location(self):
        self.assertEqual(lib.native_manifest(self.home, "darwin"), self.home / "Library/Application Support/Mozilla/NativeMessagingHosts/local.firefox_agent_bridge.json")

    def test_mac_application_launcher_and_ownership(self):
        with patch.object(lib.sys, "platform", "darwin"):
            shortcuts = lib.install_shortcuts(self.prefix / "current", self.prefix, sys.executable, self.home)
            bundle = self.home / "Applications/Firefox Agent.app"
            self.assertIn(str(bundle), shortcuts)
            binary = bundle / "Contents/MacOS/firefox-agent"
            self.assertTrue(os.access(binary, os.X_OK))
            import plistlib
            info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
            self.assertEqual(info["CFBundleExecutable"], "firefox-agent")
            binary.write_text("belongs to someone else")
            with self.assertRaises(RuntimeError):
                lib.install_shortcuts(self.prefix / "current", self.prefix, sys.executable, self.home)

    def test_archive_rejects_traversal_and_links(self):
        for name, link in [("project/../../escaped", False), ("project/link", True)]:
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                info = tarfile.TarInfo(name)
                if link:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/etc/passwd"
                else:
                    info.size = 1
                archive.addfile(info, None if link else io.BytesIO(b"x"))
            with self.assertRaises(RuntimeError):
                lib.extract_archive(output.getvalue(), self.base / "extract")
        self.assertFalse((self.base / "escaped").exists())

    def test_exact_shell_bootstrap_installs_from_an_archive(self):
        fixture = self.base / "source.tar.gz"
        with tarfile.open(fixture, "w:gz") as archive:
            for path in ROOT.rglob("*"):
                if path.is_file() and not any(part in [".git", "__pycache__", "dist"] for part in path.relative_to(ROOT).parts):
                    archive.add(path, arcname="firefox-agent-bridge/" + str(path.relative_to(ROOT)))
        env = {**self.env, "FIREFOX_AGENT_SOURCE_ARCHIVE": str(fixture), "FIREFOX_AGENT_REPOSITORY": "example/firefox-agent-bridge"}
        result = subprocess.run(["sh", str(ROOT / "install.sh"), "--no-launch", "--prefix", str(self.prefix),
                                 "--firefox", str(self.fake_firefox)], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.command("doctor", "--installed-only").returncode, 0)


if __name__ == "__main__":
    unittest.main()
