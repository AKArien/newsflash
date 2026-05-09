#!/usr/bin/env python3
"""newsflash - D-Bus notification listener that flashes keyboard LEDs.

Monitors the session bus for org.freedesktop.Notifications.Notify calls and
triggers a brightness animation on matched LED devices for each notification.

Configuration is read from $XDG_CONFIG_HOME/newsflash.toml (defaulting to
~/.config/newsflash.toml) and hot-reloaded via inotify whenever the file
changes.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from typing import Any

# ── D-Bus / GLib ──────────────────────────────────────────────────────────────
try:
    import dbus
    import dbus.connection
    import dbus.exceptions
    import dbus.lowlevel
    import dbus.mainloop.glib
    from gi.repository import GLib
except ImportError:
    sys.exit("error: 'dbus-python' and 'PyGObject' are required.")

from config import DEFAULTS, config_path, load_config, start_config_watcher
from flasher import DeviceFlasher, matching_devices

# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


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

        # Dedicated session-bus connection used only for monitoring.
        # Using BecomeMonitor ensures the D-Bus daemon still routes Notify
        # calls normally to the real notification daemon; newsflash receives
        # read-only copies and never sends a reply, so notify-send is
        # unaffected.
        monitor_bus = dbus.SessionBus()
        monitor_bus.add_message_filter(self._on_message)

        _NOTIFY_RULE = (
            "type='method_call',"
            "interface='org.freedesktop.Notifications',"
            "member='Notify'"
        )
        try:
            monitoring_iface = dbus.Interface(
                monitor_bus.get_object(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus"
                ),
                "org.freedesktop.DBus.Monitoring",
            )
            monitoring_iface.BecomeMonitor(
                dbus.Array([_NOTIFY_RULE], signature="s"),
                dbus.UInt32(0),
            )
        except dbus.exceptions.DBusException as exc:
            logger.warning(
                "BecomeMonitor unavailable (%s); falling back to eavesdrop match rule.",
                exc,
            )
            try:
                monitor_bus.add_match_string("eavesdrop=true," + _NOTIFY_RULE)
            except dbus.exceptions.DBusException as exc2:
                logger.error(
                    "Could not install eavesdrop match rule (%s). "
                    "Notifications may not be detected.",
                    exc2,
                )

        start_config_watcher(self.reload_config)

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
