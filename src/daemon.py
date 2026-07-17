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
import threading
from typing import Any

import dbus
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

logger = logging.getLogger(__name__)

import src.config as config
from src.flasher import DeviceFlasher, matching_devices

class newsflash:
    """d-bus notification monitor that flashes device leds."""

    def __init__(self) -> None:
        self._config = config.DEFAULT
        self._flashers: list[DeviceFlasher] = []
        self._system_bus: dbus.SystemBus | None = None
        self._loop: GLib.MainLoop | None = None

    def reload_config(self) -> None:
        self._config = config.load(config.path())
        DeviceFlasher.global_config = self._config
        patterns = self._config.keys()
        self._flashers = matching_devices(patterns)
        if not self._flashers:
            logger.debug("no led devices matched patterns: %s", patterns)
            return

    def _flash_all(self) -> None:
        for device in self._flashers:
            device.flash()

    def _on_message(
        self,
        connection: dbus.connection.Connection,
        message: dbus.lowlevel.Message,
    ) -> None:
        """dbus listener callback"""
        try:
            if (
                message.get_type() == dbus.lowlevel.MESSAGE_TYPE_METHOD_CALL
                and message.get_interface() == "org.freedesktop.Notifications"
                and message.get_member() == "Notify"
            ):
                self._flash_all()
        except Exception as exc:
            logger.error("error in _on_message: %s", exc, exc_info=True)

    def run(self) -> None:
        self.reload_config()

        DBusGMainLoop(set_as_default=True)
        self._system_bus = dbus.SystemBus()
        DeviceFlasher.system_bus = self._system_bus
        session_bus = dbus.SessionBus(private=True)
        session_bus.add_message_filter(self._on_message)

        obj_dbus = session_bus.get_object('org.freedesktop.DBus',
                                '/org/freedesktop/DBus')
        obj_dbus.BecomeMonitor(["interface='org.freedesktop.Notifications'"],
                            dbus.UInt32(0),
                            interface='org.freedesktop.Notifications')

        session_bus.add_message_filter(self._on_message)

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
