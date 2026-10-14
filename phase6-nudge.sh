#!/usr/bin/env bash
# Nudge the Windows Apple service off the device, wait, let it re-acquire.
# Run me from Windows whenever the mux bridge goes stale.
"/c/Program Files/usbipd-win/usbipd.exe" detach --busid 3-2 >/dev/null 2>&1
net.exe stop "Apple Mobile Device Service" >/dev/null 2>&1
sleep 2
net.exe start "Apple Mobile Device Service" >/dev/null 2>&1
sleep 4
GW=$(ip route list default | awk '{print $3}')
export USBMUXD_SOCKET_ADDRESS="${GW}:27015"
echo "after restart:"
idevice_id -l 2>&1 | head -2
