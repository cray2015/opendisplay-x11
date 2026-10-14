#!/bin/bash
# opendisplay-x11 installer — run as your own user from your X11 session.
#
#   bash install.sh                    check, then install helper (sudo) + user service
#   bash install.sh --check            only report what is missing; change nothing
#   bash install.sh --install-deps     also apt-install missing packages (sudo)
#   bash install.sh --connector card1-HDMI-A-2   force a specific spare connector
#   bash install.sh --uninstall        remove service, autostart entry and root helper
#
# What it installs:
#   /usr/local/sbin/odx11-connector, /etc/odx11/connector, /etc/sudoers.d/odx11  (root, via sudo)
#   ~/.config/systemd/user/odx11.service, ~/.config/autostart/odx11.desktop      (you)
#   ~/.config/odx11/config.toml   only if the connector is not the default and no config exists
set -uo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}/odx11/config.toml
PACKAGES=(usbmuxd libimobiledevice-utils python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
          gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-vaapi
          python3-xlib vainfo)

MODE=install CONNECTOR="" INSTALL_DEPS=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --check) MODE=check ;;
    --uninstall) MODE=uninstall ;;
    --install-deps) INSTALL_DEPS=1 ;;
    --connector) CONNECTOR=${2:?--connector needs a value like card1-HDMI-A-1}; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '  \033[31mfail\033[0m  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
WARNINGS=0 FAILURES=0

[[ $EUID -ne 0 ]] || { echo "run as your own user, not root (sudo is used only where needed)" >&2; exit 1; }

if [[ $MODE == uninstall ]]; then
  bash "$REPO/systemd/install.sh" --uninstall
  if [[ -e /usr/local/sbin/odx11-connector || -e /etc/sudoers.d/odx11 ]]; then
    sudo bash "$REPO/helper/install.sh" --uninstall
  fi
  echo "Left in place: $CONFIG (your settings) and Cinnamon/desktop monitor layouts."
  exit 0
fi

echo "== session"
if [[ ${XDG_SESSION_TYPE:-} == x11 && -n ${DISPLAY:-} ]]; then ok "X11 session on $DISPLAY"
else fail "needs an X11 session (XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset}); Wayland is not supported"; fi
command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1 \
  && ok "systemd user manager" || fail "systemd user manager not available"
if systemctl --user show-environment 2>/dev/null | grep -q '^DISPLAY='; then ok "DISPLAY is exported to the user manager"
else warn "DISPLAY not in the systemd user environment — add 'systemctl --user import-environment DISPLAY XAUTHORITY' to your session startup (bare window managers)"; fi

echo "== packages"
MISSING=()
for p in "${PACKAGES[@]}"; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || MISSING+=("$p")
done
if [[ ${#MISSING[@]} -eq 0 ]]; then ok "all packages present"
elif [[ $INSTALL_DEPS -eq 1 && $MODE == install ]]; then
  echo "  installing: ${MISSING[*]}"; sudo apt-get install -y "${MISSING[@]}" && ok "packages installed" || fail "apt-get failed"
else fail "missing: ${MISSING[*]}  (re-run with --install-deps, or: sudo apt install ${MISSING[*]})"; fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' && ok "python $(python3 -V | cut -d' ' -f2)" \
  || fail "python 3.11+ required (tomllib)"

echo "== GPU and encoder"
DRIVER=$(basename "$(readlink -f /sys/class/drm/card*/device/driver 2>/dev/null | head -1)" 2>/dev/null)
case $DRIVER in
  i915|xe) ok "GPU driver $DRIVER (tested: Intel HD 530 / i915)" ;;
  amdgpu)  warn "GPU driver amdgpu: the forced connector should work; encoder settings are untested" ;;
  *)       fail "GPU driver '${DRIVER:-none}': the forced-connector virtual monitor needs a kernel DRM driver (i915/amdgpu); NVIDIA's proprietary driver cannot do this" ;;
esac
if gst-inspect-1.0 vaapih264enc >/dev/null 2>&1; then ok "VA-API H.264 encoder (vaapih264enc)"
else fail "no vaapih264enc — install gstreamer1.0-vaapi and a VA-API driver (Intel: intel-media-va-driver)"; fi

echo "== spare connector"
ALLOWED=$(tr -d '[:space:]' < /etc/odx11/connector 2>/dev/null)
# Re-install: keep the connector already allowlisted (it reads "connected" while we force it on).
[[ -z $CONNECTOR && -n $ALLOWED ]] && CONNECTOR=$ALLOWED
if [[ -z $CONNECTOR ]]; then
  for s in /sys/class/drm/card*-HDMI-A-*/status; do
    [[ -e $s && $(cat "$s") == disconnected ]] && { CONNECTOR=$(basename "$(dirname "$s")"); break; }
  done
fi
if [[ -n $CONNECTOR && -e /sys/class/drm/$CONNECTOR/status ]]; then
  if [[ $CONNECTOR == "$ALLOWED" ]]; then
    ok "using $CONNECTOR (already allowlisted by an earlier install)"
  elif [[ $(cat "/sys/class/drm/$CONNECTOR/status") == disconnected ]]; then
    ok "using $CONNECTOR (nothing may be plugged into this port)"
  else fail "$CONNECTOR has a monitor attached — pick another with --connector"; fi
else fail "no disconnected HDMI-A connector found (ls /sys/class/drm); a spare HDMI/DP++ port is required"; fi
LOCKDOWN=$(grep -o '\[[a-z]*\]' /sys/kernel/security/lockdown 2>/dev/null | tr -d '[]')
[[ -n $LOCKDOWN && $LOCKDOWN != none ]] && warn "kernel lockdown '$LOCKDOWN' (Secure Boot): no runtime EDID, so the monitor is listed as 'unknown' — harmless"

echo "== iPad"
if command -v idevice_id >/dev/null; then
  if [[ -n $(idevice_id -l 2>/dev/null) ]]; then
    idevicepair validate >/dev/null 2>&1 && ok "iPad attached and paired" \
      || warn "iPad attached but not paired: unlock it, tap Trust, then run 'idevicepair pair'"
  else warn "no iPad attached right now (fine — it can be plugged in later)"; fi
fi

echo
if [[ $FAILURES -gt 0 ]]; then echo "$FAILURES problem(s) above must be fixed first."; exit 1; fi
if [[ $MODE == check ]]; then echo "Ready to install ($WARNINGS warning(s)). Run: bash install.sh"; exit 0; fi

echo "== installing root helper (sudo)"
sudo bash "$REPO/helper/install.sh" "$CONNECTOR" || { echo "helper install failed" >&2; exit 1; }
KCONN=${CONNECTOR#card*-}
if [[ $KCONN != HDMI-A-1 ]]; then
  if [[ -e $CONFIG ]]; then
    grep -q "connector *= *\"$KCONN\"" "$CONFIG" || echo "  NOTE: set [display] connector = \"$KCONN\" in $CONFIG"
  else
    mkdir -p "$(dirname "$CONFIG")"
    printf '# written by install.sh — see config.example.toml for every setting\n[display]\nconnector = "%s"\n' "$KCONN" > "$CONFIG"
    ok "wrote $CONFIG with connector $KCONN"
  fi
fi

echo "== installing user service"
bash "$REPO/systemd/install.sh" || { echo "service install failed" >&2; exit 1; }
echo
echo "Done. Plug in the iPad, unlock it, open OpenDisplay — the desktop extends."
echo "Arrange it in your desktop's display settings; logs: journalctl --user -u odx11 -f"
