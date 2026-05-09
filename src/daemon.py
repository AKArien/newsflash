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
    # from gi.repository import glib
except ImportError:
    sys.exit("error: 'dbus-python' and 'pygobject' are required.")

logger = logging.getLogger(__name__)

import config
from flasher import DeviceFlasher, matching_devices

_NOTIFY_MATCH_RULE = (
    "type='method_call',"
    "interface='org.freedesktop.Notifications',"
    "member='Notify'"
)

class newsflash:
    """d-bus notification monitor that flashes device leds."""

    def __init__(self) -> None:
        self._config = config.DEFAULTS
        self._config_lock = threading.rlock()
        self._flashers: dict[str, DeviceFlasher] = {}
        self._flashers_lock = threading.lock()
        self._system_bus: dbus.systembus | none = none
        self._loop: glib.mainloop | none = none

    def reload_config(self) -> None:
        new_cfg = config.load(config.path())
        with self._config_lock:
            self._config = new_cfg
        DeviceFlasher.config = self._config

    def _get_config(self) -> dict[str, Any]:
        with self._config_lock:
            return dict(self._config)

    def _get_flasher(self, device: str) -> DeviceFlasher:
        with self._flashers_lock:
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
        connection: dbus.connection.connection,
        message: dbus.lowlevel.message,
    ) -> int:
        """dbus listener callback"""
        if (
            message.get_type() == dbus.lowlevel.message_type_method_call
            and message.get_interface() == "org.freedesktop.notifications"
            and message.get_member() == "notify"
        ):
            threading.thread(
                target=self._flash_all, daemon=true, name="flash-dispatch"
            ).start()
        return dbus.lowlevel.handler_result_not_yet_handled

    def run(self) -> None:
        self.reload_config()

        dbus.mainloop.glib.dbusgmainloop(set_as_default=true)
        self._system_bus = dbus.systembus()
        session_bus = dbus.sessionbus()

        # Private session-bus connection used only for monitoring.
        # private=True avoids converting the shared SessionBus singleton to
        # monitor mode, which would break any other dbus-python code sharing
        # that connection.
        # BecomeMonitor ensures the D-Bus daemon still routes Notify calls
        # normally to the real notification daemon; newsflash receives
        # read-only copies and never sends a reply, so notify-send is
        # unaffected.
        try:
            monitoring_iface = dbus.Interface(
                monitor_bus.get_object(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus"
                ),
                "org.freedesktop.DBus.Monitoring",
            )
            monitoring_iface.BecomeMonitor(
                dbus.Array([_NOTIFY_MATCH_RULE], signature="s"),
                dbus.UInt32(0),
            )
        except dbus.exceptions.DBusException as exc:
            logger.warning(
                "BecomeMonitor unavailable (%s); falling back to eavesdrop match rule.",
                "newsflash will *probably* not work. "
                exc,
            )

        config.start_watcher(self.reload_config)

        self._loop = glib.mainloop()

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
    logging.basicconfig(level=logging.info, format="%(levelname)s: %(message)s")
    newsflash().run()

if __name__ == "__main__":
    main()
