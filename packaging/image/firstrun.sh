#!/bin/sh
# Runs once, very early, through systemd.run= from cmdline.txt. There is no
# network at this point: it only prepares the system and schedules the real
# installation for the next boot.
set -e

BOOT=/boot/firmware
[ -d "$BOOT/onionpi" ] || BOOT=/boot
STAGE="$BOOT/onionpi"
SETUP=/var/lib/onionpi-setup

exec >>/var/log/onionpi-firstrun.log 2>&1
set -x
date

if [ -s "$STAGE/hostname" ]; then
  NEW_HOSTNAME="$(head -n 1 "$STAGE/hostname")"
  CURRENT="$(cat /etc/hostname 2>/dev/null || echo raspberrypi)"
  printf '%s\n' "$NEW_HOSTNAME" >/etc/hostname
  sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
  [ "$CURRENT" = "$NEW_HOSTNAME" ] || hostname "$NEW_HOSTNAME" || true
fi

# Create the login account the same way Raspberry Pi Imager does.
if [ -s "$STAGE/userconf.txt" ]; then
  LOGIN="$(cut -d: -f1 <"$STAGE/userconf.txt")"
  LOGIN_HASH="$(cut -d: -f2- <"$STAGE/userconf.txt")"
  if [ -x /usr/lib/userconf-pi/userconf ]; then
    /usr/lib/userconf-pi/userconf "$LOGIN" "$LOGIN_HASH"
  else
    EXISTING="$(getent passwd 1000 | cut -d: -f1)"
    if [ -n "$EXISTING" ]; then
      [ "$EXISTING" = "$LOGIN" ] || usermod -l "$LOGIN" "$EXISTING"
      printf '%s:%s\n' "$LOGIN" "$LOGIN_HASH" | chpasswd -e
    fi
  fi
fi

if [ -s "$STAGE/authorized_keys" ]; then
  LOGIN="${LOGIN:-$(getent passwd 1000 | cut -d: -f1)}"
  HOME_DIR="$(getent passwd "$LOGIN" | cut -d: -f6)"
  if [ -n "$HOME_DIR" ]; then
    install -d -m 0700 -o "$LOGIN" -g "$LOGIN" "$HOME_DIR/.ssh"
    install -m 0600 -o "$LOGIN" -g "$LOGIN" "$STAGE/authorized_keys" "$HOME_DIR/.ssh/authorized_keys"
  fi
fi

if [ -s "$STAGE/country" ]; then
  COUNTRY="$(head -n 1 "$STAGE/country")"
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_wifi_country "$COUNTRY" || true
  fi
fi

systemctl enable ssh >/dev/null 2>&1 || true
systemctl enable systemd-timesyncd >/dev/null 2>&1 || true

# Move the payload off the FAT partition: it is world readable there.
install -d -m 0700 "$SETUP"
cp "$STAGE/payload.tar.gz" "$SETUP/payload.tar.gz"
cp "$STAGE/install.env" "$SETUP/install.env"
chmod 0600 "$SETUP/install.env" "$SETUP/payload.tar.gz"
install -m 0755 "$STAGE/onionpi-firstboot.sh" /usr/local/sbin/onionpi-firstboot
install -m 0644 "$STAGE/onionpi-firstboot.service" /etc/systemd/system/onionpi-firstboot.service
systemctl enable onionpi-firstboot.service

# The FAT partition keeps no secret past this point.
rm -f "$STAGE/install.env" "$STAGE/payload.tar.gz" "$STAGE/userconf.txt"

# Return to a normal boot sequence.
sed -i -e 's| systemd\.run=[^ ]*||g' \
       -e 's| systemd\.run_success_action=[^ ]*||g' \
       -e 's| systemd\.run_failure_action=[^ ]*||g' "$BOOT/cmdline.txt"
rm -f "$BOOT/firstrun.sh"
sync
exit 0
