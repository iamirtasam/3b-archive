#!/usr/bin/env bash
# Phase 6 — WSL-side toolchain install (run as root inside Ubuntu)
# Installs: build deps, usbmuxd (iPhone USB), Swift 6.4, xtool AppImage.
# Does NOT need the Xcode.xip - that comes later via `xtool setup`.
set -euo pipefail
log() { echo "[phase6] $*"; }

. /etc/os-release
log "Detected Ubuntu ${VERSION_ID}"
export DEBIAN_FRONTEND=noninteractive

log "Installing apt dependencies (incl. usbmuxd + libimobiledevice for iPhone USB)..."
apt-get update -qq
BASE="binutils git gnupg2 libc6-dev libcurl4-openssl-dev libedit2 libpython3-dev libsqlite3-0 libxml2-dev libz3-dev pkg-config tzdata unzip zip zlib1g-dev usbmuxd libimobiledevice-utils curl ca-certificates libfuse2t64"
if [ "${VERSION_ID}" = "24.04" ]; then
  apt-get install -y -qq $BASE libgcc-13-dev libstdc++-13-dev >/dev/null
else
  apt-get install -y -qq $BASE libgcc-12-dev libstdc++-12-dev >/dev/null
fi
log "apt dependencies done"

case "${VERSION_ID}" in
  26.04) SWU="ubuntu2604"; SWF="ubuntu26.04" ;;
  24.04) SWU="ubuntu2404"; SWF="ubuntu24.04" ;;
  22.04) SWU="ubuntu2204"; SWF="ubuntu22.04" ;;
  *) log "Unknown Ubuntu ${VERSION_ID} - assuming 26.04"; SWU="ubuntu2604"; SWF="ubuntu26.04" ;;
esac

if ! command -v swift >/dev/null 2>&1 && [ ! -x /opt/swift/usr/bin/swift ]; then
  URL="https://download.swift.org/swift-6.4.1-release/${SWU}/swift-6.4.1-RELEASE/swift-6.4.1-RELEASE-${SWF}.tar.gz"
  log "Downloading Swift 6.4.1 for ${SWF} (~1.1 GB)..."
  curl -fL "$URL" -o /tmp/swift.tar.gz --retry 3
  mkdir -p /opt/swift
  tar -xzf /tmp/swift.tar.gz -C /opt/swift --strip-components=1
  rm -f /tmp/swift.tar.gz
fi
export PATH=/opt/swift/usr/bin:$PATH
# expose swift to the normal user's shells too
USER_HOME=$(getent passwd 1000 | cut -d: -f6)
if [ -n "${USER_HOME}" ] && ! grep -q "/opt/swift/usr/bin" "${USER_HOME}/.bashrc" 2>/dev/null; then
  echo 'export PATH=/opt/swift/usr/bin:$PATH' >> "${USER_HOME}/.bashrc"
fi
log "Swift: $(swift --version 2>&1 | head -1)"

if [ ! -x /usr/local/bin/xtool ]; then
  log "Downloading xtool AppImage..."
  curl -fL "https://github.com/xtool-org/xtool/releases/latest/download/xtool-x86_64.AppImage" \
       -o /usr/local/bin/xtool --retry 3
  chmod +x /usr/local/bin/xtool
fi
log "xtool: $(/usr/local/bin/xtool --help 2>&1 | head -2 | tail -1)"

log "DONE - WSL toolchain ready. Waiting on Xcode.xip for 'xtool setup'."
