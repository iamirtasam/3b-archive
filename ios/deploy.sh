#!/usr/bin/env bash
# Build + install 3B Archive onto the iPhone from WSL.
#
# Uses the Windows Apple socket bridge instead of usbipd USB passthrough —
# usbipd corrupts large USB transfers on WSL (xtool issue #19), so Windows'
# Apple Mobile Device Service owns the phone and we forward its socket:
#   netsh interface portproxy set v4tov4 listenaddress=<wsl-gw> listenport=27015 connectaddress=127.0.0.1 connectport=27015
#   (firewall rule "usbmuxd WSL forward" on 27015, one-time)
#
# Prereqs (one-time): usbipd-win installed; Apple Devices app or iTunes
# installed and the phone connected+trusted so Apple's service runs.
# Toolchain setup lives in setup-wsl-toolchain.sh; bridge checks in
# check-device.sh.
#
# Usage:  bash ios/deploy.sh            (from the repo root, in WSL)
set -e
GW=$(ip route list default | awk '{print $3}')
export USBMUXD_SOCKET_ADDRESS="${GW}:27015"
cd "$(dirname "$0")"
exec xtool dev --udid 00008140-00143D522600401C
