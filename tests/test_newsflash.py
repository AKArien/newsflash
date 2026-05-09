"""Unit tests for newsflash core logic.

These tests cover pure-Python functions that do not require D-Bus, a running
display server, or physical LED hardware.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

# Make sure the modules under test are importable without D-Bus being available
# at import time.  We monkey-patch the heavy optional imports before loading.
import types

# Stub out dbus so the modules can be imported in a headless CI environment.
_dbus_stub = types.ModuleType("dbus")
_dbus_stub.SessionBus = object
_dbus_stub.SystemBus = object
_dbus_stub.UInt32 = int

_dbus_lowlevel = types.ModuleType("dbus.lowlevel")
_dbus_lowlevel.MESSAGE_TYPE_METHOD_CALL = 1
_dbus_lowlevel.HANDLER_RESULT_NOT_YET_HANDLED = 0
_dbus_stub.lowlevel = _dbus_lowlevel

_dbus_ml = types.ModuleType("dbus.mainloop.glib")
_dbus_ml.DBusGMainLoop = lambda **kw: None
_dbus_stub.mainloop = types.ModuleType("dbus.mainloop")
_dbus_stub.mainloop.glib = _dbus_ml

_dbus_exc = types.ModuleType("dbus.exceptions")


class _DBusException(Exception):
    pass


_dbus_exc.DBusException = _DBusException
_dbus_stub.exceptions = _dbus_exc

_dbus_conn = types.ModuleType("dbus.connection")
_dbus_conn.Connection = object
_dbus_stub.connection = _dbus_conn

sys.modules.setdefault("dbus", _dbus_stub)
sys.modules.setdefault("dbus.lowlevel", _dbus_lowlevel)
sys.modules.setdefault("dbus.mainloop", _dbus_stub.mainloop)
sys.modules.setdefault("dbus.mainloop.glib", _dbus_ml)
sys.modules.setdefault("dbus.exceptions", _dbus_exc)
sys.modules.setdefault("dbus.connection", _dbus_conn)

_gi_stub = types.ModuleType("gi")
_gi_repo = types.ModuleType("gi.repository")


class _FakeGLib:
    class MainLoop:
        def run(self) -> None:
            pass

        def quit(self) -> None:
            pass


_gi_repo.GLib = _FakeGLib
_gi_stub.repository = _gi_repo
sys.modules.setdefault("gi", _gi_stub)
sys.modules.setdefault("gi.repository", _gi_repo)

# Now import the modules under test.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as cfg_mod  # noqa: E402
import flasher as fl_mod  # noqa: E402
import newsflash as nf    # noqa: E402


class TestConfigPath(unittest.TestCase):
    def test_uses_xdg_config_home(self):
        os.environ["XDG_CONFIG_HOME"] = "/custom/config"
        path = cfg_mod.config_path()
        self.assertEqual(path, "/custom/config/newsflash.toml")

    def test_falls_back_to_home_config(self):
        os.environ.pop("XDG_CONFIG_HOME", None)
        path = cfg_mod.config_path()
        self.assertIn(".config", path)
        self.assertTrue(path.endswith("newsflash.toml"))


class TestLoadConfig(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        loaded = cfg_mod.load_config("/nonexistent/path/newsflash.toml")
        self.assertEqual(loaded["duration"], cfg_mod.DEFAULTS["duration"])
        self.assertEqual(loaded["cycles"], cfg_mod.DEFAULTS["cycles"])
        self.assertEqual(loaded["devices"], cfg_mod.DEFAULTS["devices"])

    def test_values_loaded_from_file(self):
        content = textwrap.dedent("""\
            duration = 2.5
            cycles = 4
            devices = ["*rgb*"]
        """)
        with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as fh:
            fh.write(content.encode())
            path = fh.name
        try:
            loaded = cfg_mod.load_config(path)
            self.assertAlmostEqual(loaded["duration"], 2.5)
            self.assertEqual(loaded["cycles"], 4)
            self.assertEqual(loaded["devices"], ["*rgb*"])
        finally:
            os.unlink(path)

    def test_partial_overrides_keep_defaults(self):
        content = "cycles = 3\n"
        with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as fh:
            fh.write(content.encode())
            path = fh.name
        try:
            loaded = cfg_mod.load_config(path)
            self.assertEqual(loaded["cycles"], 3)
            # duration and devices should still be the defaults
            self.assertEqual(loaded["duration"], cfg_mod.DEFAULTS["duration"])
            self.assertEqual(loaded["devices"], cfg_mod.DEFAULTS["devices"])
        finally:
            os.unlink(path)

    def test_bad_toml_returns_defaults(self):
        with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as fh:
            fh.write(b"this is not valid = = toml !!!")
            path = fh.name
        try:
            loaded = cfg_mod.load_config(path)
            self.assertEqual(loaded["duration"], cfg_mod.DEFAULTS["duration"])
        finally:
            os.unlink(path)


class TestMatchingDevices(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        # Save and override the module-level constant
        self._orig = fl_mod.LED_CLASS_PATH
        fl_mod.LED_CLASS_PATH = self._tmpdir
        # Create some fake device directories
        for name in [
            "asus::kbd_backlight",
            "tpacpi::kbd_backlight",
            "input4::scrolllock",
            "input4::capslock",
            "rgb_keyboard_0",
        ]:
            os.makedirs(os.path.join(self._tmpdir, name), exist_ok=True)

    def tearDown(self):
        fl_mod.LED_CLASS_PATH = self._orig
        import shutil
        shutil.rmtree(self._tmpdir)

    def test_default_patterns_match_keyboard_devices(self):
        devices = fl_mod.matching_devices(["*keyboard*", "*kbd*"])
        names = set(devices)
        self.assertIn("asus::kbd_backlight", names)
        self.assertIn("tpacpi::kbd_backlight", names)
        self.assertIn("rgb_keyboard_0", names)
        self.assertNotIn("input4::scrolllock", names)
        self.assertNotIn("input4::capslock", names)

    def test_wildcard_star_matches_all(self):
        devices = fl_mod.matching_devices(["*"])
        self.assertEqual(len(devices), 5)

    def test_no_match_returns_empty(self):
        devices = fl_mod.matching_devices(["*nonexistent*"])
        self.assertEqual(devices, [])

    def test_no_duplicates(self):
        # A device matching multiple patterns should appear only once.
        devices = fl_mod.matching_devices(["*kbd*", "*keyboard*"])
        self.assertEqual(len(devices), len(set(devices)))

    def test_missing_led_path_returns_empty(self):
        fl_mod.LED_CLASS_PATH = "/nonexistent/path"
        self.assertEqual(fl_mod.matching_devices(["*"]), [])


class TestAnimationKeyframes(unittest.TestCase):
    def test_cycles_1(self):
        kf = fl_mod.DeviceFlasher._animation_keyframes(50, 255, 1)
        self.assertEqual(kf, [50, 255, 0, 50])

    def test_cycles_2(self):
        kf = fl_mod.DeviceFlasher._animation_keyframes(50, 255, 2)
        self.assertEqual(kf, [50, 255, 0, 255, 0, 50])

    def test_cycles_3(self):
        kf = fl_mod.DeviceFlasher._animation_keyframes(100, 200, 3)
        self.assertEqual(kf, [100, 200, 0, 200, 0, 200, 0, 100])

    def test_initial_zero(self):
        kf = fl_mod.DeviceFlasher._animation_keyframes(0, 255, 2)
        self.assertEqual(kf[0], 0)
        self.assertEqual(kf[-1], 0)
        self.assertEqual(max(kf), 255)

    def test_length(self):
        for cycles in (1, 2, 3, 5):
            kf = fl_mod.DeviceFlasher._animation_keyframes(10, 255, cycles)
            # [initial] + [max, 0] * cycles + [initial]
            self.assertEqual(len(kf), 2 * cycles + 2)


class TestReloadConfig(unittest.TestCase):
    def test_reload_updates_internal_state(self):
        daemon = nf.NewsFlash()
        content = "duration = 3.0\ncycles = 5\n"
        with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as fh:
            fh.write(content.encode())
            path = fh.name
        try:
            os.environ["XDG_CONFIG_HOME"] = os.path.dirname(path)
            # Rename so it matches the expected config filename
            target = os.path.join(os.path.dirname(path), cfg_mod.CONFIG_FILENAME)
            os.rename(path, target)
            daemon.reload_config()
            loaded = daemon._get_config()
            self.assertAlmostEqual(loaded["duration"], 3.0)
            self.assertEqual(loaded["cycles"], 5)
        finally:
            try:
                os.unlink(target)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    unittest.main()

