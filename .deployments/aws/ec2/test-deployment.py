"""Offline target-helper checks. Run only in an isolated network-disabled container."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import unittest


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        assert Path("/.dockerenv").exists(), "Run this test in the documented isolated container"
        self.root = Path.home()
        assert self.root == Path("/root")
        for name in ("ec2", "realestate-releases", "realestate-current", "mock-bin"):
            path = self.root / name
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        config = self.root / "ec2"
        config.mkdir()
        (config / ".env").write_text("DOMAIN=deployment.example\nWORKSPACE_PATH=/tmp/workspace\nAPPLICATION_IMAGE=old\nGATEWAY_IMAGE=old\n")
        (config / "app.env").write_text("DJANGO_SECRET_KEY=test-only\n")
        (config / "global-bundle.pem").write_text("test-only\n")
        for relative in ("user/media", "output/media", "legacy-media"):
            (Path("/tmp/workspace") / relative).mkdir(parents=True, exist_ok=True)
        self.sha = "a" * 40
        archive = self.root / f"realestate-{self.sha}.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for name, data in {"release.txt": self.sha, "images.tar": "mock", "ec2/compose.yml": "mock", "ec2/compose.preview.yml": "mock"}.items():
                content = data.encode()
                info = tarfile.TarInfo(name)
                info.size = len(content)
                stream.addfile(info, io.BytesIO(content))
        self.digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.log = self.root / "commands.jsonl"
        self.log.write_text("")
        mock_bin = self.root / "mock-bin"
        mock_bin.mkdir()
        mock = """#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['RELEASE_TEST_LOG'],'a') as stream: stream.write(json.dumps([name]+args)+'\\n')
if name=='awk': print('8388608')
if name=='findmnt': print('ext4' if os.environ.get('FAIL_MOUNT') else 'nfs4')
if name=='docker' and 'migrate' in args and '--check' in args and os.environ.get('PENDING_MIGRATIONS'): sys.exit(1)
if name=='curl' and os.environ.get('FAIL_HEALTH'): sys.exit(22)
"""
        for name in ("docker", "curl", "findmnt", "awk"):
            path = mock_bin / name
            path.write_text(mock)
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(mock_bin) + ":" + os.environ["PATH"], RELEASE_TEST_LOG=str(self.log))

    def run_release(self, mode="preview", digest=None, demo="false", **flags):
        result = subprocess.run(["bash", "/checks/ec2/deploy-release.sh", self.sha, digest or self.digest, mode, demo],
                                env=dict(self.env, **flags), capture_output=True, text=True)
        self.commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result

    def test_preview_and_https_use_correct_services(self):
        for mode in ("preview", "https"):
            with self.subTest(mode=mode):
                self.setUp()
                result = self.run_release(mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                up = next(command for command in self.commands if command[0] == "docker" and "up" in command)
                self.assertEqual("https" in up, mode == "https")
                self.assertEqual("compose.preview.yml" in up, mode == "preview")
                self.assertNotIn("worker", up)
                self.assertTrue((self.root / "realestate-current").is_symlink())
                self.assertEqual((self.root / "ec2/.env").read_text().splitlines()[2], "APPLICATION_IMAGE=old")
                self.assertEqual((self.root / "realestate-current/.deployment-mode").read_text().strip(), mode)

    def test_demo_visibility_is_explicit_in_both_transport_modes(self):
        for mode in ("preview", "https"):
            for demo in ("true", "false"):
                with self.subTest(mode=mode, demo=demo):
                    self.setUp()
                    source = self.root / "ec2/app.env"
                    source.write_text(source.read_text() + "PROJECT_PUBLIC_PREVIEW_ENABLED=true\n")
                    before = source.read_bytes()
                    result = self.run_release(mode, demo=demo)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    release_env = (self.root / "realestate-current/app.env").read_text()
                    self.assertEqual(release_env.count("PROJECT_PUBLIC_PREVIEW_ENABLED="), 1)
                    self.assertIn(f"PROJECT_PUBLIC_PREVIEW_ENABLED={demo}\n", release_env)
                    self.assertIn("DJANGO_SECRET_KEY=test-only\n", release_env)
                    self.assertEqual(source.read_bytes(), before)

    def test_invalid_demo_value_is_rejected_before_any_commands(self):
        self.assertNotEqual(self.run_release(demo="invalid").returncode, 0)
        self.assertEqual(self.commands, [])

    def test_pending_migrations_do_not_replace_running_services(self):
        self.assertNotEqual(self.run_release(PENDING_MIGRATIONS="1").returncode, 0)
        self.assertFalse(any("up" in command for command in self.commands))
        self.assertFalse((self.root / "realestate-current").exists())

    def test_missing_mount_and_bad_checksum_stop_before_loading_images(self):
        for flags in ({"FAIL_MOUNT": "1"}, {"digest": "b" * 64}):
            with self.subTest(flags=flags):
                self.setUp()
                self.assertNotEqual(self.run_release(**flags).returncode, 0)
                self.assertFalse(any(command[:2] == ["docker", "load"] for command in self.commands))

    def test_failed_health_does_not_mark_a_release_successful(self):
        self.assertNotEqual(self.run_release(FAIL_HEALTH="1").returncode, 0)
        self.assertFalse((self.root / "realestate-current").exists())

    def test_invalid_mode_is_rejected(self):
        self.assertNotEqual(self.run_release("invalid").returncode, 0)
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
