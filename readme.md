# newsflash

A tiny tiny daemon to flash your computer’s lights when a you recieve a notification.

## Configuration

You can configure the total duration of flashes, the amount of flashes executed for each notification, the animation smoothness and the devices that will be flashed.

Configured with a file at `$XDG_CONFIG_HOME/newsflash.toml` (falls back to `~/.config/newsflash.toml` if unset), check [the example config file](./newsflash.toml.example) for guidance. Changes to configuration are hot-reloaded as long as your system supports inotify.

## Installation

Install with pipx :
```bash
git clone https://github.com/AKArien/newsflash.git
cd newsflash
pipx install .
```

A service file is provided for systemd (turstile coming soon) :

```bash
cp newsflash.service $XDG_CONFIG_HOME/systemd/user/
systemctl --user start newsflash
systemctl --user enable newsflash
```

## Permissions

If running a non-systemd system and the daemon fails to change the brightness, your user may be lacking permissions.
Add a udev rule like the following (you are probably already in the video group) :

```
# /etc/udev/rules.d/90-leds.rules
ACTION=="add", SUBSYSTEM=="leds", RUN+="/bin/chgrp video /sys/class/leds/%k/brightness"
ACTION=="add", SUBSYSTEM=="leds", RUN+="/bin/chmod g+w  /sys/class/leds/%k/brightness"
```

If you have such problems on a systemd system, open an issue and pray someone has an idea instead (it uses the logind dbus interface for it).
