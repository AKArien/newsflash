"""newsflash.flasher - LED device discovery and brightness animation."""

from __future__ import annotations

import fnmatch
import logging
import os
import threading
import time
import math

import dbus
import dbus.exceptions

logger = logging.getLogger(__name__)

LED_CLASS_PATH = "/sys/class/leds"

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

class DeviceFlasher:
    """Manages the flash animation for a single LED device.

    At most one animation runs at a time per device; if a new flash is
    requested while one is already running it is silently ignored.
    """

    config: dict | None = None
    system_bus: dbus.SystemBus | None = None

    def __init__(self, device: str) -> None:
        self.device = device
        self._lock = threading.Lock()
        self.initial = self._read_brightness()
        self.max_brightness = self._read_max_brightness()
        if os.access(os.path.join(LED_CLASS_PATH, device, "brightness"), os.W_OK):
            self._write = self._write_brightness_direct
        else:
            self._write = self._write_brightness_logind

    def _read_int(self, path: str) -> int:
        try:
            with open(path) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return 0

    def _read_brightness(self) -> int:
        return self._read_int(
            os.path.join(LED_CLASS_PATH, self.device, "brightness")
        )

    def _read_max_brightness(self) -> int:
        return self._read_int(
            os.path.join(LED_CLASS_PATH, self.device, "max_brightness"),
        )

    def _write_brightness_direct(self, value: int) -> None:
        path = os.path.join(LED_CLASS_PATH, self.device, "brightness")
        with open(path, "w") as fh:
            fh.write(str(value))

    def _write_brightness_logind(self, value: int) -> None:
        """Set brightness via systemd-logind's SetBrightness D-Bus method."""
        try:
            obj = DeviceFlasher.system_bus.get_object(
                "org.freedesktop.login1", "/org/freedesktop/login1"
            )
            iface = dbus.Interface(obj, "org.freedesktop.login1.Manager")
            iface.SetBrightness(
                "leds", self.device, dbus.UInt32(value),
                reply_handler=lambda: None,
                error_handler=lambda exc: logger.warning(
                    "SetBrightness via logind failed for %s: %s", self.device, exc
                ),
            )
        except dbus.exceptions.DBusException as exc:
            logger.warning("SetBrightness call error for %s: %s", self.device, exc)

    def _animation_keyframes(self, initial: int) -> list[int]:
        keyframes = []
        cfg = DeviceFlasher.config

        steps = round(cfg["duration"] * cfg["animation_hz"])
        phase = 0.5 + initial / (2 * self.max_brightness)

        for i in range(steps):
            keyframes.append(round(
                (math.cos((i / steps * cfg["cycles"] + phase) * math.tau) + 1)
                / 2 * self.max_brightness
            ))

        return keyframes

    def flash(self) -> None:
        """Start a flash animation in a new thread (non-blocking).

        Does nothing if an animation is already in progress for this device.
        """
        if self._lock.locked():
            return
        threading.Thread(
            target=self._run_animation,
            daemon=True,
            name=f"flash-{self.device}",
        ).start()

    def _run_animation(self) -> None:
        with self._lock:
            try:
                initial = self._read_brightness()

                keyframes = self._animation_keyframes(initial)

                wait = (DeviceFlasher.config["duration"] / len(keyframes))

                for a in keyframes:
                    self._write(a)
                    time.sleep(wait)

                self._write(initial) # in case the last frame isn’t exact

            except Exception as exc:
                logger.error("Animation error for %s: %s", self.device, exc)
