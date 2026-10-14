#!/usr/bin/env bash
# One-off: put Swift on every user's PATH and verify the toolchain.
set -e
printf 'export PATH=/opt/swift/usr/bin:$PATH\n' > /etc/profile.d/swift.sh
chmod +r /etc/profile.d/swift.sh
echo "root sees: $(swift --version 2>&1 | head -1)"
UID1000=$(getent passwd 1000 | cut -d: -f1)
echo "uid1000 is: ${UID1000:-none}"
