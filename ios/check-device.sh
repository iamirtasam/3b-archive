#!/usr/bin/env bash
# Sanity-check the Windows↔WSL iPhone bridge. Run from WSL when deploys
# fail with LockdownClient.Error.muxError / "No device found".
GW=$(ip route list default | awk '{print $3}')
export USBMUXD_SOCKET_ADDRESS="${GW}:27015"
echo "bridge: ${GW}:27015"
echo "== devices via bridge:"
idevice_id -l 2>&1
echo "== lockdown probe:"
ideviceinfo -k DeviceName 2>&1 | head -1
echo
echo "If the probe errors: restart 'Apple Mobile Device Service' on Windows"
echo "(admin PowerShell: Restart-Service 'Apple Mobile Device Service' -Force),"
echo "wait 5 s, then run me again. Deploy with: bash ios/deploy.sh"
