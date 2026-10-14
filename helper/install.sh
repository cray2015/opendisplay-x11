#!/bin/bash
# Installs the root connector helper and its sudoers rule (PROJECT_SPEC.md §8.5).
#   sudo bash helper/install.sh [cardN-HDMI-A-M]     default: card1-HDMI-A-1
#   sudo bash helper/install.sh --uninstall
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
USER_NAME=${SUDO_USER:?run via sudo from your own account}
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/usr/local/sbin/odx11-connector
ALLOW_DIR=/etc/odx11
SUDOERS=/etc/sudoers.d/odx11

if [[ ${1:-} == --uninstall ]]; then
  "$BIN" detect 2>/dev/null || true
  rm -f "$BIN" "$SUDOERS" "$ALLOW_DIR/connector"
  rmdir "$ALLOW_DIR" 2>/dev/null || true
  echo "odx11 helper removed"
  exit 0
fi

CONN=${1:-card1-HDMI-A-1}
[[ $CONN =~ ^card[0-9]+-HDMI-A-[0-9]+$ ]] || { echo "connector must look like card1-HDMI-A-1" >&2; exit 1; }
[[ -e /sys/class/drm/$CONN/status ]] || { echo "no such connector /sys/class/drm/$CONN" >&2; exit 1; }
[[ $(cat /sys/class/drm/$CONN/status) == disconnected ]] || {
  echo "$CONN is not disconnected — is a real monitor on it?" >&2; exit 1; }

install -o root -g root -m 0755 "$HERE/odx11-connector" "$BIN"
install -d -o root -g root -m 0755 "$ALLOW_DIR"
echo "$CONN" > "$ALLOW_DIR/connector"
chmod 0644 "$ALLOW_DIR/connector"

tmp=$(mktemp)
echo "$USER_NAME ALL=(root) NOPASSWD: $BIN" > "$tmp"
visudo -cf "$tmp" >/dev/null
install -o root -g root -m 0440 "$tmp" "$SUDOERS"
rm -f "$tmp"

echo "installed $BIN for $USER_NAME, allowlisted connector $CONN"
echo "set [display] connector = \"${CONN#card*-}\" in ~/.config/odx11/config.toml if not the default"
