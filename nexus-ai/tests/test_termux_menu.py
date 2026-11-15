"""Offline unit tests for the Termux menu's M5 helper and release updater."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m5 = module("nexus_device_tests", "nexus_device.py")
up = module("nexus_update_tests", "nexus_update.py")


def release_zip(path: Path, malicious: str | None = None) -> None:
    root = "Nexus-AI-0.1.3-alpha/"
    paths = {
        "nexus-ai/nexusctl.py": "pass\n",
        "nexus-ai/nexus_tui.py": "pass\n",
        "nexus-ai/nexus_device.py": "pass\n",
        "nexus-ai/nexus_update.py": "pass\n",
        "nexus-ai/android_bridge/app.py": "pass\n",
        "nexus-ai/android_bridge/supervisor.py": "pass\n",
        "nexus-firmware/firmware/platformio.ini": "[env:m5stickc_plus2]\n",
        "nexus-firmware/firmware/src/main.cpp": "// source\n",
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, contents in paths.items():
            z.writestr(root + name, contents)
        if malicious is not None:
            z.writestr(root + malicious, "unsafe\n")


class ArchiveTest(unittest.TestCase):
    def test_release_extracts_only_source_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            archive = base / "source.zip"
            release_zip(archive)
            app, fw = up.extract_sources(archive, base / "stage")
            self.assertTrue((app / "nexus_tui.py").is_file())
            self.assertTrue((fw / "src/main.cpp").is_file())

    def test_reject_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            archive = base / "source.zip"
            release_zip(archive, "../wrong.py")
            with self.assertRaises(ValueError):
                up.extract_sources(archive, base / "stage")

    def test_reject_secret_file(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            archive = base / "source.zip"
            release_zip(archive, "nexus-firmware/firmware/src/config_local.h")
            with self.assertRaises(ValueError):
                up.extract_sources(archive, base / "stage")

    def test_tag_validation(self):
        self.assertEqual(up.validate_tag("v0.1.3-alpha"), "v0.1.3-alpha")
        for bad in ("main", "../../latest", "v0.1.3-alpha%0a", "v1.2.3/extra"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                up.validate_tag(bad)


class DeviceTest(unittest.TestCase):
    def test_wifi_writes_private_header_without_echo(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fw = base / "fw"
            (fw / "src").mkdir(parents=True)
            (fw / "platformio.ini").write_text("[env:m5stickc_plus2]\n")
            (fw / "src/main.cpp").write_text("// source\n")
            cfg = base / "config"
            out = io.StringIO()
            fake_input = types.SimpleNamespace(isatty=lambda: True)
            with patch.object(m5, "FIRMWARE", fw), patch.object(m5, "CONFIG", fw / "src/config_local.h"), \
                 patch.object(m5, "LOCAL", cfg), patch.object(m5, "_device_token", return_value="verysecretdevicetoken1234567"), \
                 patch.object(m5.sys, "stdin", fake_input), patch("builtins.input", return_value="MyNet"), \
                 patch.object(m5.getpass, "getpass", return_value="supersecretpass"), \
                 patch("sys.stdout", out):
                self.assertTrue(m5.wifi())
            text = (fw / "src/config_local.h").read_text()
            self.assertIn('#define NEXUS_WIFI_SSID "MyNet"', text)
            self.assertIn('#define NEXUS_WIFI_PASSWORD "supersecretpass"', text)
            self.assertNotIn("supersecretpass", out.getvalue())
            self.assertNotIn("verysecretdevicetoken1234567", out.getvalue())

    def test_parse_wifi_macro(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config_local.h"
            target.write_text('#define NEXUS_WIFI_SSID "HomeNet"\n')
            with patch.object(m5, "CONFIG", target):
                self.assertEqual(m5._value("NEXUS_WIFI_SSID"), "HomeNet")


class UpdateTest(unittest.TestCase):
    def test_local_zip_update_preserves_wifi(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            app = base / "nexus-ai"
            fw = base / "nexus-firmware" / "firmware"
            app.mkdir()
            (app / "nexusctl.py").write_text("old version\n")
            (fw / "src").mkdir(parents=True)
            (fw / "platformio.ini").write_text("old fw")
            header = fw / "src/config_local.h"
            header.write_text('#define NEXUS_WIFI_PASSWORD "private"\n')
            archive = base / "new.zip"
            release_zip(archive)
            cache = base / ".cache/nexus"
            cfg = base / ".config/nexus"
            version = cfg / "installed_version"
            fake_ctl = types.SimpleNamespace(managed_pid=lambda: None, request_status=lambda: False,
                                             start=lambda: None, stop=lambda: None)
            with patch.object(up, "APP", app), patch.object(up, "FW", fw), \
                 patch.object(up, "CACHE", cache), patch.object(up, "CFG", cfg), \
                 patch.object(up, "VERSION_FILE", version), \
                 patch.dict(sys.modules, {"nexusctl": fake_ctl}):
                self.assertTrue(up.update("v0.1.3-alpha", archive))
                self.assertEqual(version.read_text().strip(), "v0.1.3-alpha")
                self.assertIn("private", header.read_text())
                self.assertTrue((app / "nexus_device.py").is_file())
                self.assertEqual(len(list((cache / "backups").glob("*/nexus-ai"))), 1)


if __name__ == "__main__":
    unittest.main()
