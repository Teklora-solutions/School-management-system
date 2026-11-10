"""Deployment contracts; run with Python's standard library, no Django required."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class InitializationTests(unittest.TestCase):
    def test_initialization_is_private_idempotent_and_does_not_touch_sqlite(self):
        spec = importlib.util.spec_from_file_location("init_docker", ROOT / "scripts/init_docker.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(ROOT / ".env.example", root / ".env.example")
            (root / "backend").mkdir()
            sqlite = root / "backend/db.sqlite3"
            sqlite.write_bytes(b"existing database must not change")
            existing_env = (
                "SECRET_KEY=existing-secret\nCUSTOM_SETTING=keep-me\n"
                "POSTGRES_USER=school\n"
                "POSTGRES_PASSWORD='manual-test-only$pass:@word'\n"
            )
            (root / ".env").write_text(existing_env)
            with patch.object(module, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                module.main()
                original = (root / ".env").read_bytes()
                token = root / "data/secrets/cloudflared-token"
                token.write_text("existing-token")
                module.main()
            self.assertEqual((root / ".env").read_bytes(), original)
            self.assertNotIn(b"GENERATE_", original)
            self.assertEqual(token.read_text(), "existing-token")
            self.assertEqual((root / "data").stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "data").stat().st_uid, os.getuid())
            self.assertEqual((root / "data/postgres").stat().st_mode & 0o777, 0o755)
            self.assertEqual(token.stat().st_mode & 0o777, 0o644)
            self.assertEqual(sqlite.read_bytes(), b"existing database must not change")
            self.assertTrue((root / ".env").read_text().startswith(existing_env))

    def test_fresh_setup_never_generates_a_database_password(self):
        spec = importlib.util.spec_from_file_location("init_docker", ROOT / "scripts/init_docker.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(ROOT / ".env.example", root / ".env.example")
            with patch.object(module, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                module.main()
            values = module.assignments((root / ".env").read_text())
            database_keys = {key for key in values if key.startswith(("POSTGRES_", "APP_DB_"))}
            self.assertEqual(database_keys, {"POSTGRES_USER", "POSTGRES_PASSWORD"})
            self.assertEqual(values["POSTGRES_USER"], "school")
            self.assertEqual(values["POSTGRES_PASSWORD"], "")
            self.assertNotEqual(values["SECRET_KEY"], "GENERATE_DJANGO_SECRET")

    def test_setup_refuses_to_run_as_root_before_creating_data(self):
        spec = importlib.util.spec_from_file_location("init_docker", ROOT / "scripts/init_docker.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(module, "ROOT", root),
                patch.object(module.os, "geteuid", return_value=0),
                self.assertRaisesRegex(SystemExit, "normal user"),
            ):
                module.main()
            self.assertFalse((root / "data").exists())

    def test_data_is_excluded_from_git_and_the_build_context(self):
        for name in (".gitignore", ".dockerignore"):
            self.assertIn("/data/", (ROOT / name).read_text().splitlines())
        ignored = subprocess.run(
            ["git", "check-ignore", ".env", "data/media/test.png", "data/secrets/cloudflared-token"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        self.assertEqual(len(ignored.stdout.splitlines()), 3)


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required for config validation")
class ComposeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, SECRET_KEY="test-only", POSTGRES_PASSWORD="test-user-supplied-only",
                   POSTGRES_USER="chosen_user", PUBLIC_HOSTNAME="school.example.com",
                   DATABASE_URL="sqlite:///not-the-stack.sqlite3", CACHE_URL="locmemcache://")
        result = subprocess.run(
            ["docker", "compose", "--env-file", ".env.example", "config", "--format", "json"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True,
        )
        cls.config = json.loads(result.stdout)

    def test_compose_rejects_a_missing_user_supplied_database_password(self):
        environment = dict(os.environ, SECRET_KEY="test-only", POSTGRES_USER="school",
                           POSTGRES_PASSWORD="", PUBLIC_HOSTNAME="school.example.com")
        result = subprocess.run(
            ["docker", "compose", "--env-file", ".env.example", "config", "--quiet"],
            cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("POSTGRES_PASSWORD", result.stderr)

    def test_no_service_publishes_a_host_port_or_uses_host_networking(self):
        for name, service in self.config["services"].items():
            with self.subTest(service=name):
                self.assertFalse(service.get("ports"))
                self.assertNotEqual(service.get("network_mode"), "host")

    def test_durable_mounts_are_in_data_and_configuration_is_read_only(self):
        self.assertFalse(self.config.get("volumes"), "Do not use Docker-managed named volumes")
        for name, service in self.config["services"].items():
            for volume in service.get("volumes", []):
                with self.subTest(service=name, mount=volume):
                    self.assertEqual(volume["type"], "bind")
                    self.assertIs(volume["bind"]["create_host_path"], False)
                    source = Path(volume["source"])
                    if not source.is_relative_to(ROOT / "data"):
                        self.assertTrue(source.is_relative_to(ROOT / "deploy"))
                        self.assertTrue(volume.get("read_only"))

    def test_database_is_private_and_uses_the_supplied_credentials(self):
        services = self.config["services"]
        self.assertTrue(self.config["networks"]["backend"]["internal"])
        for name in ("db", "redis"):
            self.assertEqual(set(services[name]["networks"]), {"backend"})
        self.assertEqual(services["db"]["environment"]["POSTGRES_USER"], "chosen_user")
        self.assertEqual(services["db"]["environment"]["POSTGRES_PASSWORD"], "test-user-supplied-only")
        self.assertEqual(services["db"]["environment"]["POSTGRES_DB"], "school")

    def test_database_and_cache_urls_are_automatic_for_all_django_services(self):
        for name in ("app-init", "app", "worker"):
            with self.subTest(service=name):
                environment = self.config["services"][name]["environment"]
                self.assertEqual(environment["DATABASE_URL"], "postgres://chosen_user@db:5432/school")
                self.assertEqual(environment["POSTGRES_PASSWORD"], "test-user-supplied-only")
                self.assertEqual(environment["CACHE_URL"], "redis://redis:6379/1")

    def test_web_and_worker_wait_for_one_shot_migrations(self):
        for name in ("app", "worker"):
            self.assertEqual(
                self.config["services"][name]["depends_on"]["app-init"]["condition"],
                "service_completed_successfully",
            )
        self.assertEqual(self.config["services"]["app-init"]["restart"], "no")

    def test_tunnel_uses_a_secret_file_not_a_command_line_token(self):
        tunnel = self.config["services"]["cloudflared"]
        self.assertIn("--token-file", tunnel["command"])
        self.assertNotIn("--token", tunnel["command"])
        self.assertNotIn("TUNNEL_TOKEN", tunnel.get("environment", {}))
        token = Path(self.config["secrets"]["cloudflared_token"]["file"])
        self.assertTrue(token.is_relative_to(ROOT / "data/secrets"))

    def test_private_uploads_are_not_exposed_by_a_blanket_media_alias(self):
        nginx = (ROOT / "deploy/nginx.conf").read_text()
        self.assertIn("location /media/ {\n            return 404;", nginx)
        self.assertIn("proxy_set_header X-Forwarded-Proto https;", nginx)


if __name__ == "__main__":
    unittest.main()
