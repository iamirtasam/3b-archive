#!/usr/bin/env bash
GW=$(ip route list default | awk '{print $3}')
export USBMUXD_SOCKET_ADDRESS="${GW}:27015"
echo "== xtool devices:"
timeout 30 xtool devices 2>&1 | head -8
echo "== idevice lockdown probe:"
timeout 15 ideviceinfo -k ProductType 2>&1 | head -1
