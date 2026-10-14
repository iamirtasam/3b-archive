#!/usr/bin/env bash
# Route libimobiledevice/xtool through the Windows-side Apple socket
# (usbipd corrupts large USB transfers on WSL — see xtool issue #19).
GW=$(ip route list default | awk '{print $3}')
export USBMUXD_SOCKET_ADDRESS="${GW}:27015"
echo "socket: ${USBMUXD_SOCKET_ADDRESS}"
ideviceinfo -k DeviceName 2>&1 | head -1
