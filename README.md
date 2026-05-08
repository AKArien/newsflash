# newsflash

A lightweight Python daemon that flashes keyboard backlight LEDs whenever a
desktop notification arrives on the D-Bus session bus.

## How it works

1. **Notification detection** — newsflash eavesdrops on the session D-Bus for
   `org.freedesktop.Notifications.Notify` method calls (the standard mechanism
   used by every Linux notification daemon).

2. **LED animation** — On each notification the brightness of all matched LED
   devices is smoothly animated: *current → max → 0*, repeated for the
   configured number of cycles, then restored to the original value.

3. **Brightness writes** — The daemon first attempts to write brightness
   directly to `/sys/class/leds/<device>/brightness`.  If it lacks permission
   it falls back to the `SetBrightness` method of `systemd-logind`
   (`org.freedesktop.login1.Manager`).

4. **Hot-reload** — Configuration is reloaded automatically whenever the
   config file changes, using Linux inotify (no restart required).

## Installation

```bash
pip install .
```

### Runtime dependencies

| Package | Purpose |
|---------|---------|
| `dbus-python` | D-Bus bindings |
| `PyGObject` | GLib main loop |
| `inotify-simple` | Config hot-reload (optional but recommended) |
| `tomli` | TOML parsing on Python < 3.11 (built-in on 3.11+) |

## Running

```bash
newsflash
# or
python newsflash.py
```

Start it as a systemd user service for automatic startup:

```ini
# ~/.config/systemd/user/newsflash.service
[Unit]
Description=newsflash LED notification daemon
After=graphical-session.target

[Service]
ExecStart=%h/.local/bin/newsflash
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now newsflash
```

## Configuration

Copy `newsflash.toml.example` to `~/.config/newsflash.toml` and adjust:

```toml
# Total duration of one flash sequence, in seconds (default: 1.0)
duration = 1.0

# Number of up-down brightness cycles per notification (default: 2)
cycles = 2

# LED device name patterns — matched against /sys/class/leds/ entries
# (supports * wildcards, default: keyboard and kbd devices)
devices = ["*keyboard*", "*kbd*"]
```

The file is watched with inotify; changes take effect immediately.

## Permissions

To allow direct sysfs writes you can add a udev rule:

```
# /etc/udev/rules.d/90-leds.rules
ACTION=="add", SUBSYSTEM=="leds", RUN+="/bin/chgrp video /sys/class/leds/%k/brightness"
ACTION=="add", SUBSYSTEM=="leds", RUN+="/bin/chmod g+w  /sys/class/leds/%k/brightness"
```

Then add your user to the `video` group.  Without this newsflash falls back to
`systemd-logind`, which works without any extra setup on most distributions.
