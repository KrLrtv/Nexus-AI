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


    def test_rollback_and_cleanup_retains_wifi_and_one_version(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            app = base / "nexus-ai"
            fw = base / "nexus-firmware" / "firmware"
            app.mkdir()
            (app / "nexusctl.py").write_text("old code\n")
            (fw / "src").mkdir(parents=True)
            (fw / "platformio.ini").write_text("old firmware config")
            (fw / "src" / "main.cpp").write_text("// old firmware\n")
            (fw / "src" / "config_local.h").write_text('#define NEXUS_WIFI_PASSWORD "first-secret"\n')
            cache = base / ".cache/nexus"
            cfg = base / ".config/nexus"
            cfg.mkdir(parents=True)
            version = cfg / "installed_version"
            version.write_text("v0.1.2-alpha\n")
            archive = base / "release.zip"
            release_zip(archive)
            fake_ctl = types.SimpleNamespace(managed_pid=lambda: None, request_status=lambda: False,
                                             start=lambda: None, stop=lambda: None)
            with patch.object(up, "APP", app), patch.object(up, "FW", fw), \
                 patch.object(up, "CACHE", cache), patch.object(up, "CFG", cfg), \
                 patch.object(up, "VERSION_FILE", version), \
                 patch.dict(sys.modules, {"nexusctl": fake_ctl}):
                self.assertTrue(up.update("v0.1.3-alpha", archive))
                self.assertEqual(len(up.backup_candidates()), 1)
                self.assertEqual(up.backup_version(up.backup_candidates()[0]), "v0.1.2-alpha")
                # A second upgrade must delete the older snapshot rather than hoard it.
                (fw / "src" / "config_local.h").write_text('#define NEXUS_WIFI_PASSWORD "current-secret"\n')
                self.assertTrue(up.update("v0.1.4-alpha", archive))
                self.assertEqual(len(up.backup_candidates()), 1)
                self.assertEqual(up.backup_version(up.backup_candidates()[0]), "v0.1.3-alpha")

                self.assertTrue(up.rollback(approve=True))
                self.assertEqual(version.read_text().strip(), "v0.1.3-alpha")
                self.assertIn("current-secret", (fw / "src" / "config_local.h").read_text())
                self.assertEqual(len(up.backup_candidates()), 1)
                self.assertEqual(up.backup_version(up.backup_candidates()[0]), "v0.1.4-alpha")

                # One more rollback toggles back to the previously active release.
                self.assertTrue(up.rollback(approve=True))
                self.assertEqual(version.read_text().strip(), "v0.1.4-alpha")
                self.assertIn("current-secret", (fw / "src" / "config_local.h").read_text())
                self.assertEqual(len(up.backup_candidates()), 1)

                self.assertTrue(up.clear_backups(confirm=True))
                self.assertEqual(len(up.backup_candidates()), 0)
                self.assertEqual(version.read_text().strip(), "v0.1.4-alpha")

    def test_rollback_without_backup_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(up, "CACHE", Path(d) / ".cache"):
                self.assertFalse(up.rollback(approve=True))


class TuiErrorTest(unittest.TestCase):
    def _tui(self):
        # The Termux control center imports nexusctl by its actual installed
        # path. Stub only this dependency for independent CI testing.
        engine = types.SimpleNamespace(
            request_status=lambda: 0,
            managed_pid=lambda: None,
            config=lambda: {},
            MODEL_DEFAULT="gemini-test",
        )
        with patch.dict(sys.modules, {
            "nexusctl": engine,
            "nexus_device": m5,
            "nexus_update": up,
            "nexus_autostart": module("nexus_autostart_tui_tests", "nexus_autostart.py"),
        }):
            return module("nexus_tui_tests", "nexus_tui.py")

    def test_false_result_shows_real_error_instead_of_runtimeerror(self):
        tui = self._tui()
        screen = tui.ControlCenter()
        def simulated_missing_wifi():
            print("Wi-Fi M5 не настроен. Выбери пункт 2 для настройки.")
            return False
        with patch("sys.stdout", io.StringIO()):
            screen.popup(simulated_missing_wifi)
        self.assertEqual(screen.flash,
                         "Wi-Fi M5 не настроен. Выбери пункт 2 для настройки.")
        self.assertTrue(screen.flash_error)
        self.assertNotIn("RuntimeError", screen.flash)

    def test_success_keeps_success_status(self):
        tui = self._tui()
        screen = tui.ControlCenter()
        def simulated_success():
            print("Сборка завершена")
            return True
        with patch("sys.stdout", io.StringIO()):
            screen.popup(simulated_success)
        self.assertEqual(screen.flash, "Сборка завершена")
        self.assertFalse(screen.flash_error)


class MenuOnlyWorkflowTest(unittest.TestCase):
    def _ui(self):
        fake_engine = types.SimpleNamespace(request_status=lambda: 0, managed_pid=lambda: None,
                                            config=lambda: {}, MODEL_DEFAULT="gemini-test")
        startup = module("nexus_autostart_tests", "nexus_autostart.py")
        with patch.dict(sys.modules, {
            "nexusctl": fake_engine, "nexus_device": m5, "nexus_update": up,
            "nexus_autostart": startup,
        }):
            tui = module("nexus_tui_menu_only_tests", "nexus_tui.py")
        return tui.ControlCenter(), tui

    def test_all_core_actions_are_accessible_from_menu(self):
        screen, _ = self._ui()
        root = dict((action,label) for label,action in screen.entries())
        self.assertIn("update", root)
        self.assertIn("versions", root)
        self.assertIn("m5", root)
        screen.page = "versions"
        actions = {a for _,a in screen.entries()}
        self.assertTrue({"versions-preview","versions-stable","rollback",
                         "versions-backups","versions-clear",
                         "versions-autostart"}.issubset(actions))

    def test_preview_install_is_opt_in_and_restarts_interface(self):
        screen, tui = self._ui()
        commands=[]
        def fake_run(args, check=False):
            commands.append(args)
            return types.SimpleNamespace(returncode=0)
        with patch.object(tui.subprocess, "run",side_effect=fake_run), \
             patch("builtins.input",return_value="2.0"), \
             patch("sys.stdout",io.StringIO()):
            screen.handle("versions-preview")
        self.assertEqual(commands[0][-1], "--preview")
        self.assertTrue(screen.relaunch)
        self.assertFalse(screen.run)

    def test_preview_cancel_never_downloads(self):
        screen, tui = self._ui()
        with patch.object(tui.subprocess, "run") as runner, \
             patch("builtins.input",return_value="отмена"), \
             patch("sys.stdout",io.StringIO()):
            screen.handle("versions-preview")
        runner.assert_not_called()
        self.assertFalse(screen.relaunch)

    def test_regular_one_button_update_uses_current_channel(self):
        screen,tui=self._ui()
        commands=[]
        with patch.object(tui.subprocess, "run",
                          side_effect=lambda args,check=False:
                              commands.append(args) or types.SimpleNamespace(returncode=0)), \
             patch("sys.stdout",io.StringIO()):
            screen.handle("update")
        self.assertEqual(commands[0], [tui.sys.executable,str(tui.ROOT/"nexus_update.py")])
        self.assertTrue(screen.relaunch)


class StartupIntegrationTest(unittest.TestCase):
    def test_autostart_opt_in_and_disable_preserves_other_settings(self):
        startup=module("nexus_autostart_independent", "nexus_autostart.py")
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            profile=root/".bashrc"
            profile.write_text("export TEST_SETTING=kept\n", encoding="utf-8")
            self.assertFalse(startup.enabled(root,"bash"))
            self.assertTrue(startup.set_enabled(True,root,"bash"))
            self.assertTrue(startup.enabled(root,"bash"))
            data=profile.read_text()
            self.assertIn("TEST_SETTING=kept",data)
            self.assertIn("NEXUS_MENU_AUTO_ACTIVE=1 nexus",data)
            self.assertFalse(startup.set_enabled(False,root,"bash"))
            self.assertFalse(startup.enabled(root,"bash"))
            self.assertIn("TEST_SETTING=kept",profile.read_text())

    def test_do_not_replace_symlinked_shell_profile(self):
        startup=module("nexus_autostart_secure", "nexus_autostart.py")
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            target=root/"target"
            target.write_text("ORIGINAL\n")
            (root/".bashrc").symlink_to(target)
            with self.assertRaises(RuntimeError):
                startup.set_enabled(True,root,"bash")
            self.assertEqual(target.read_text(),"ORIGINAL\n")


class UpdateChannelsTest(unittest.TestCase):
    def test_channel_defaults_to_preview_only_when_installed_v2(self):
        with patch.object(up,"installed_version",return_value="v2.0.0-alpha"):
            with patch.object(up.urllib.request, "urlopen") as fetch:
                fake = io.BytesIO(b'{"repository":"KrLrtv/Nexus-AI","tag":"v2.0.0-alpha","channel":"preview"}')
                fetch.return_value.__enter__.return_value = fake
                self.assertEqual(up.target_release(None), "v2.0.0-alpha")
                args=fetch.call_args[0][0]
                self.assertIn("preview.json",args.full_url)
        with patch.object(up,"installed_version",return_value="v0.1.6-alpha"):
            with patch.object(up.urllib.request, "urlopen") as fetch:
                fake = io.BytesIO(b'{"repository":"KrLrtv/Nexus-AI","tag":"v0.1.6-alpha","channel":"alpha"}')
                fetch.return_value.__enter__.return_value = fake
                self.assertEqual(up.target_release(None), "v0.1.6-alpha")
                self.assertIn("channel.json",fetch.call_args[0][0].full_url)

    def test_reject_preview_manifest_pointing_to_stable(self):
        with patch.object(up.urllib.request, "urlopen") as fetch:
            fake=io.BytesIO(b'{"repository":"KrLrtv/Nexus-AI","tag":"v0.1.6-alpha","channel":"preview"}')
            fetch.return_value.__enter__.return_value = fake
            with self.assertRaises(ValueError):
                up.target_release(None,"preview")


if __name__ == "__main__":
    unittest.main()
