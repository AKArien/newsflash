"""newsflash.config - configuration loading and inotify-based hot-reload."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import any, callable

try:
    import tomllib  # python 3.11+
except importerror:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except importerror:
        sys.exit(
            "error: tomllib (python >= 3.11) or the 'tomli' package is required."
        )

try:
    import inotify_simple as _in

    _have_inotify = true
except importerror:
    _have_inotify = false

logger = logging.getlogger(__name__)

defaults: dict[str, any] = {
    "duration": 1.0,       # total animation time in seconds
    "cycles": 2,           # number of up-down flash cycles
    "devices": ["*keyboard*", "*kbd*"],  # led device name patterns
}

config_filename = "newsflash.toml"

def path() -> str:
    """return the absolute path to the user's configuration file."""
    config_home = os.environ.get("xdg_config_home") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(config_home, config_filename)


def load(path: str) -> dict[str, any]:
    """return configuration from *path* merged over defaults.

    missing or unreadable files are silently treated as empty; parse errors
    are logged and also result in the defaults being used.
    """
    cfg = dict(defaults)
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                loaded = tomllib.load(fh)
            cfg.update(loaded)
            logger.info("loaded configuration from %s", path)
        except exception as exc:
            logger.error("failed to load config %s: %s", path, exc)
    return cfg

def _watch_config(on_change: callable[[], none]) -> none:
    cfg_dir = os.path.dirname(config_path())
    if not os.path.isdir(cfg_dir):
        return
    inotify = _in.inotify()
    mask = (
        _in.flags.close_write
        | _in.flags.moved_to
        | _in.flags.create
    )
    inotify.add_watch(cfg_dir, mask)
    try:
        while true:
            for event in inotify.read():
                if event.name == config_filename:
                    logger.info("config file changed, reloading...")
                    on_change()
    finally:
        inotify.close()

def start_watcher(on_change: callable[[], none]) -> none:
    """start a daemon thread that calls *on_change* when the config file changes.

    does nothing if inotify-simple is not installed.
    """
    if not _have_inotify:
        logger.warning(
            "inotify-simple not found; config hot-reload disabled. "
            "install 'inotify-simple' to enable it."
        )
        return
    thread = threading.thread(
        target=_watch_config, args=(on_change,), daemon=true, name="config-watcher"
    )
    thread.start()
    logger.info("config hot-reload enabled.")
