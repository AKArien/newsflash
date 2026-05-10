#!/usr/bin/env python3
"""newsflash : d-bus notification listener that flashes device leds.

Monitors the session bus for org.freedesktop.Notifications.Notify calls and
triggers a brightness animation on matched LED devices for each notification.

configuration is read from $XDG_CONFIG_HOME/newsflash.toml (defaulting to
~/.config/newsflash.toml) and hot-reloaded whenever the config file changes.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from typing import Any

try:
    import dbus
    import dbus.connection
    import dbus.exceptions
    import dbus.lowlevel
    import dbus.mainloop.glib
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
except ImportError:
    sys.exit("error: 'dbus-python' and 'pygobject' are required.")

logger = logging.getLogger(__name__)

import src.config as config
from src.flasher import DeviceFlasher, matching_devices

class newsflash:
    """d-bus notification monitor that flashes device leds."""

    def __init__(self) -> None:
        self._config = config.DEFAULTS
        self._config_lock = threading.RLock()
        self._flashers: dict[str, DeviceFlasher] = {}
        self._system_bus: dbus.SystemBus | None = None
        self._loop: GLib.MainLoop | None = None

    def reload_config(self) -> None:
        new_cfg = config.load(config.path())
        with self._config_lock:
            self._config = new_cfg
        DeviceFlasher.config = self._config

    def _get_config(self) -> dict[str, Any]:
        with self._config_lock:
            return dict(self._config)

    def _get_flasher(self, device: str) -> DeviceFlasher:
        if device not in self._flashers:
            self._flashers[device] = DeviceFlasher(device)
        return self._flashers[device]

    def _flash_all(self) -> None:
        cfg = self._get_config()
        patterns: list[str] = cfg.get("devices", config.DEFAULTS["devices"])

        devices = matching_devices(patterns)
        if not devices:
            logger.debug("no led devices matched patterns: %s", patterns)
            return

        for device in devices:
            self._get_flasher(device).flash()

    def _on_message(
        self,
        connection: dbus.connection.Connection,
        message: dbus.lowlevel.Message,
    ) -> int:
        """dbus listener callback"""
        logger.info("message intercepted.")
        if (
            message.get_type() == dbus.lowlevel.MESSAGE_TYPE_METHOD_CALL
            and message.get_interface() == "org.freedesktop.Notifications"
            and message.get_member() == "Notify"
        ):
            self._flash_all()
        return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED

    def run(self) -> None:
        self.reload_config()

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._system_bus = dbus.SystemBus()
        DeviceFlasher.system_bus = self._system_bus
        monitor_bus = dbus.SessionBus(private=True)
        monitor_bus.add_message_filter(self._on_message)

        try:
            monitoring_iface = dbus.Interface(
                monitor_bus.get_object(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus"
                ),
                "org.freedesktop.DBus.Monitoring",
            )
            monitoring_iface.BecomeMonitor(
                dbus.Array([(
                    "type='method_call',"
                    "interface='org.freedesktop.Notifications',"
                    "member='Notify'"
                )], signature="s"),
                dbus.UInt32(0),
            )
        except dbus.exceptions.DBusException as exc:
            logger.warning(
                "BecomeMonitor unavailable (%s) ; ",
                "newsflash will *probably* not work.",
                exc,
            )

        config.start_watcher(self.reload_config)

        self._loop = GLib.MainLoop()

        def _shutdown(signum: int, _frame: object) -> None:
            logger.info("received signal %d, shutting down.", signum)
            if self._loop:
                self._loop.quit()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        logger.info("newsflash daemon started.")
        self._loop.run()
        logger.info("newsflash daemon stopped.")

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    newsflash().run()

if __name__ == "__main__":
    main()
