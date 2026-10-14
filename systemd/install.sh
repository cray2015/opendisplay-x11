#!/bin/bash
# Installs the odx11 user service and its Cinnamon autostart entry (no sudo needed;
# the root helper is helper/install.sh).
#   bash systemd/install.sh            install, then start it now
#   bash systemd/install.sh --uninstall
set -euo pipefail
[[ $EUID -ne 0 ]] || { echo "run as your own user, not root" >&2; exit 1; }
REPO=$(cd "$(dirname "$0")/.." && pwd)
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
AUTOSTART=${XDG_CONFIG_HOME:-$HOME/.config}/autostart/odx11.desktop

if [[ ${1:-} == --uninstall ]]; then
  systemctl --user stop odx11.service 2>/dev/null || true
  rm -f "$UNIT_DIR/odx11.service" "$AUTOSTART"
  systemctl --user daemon-reload
  echo "odx11 service removed"
  exit 0
fi

[[ -x /usr/local/sbin/odx11-connector ]] || { echo "install the root helper first: sudo bash helper/install.sh" >&2; exit 1; }
mkdir -p "$UNIT_DIR" "$(dirname "$AUTOSTART")"
sed "s|@REPO@|$REPO|g" "$REPO/systemd/odx11.service" > "$UNIT_DIR/odx11.service"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=odx11
Comment=iPad as a second monitor over USB
Exec=systemctl --user start odx11.service
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
systemctl --user daemon-reload
systemctl --user restart odx11.service
echo "installed; status: $(systemctl --user is-active odx11.service)"
echo "logs: journalctl --user -u odx11 -f"
