#!/usr/bin/env python3
"""newsflash — Flash keyboard LEDs on desktop notifications.

Listens on the D-Bus session bus for desktop notifications
(org.freedesktop.Notifications.Notify method calls) and responds by
smoothly animating the brightness of matched LED devices.

Configuration is read from $XDG_CONFIG_HOME/newsflash.toml (defaulting to
~/.config/newsflash.toml) and hot-reloaded via inotify whenever the file
changes.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import signal
import sys
import threading
import time
from typing import Any

# ── TOML ──────────────────────────────────────────────────────────────────────
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        sys.exit(
            "error: tomllib (Python ≥ 3.11) or the 'tomli' package is required."
        )

# ── D-Bus / GLib ──────────────────────────────────────────────────────────────
try:
    import dbus
    import dbus.lowlevel
    import dbus.mainloop.glib
    from gi.repository import GLib
except ImportError:
    sys.exit("error: 'dbus-python' and 'PyGObject' are required.")

# ── inotify (optional; hot-reload disabled when absent) ───────────────────────
try:
    import inotify_simple as _inotify_mod

    _INOTIFY_LIB = "inotify_simple"
except ImportError:
    try:
        import inotify.adapters as _inotify_adapters  # type: ignore[assignment]

        _INOTIFY_LIB = "inotify"
    except ImportError:
        _INOTIFY_LIB: str | None = None

# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULTS: dict[str, Any] = {
    "duration": 1.0,       # total animation time in seconds
    "cycles": 2,           # number of up-down flash cycles
    "devices": ["*keyboard*", "*kbd*"],  # LED device name patterns
}

LED_CLASS_PATH = "/sys/class/leds"
CONFIG_FILENAME = "newsflash.toml"
ANIMATION_FPS = 60  # brightness updates per second during animation


# ── Configuration ─────────────────────────────────────────────────────────────

def config_path() -> str:
    """Return the absolute path to the user's configuration file."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(config_home, CONFIG_FILENAME)


def load_config(path: str) -> dict[str, Any]:
    """Return configuration from *path* merged over defaults.

    Missing or unreadable files are silently treated as empty; parse errors
    are logged and also result in the defaults being used.
    """
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                loaded = tomllib.load(fh)
            cfg.update(loaded)
            logger.info("Loaded configuration from %s", path)
        except Exception as exc:
            logger.error("Failed to load config %s: %s", path, exc)
    return cfg


# ── LED device helpers ────────────────────────────────────────────────────────

def matching_devices(patterns: list[str]) -> list[str]:
    """Return LED device names under LED_CLASS_PATH that match any pattern.

    Patterns use fnmatch wildcards (e.g. ``*keyboard*``).
    """
    if not os.path.isdir(LED_CLASS_PATH):
        return []
    seen: set[str] = set()
    matched: list[str] = []
    for device in os.listdir(LED_CLASS_PATH):
        if device in seen:
            continue
        for pattern in patterns:
            if fnmatch.fnmatch(device, pattern):
                matched.append(device)
                seen.add(device)
                break
    return matched


def _read_int(path: str, default: int = 0) -> int:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return default


def read_brightness(device: str) -> int:
    return _read_int(os.path.join(LED_CLASS_PATH, device, "brightness"))


def read_max_brightness(device: str) -> int:
    return _read_int(os.path.join(LED_CLASS_PATH, device, "max_brightness"), default=255)


def can_write_direct(device: str) -> bool:
    """Return True when the process can write directly to the sysfs file."""
    path = os.path.join(LED_CLASS_PATH, device, "brightness")
    return os.access(path, os.W_OK)


def write_brightness_direct(device: str, value: int) -> None:
    path = os.path.join(LED_CLASS_PATH, device, "brightness")
    with open(path, "w") as fh:
        fh.write(str(value))


def write_brightness_logind(system_bus: dbus.SystemBus, device: str, value: int) -> None:
    """Set brightness via systemd-logind's SetBrightness D-Bus method."""
    try:
        obj = system_bus.get_object(
            "org.freedesktop.login1", "/org/freedesktop/login1"
        )
        iface = dbus.Interface(obj, "org.freedesktop.login1.Manager")
        iface.SetBrightness("leds", device, dbus.UInt32(value))
    except dbus.exceptions.DBusException as exc:
        logger.debug("SetBrightness via logind failed for %s: %s", device, exc)


def animation_keyframes(initial: int, max_brightness: int, cycles: int) -> list[int]:
    """Return the brightness keyframe sequence for an animation.

    The animation smoothly moves from *initial* → *max_brightness* → 0,
    repeating that up-down cycle *cycles* times, then returns to *initial*.

    Example (cycles=2):  [initial, max, 0, max, 0, initial]
    """
    return [initial] + [max_brightness, 0] * cycles + [initial]


# ── Per-device flash animator ─────────────────────────────────────────────────

class DeviceFlasher:
    """Manages the flash animation for a single LED device.

    At most one animation runs at a time per device; if a new flash is
    requested while one is already running it is silently ignored.
    """

    def __init__(self, device: str, system_bus: dbus.SystemBus) -> None:
        self.device = device
        self._system_bus = system_bus
        self._direct = can_write_direct(device)
        self._lock = threading.Lock()

    def _write(self, value: int) -> None:
        value = max(0, value)
        if self._direct:
            try:
                write_brightness_direct(self.device, value)
                return
            except OSError:
                self._direct = False  # fall through to logind
        write_brightness_logind(self._system_bus, self.device, value)

    def flash(self, duration: float, cycles: int) -> None:
        """Start a flash animation in a daemon thread (non-blocking).

        Does nothing if an animation is already in progress for this device.
        """
        if not self._lock.acquire(blocking=False):
            return
        threading.Thread(
            target=self._run_animation,
            args=(duration, cycles),
            daemon=True,
            name=f"flash-{self.device}",
        ).start()

    def _run_animation(self, duration: float, cycles: int) -> None:
        try:
            initial = read_brightness(self.device)
            max_val = read_max_brightness(self.device)
            if max_val == 0:
                return

            keyframes = animation_keyframes(initial, max_val, cycles)
            n_segments = len(keyframes) - 1
            total_steps = max(1, int(duration * ANIMATION_FPS))
            step_dt = duration / total_steps

            for step in range(total_steps + 1):
                t = step / total_steps          # 0.0 … 1.0
                seg_f = t * n_segments
                seg = min(int(seg_f), n_segments - 1)
                frac = seg_f - seg
                brightness = int(
                    keyframes[seg]
                    + (keyframes[seg + 1] - keyframes[seg]) * frac
                )
                self._write(brightness)
                if step < total_steps:
                    time.sleep(step_dt)

            self._write(initial)  # ensure exact restoration
        except Exception as exc:
            logger.error("Animation error for %s: %s", self.device, exc)
        finally:
            self._lock.release()


# ── Main daemon ───────────────────────────────────────────────────────────────

class NewsFlash:
    """D-Bus notification monitor that flashes keyboard LEDs."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = dict(DEFAULTS)
        self._config_lock = threading.RLock()
        self._flashers: dict[str, DeviceFlasher] = {}
        self._flashers_lock = threading.Lock()
        self._system_bus: dbus.SystemBus | None = None
        self._loop: GLib.MainLoop | None = None

    # ── Config management ──────────────────────────────────────────────────

    def reload_config(self) -> None:
        """Load (or reload) configuration from disk."""
        new_cfg = load_config(config_path())
        with self._config_lock:
            self._config = new_cfg

    def _get_config(self) -> dict[str, Any]:
        with self._config_lock:
            return dict(self._config)

    # ── inotify config watcher ─────────────────────────────────────────────

    def _watch_inotify_simple(self) -> None:
        import inotify_simple  # noqa: F401

        cfg_dir = os.path.dirname(config_path())
        if not os.path.isdir(cfg_dir):
            return
        inotify = _inotify_mod.INotify()
        mask = (
            _inotify_mod.flags.CLOSE_WRITE
            | _inotify_mod.flags.MOVED_TO
            | _inotify_mod.flags.CREATE
        )
        inotify.add_watch(cfg_dir, mask)
        try:
            while True:
                for event in inotify.read():
                    if event.name == CONFIG_FILENAME:
                        logger.info("Config file changed, reloading…")
                        self.reload_config()
        finally:
            inotify.close()

    def _watch_inotify(self) -> None:
        cfg_dir = os.path.dirname(config_path())
        if not os.path.isdir(cfg_dir):
            return
        ino = _inotify_adapters.Inotify()
        ino.add_watch(cfg_dir)
        for event in ino.event_gen(yield_nones=False):
            (_, type_names, _path, filename) = event
            if filename == CONFIG_FILENAME and any(
                t in type_names
                for t in ("IN_CLOSE_WRITE", "IN_MOVED_TO", "IN_CREATE")
            ):
                logger.info("Config file changed, reloading…")
                self.reload_config()

    def _start_config_watcher(self) -> None:
        if _INOTIFY_LIB == "inotify_simple":
            target = self._watch_inotify_simple
        elif _INOTIFY_LIB == "inotify":
            target = self._watch_inotify
        else:
            logger.warning(
                "No inotify library found; config hot-reload disabled. "
                "Install 'inotify-simple' to enable it."
            )
            return
        thread = threading.Thread(
            target=target, daemon=True, name="config-watcher"
        )
        thread.start()
        logger.info("Config hot-reload enabled via %s.", _INOTIFY_LIB)

    # ── LED flash dispatch ─────────────────────────────────────────────────

    def _get_flasher(self, device: str) -> DeviceFlasher:
        with self._flashers_lock:
            if device not in self._flashers:
                self._flashers[device] = DeviceFlasher(device, self._system_bus)
            return self._flashers[device]

    def _flash_all(self) -> None:
        cfg = self._get_config()
        patterns: list[str] = cfg.get("devices", DEFAULTS["devices"])
        duration: float = float(cfg.get("duration", DEFAULTS["duration"]))
        cycles: int = int(cfg.get("cycles", DEFAULTS["cycles"]))

        devices = matching_devices(patterns)
        if not devices:
            logger.debug("No LED devices matched patterns: %s", patterns)
            return

        for device in devices:
            self._get_flasher(device).flash(duration, cycles)

    # ── D-Bus message filter ───────────────────────────────────────────────

    def _on_message(
        self,
        connection: dbus.connection.Connection,
        message: dbus.lowlevel.Message,
    ) -> int:
        if (
            message.get_type() == dbus.lowlevel.MESSAGE_TYPE_METHOD_CALL
            and message.get_interface() == "org.freedesktop.Notifications"
            and message.get_member() == "Notify"
        ):
            logger.debug("Notification detected — flashing LEDs.")
            threading.Thread(
                target=self._flash_all, daemon=True, name="flash-dispatch"
            ).start()
        return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED

    # ── Entry point ────────────────────────────────────────────────────────

    def run(self) -> None:
        self.reload_config()

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._system_bus = dbus.SystemBus()
        session_bus = dbus.SessionBus()

        # Intercept Notify method calls on the session bus.
        # eavesdrop=true is required to see method calls not addressed to us.
        session_bus.add_message_filter(self._on_message)
        try:
            session_bus.add_match_string(
                "eavesdrop=true,"
                "type='method_call',"
                "interface='org.freedesktop.Notifications',"
                "member='Notify'"
            )
        except dbus.exceptions.DBusException as exc:
            logger.error(
                "Could not install eavesdrop match rule (%s). "
                "Notifications may not be detected. "
                "Ensure your D-Bus policy allows eavesdropping.",
                exc,
            )

        self._start_config_watcher()

        self._loop = GLib.MainLoop()

        def _shutdown(signum: int, _frame: object) -> None:
            logger.info("Received signal %d, shutting down.", signum)
            if self._loop:
                self._loop.quit()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        logger.info("newsflash daemon started.")
        self._loop.run()
        logger.info("newsflash daemon stopped.")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    NewsFlash().run()


if __name__ == "__main__":
    main()
