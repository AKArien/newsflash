"""newsflash.config - Configuration loading and inotify-based hot-reload."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Callable

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        sys.exit(
            "error: tomllib (Python >= 3.11) or the 'tomli' package is required."
        )

try:
    import inotify_simple as _in

    _HAVE_INOTIFY = True
except ImportError:
    _HAVE_INOTIFY = False

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "duration": 1.0,       # total animation time in seconds
    "cycles": 2,           # number of up-down flash cycles
    "devices": ["*keyboard*", "*kbd*"],  # LED device name patterns
}

CONFIG_FILENAME = "newsflash.toml"

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

def _watch_config(on_change: Callable[[], None]) -> None:
    cfg_dir = os.path.dirname(config_path())
    if not os.path.isdir(cfg_dir):
        return
    inotify = _in.INotify()
    mask = (
        _in.flags.CLOSE_WRITE
        | _in.flags.MOVED_TO
        | _in.flags.CREATE
    )
    inotify.add_watch(cfg_dir, mask)
    try:
        while True:
            for event in inotify.read():
                if event.name == CONFIG_FILENAME:
                    logger.info("Config file changed, reloading...")
                    on_change()
    finally:
        inotify.close()

def start_config_watcher(on_change: Callable[[], None]) -> None:
    """Start a daemon thread that calls *on_change* when the config file changes.

    Does nothing if inotify-simple is not installed.
    """
    if not _HAVE_INOTIFY:
        logger.warning(
            "inotify-simple not found; config hot-reload disabled. "
            "Install 'inotify-simple' to enable it."
        )
        return
    thread = threading.Thread(
        target=_watch_config, args=(on_change,), daemon=True, name="config-watcher"
    )
    thread.start()
    logger.info("Config hot-reload enabled.")
