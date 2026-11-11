#!/bin/sh
# Install the udev rule so sm809 works without root. Run with sudo.
set -e
install -m 0644 "$(dirname "$0")/sm809/70-sm809.rules" /etc/udev/rules.d/70-sm809.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw
echo "Installed. If access is still denied, unplug and replug the mouse or receiver."
