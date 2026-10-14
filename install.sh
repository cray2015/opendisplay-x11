#!/bin/bash
# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
# Developer install from a git checkout. End users don't need this: they double-click the
# .deb. This checks the machine, builds the same .deb from the source tree, and installs it
# through the package manager — so developers run exactly what users run.
#
#   bash install.sh --check        report what is missing; change nothing
#   bash install.sh                build dist/opendisplay-x11_*.deb and install it (password dialog)
#   bash install.sh --build        only build the .deb
#   bash install.sh --uninstall    remove the package (password dialog)
set -uo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
MODE=install
case ${1:-} in
  --check) MODE=check ;; --build) MODE=build ;; --uninstall) MODE=uninstall ;;
  "") ;; -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
esac
[[ $EUID -ne 0 ]] || { echo "run as your own user; the password dialog appears when needed" >&2; exit 1; }

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mfail\033[0m  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
FAILURES=0
as_root() { if command -v pkexec >/dev/null; then pkexec "$@"; else sudo "$@"; fi; }

if [[ $MODE == uninstall ]]; then
  as_root apt-get remove -y opendisplay-x11
  exit $?
fi

echo "== machine"
[[ ${XDG_SESSION_TYPE:-} == x11 && -n ${DISPLAY:-} ]] && ok "X11 session on $DISPLAY" \
  || fail "needs an X11 session (XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset}); Wayland is not supported"
DRIVER=$(basename "$(readlink -f /sys/class/drm/card*/device/driver 2>/dev/null | head -1)" 2>/dev/null)
case $DRIVER in
  i915|xe) ok "GPU driver $DRIVER (tested: Intel HD 530 / i915)" ;;
  amdgpu)  warn "GPU driver amdgpu: the virtual monitor should work; encoder settings are untested" ;;
  *)       fail "GPU driver '${DRIVER:-none}': needs a kernel DRM driver (i915/amdgpu); NVIDIA's proprietary driver cannot force a connector" ;;
esac
FREE=$(for s in /sys/class/drm/card*-HDMI-A-*/status; do [[ -e $s && $(cat "$s") == disconnected ]] && basename "$(dirname "$s")"; done | head -1)
[[ -n $FREE ]] && ok "free video port available ($FREE)" || warn "no free video port (HDMI, or DisplayPort with an HDMI alias) right now — required, unless odx11 is already using it; see README 'Why a free video port is required'"
id -nG | tr ' ' '\n' | grep -qx plugdev && ok "$USER is in plugdev (may force the connector once the package's udev rule is installed)" \
  || fail "$USER is not in the plugdev group — add it (Users and Groups settings) and log out and back in"
if command -v idevice_id >/dev/null && [[ -n $(idevice_id -l 2>/dev/null) ]]; then
  idevicepair validate >/dev/null 2>&1 && ok "iPad attached and paired" || warn "iPad attached but not paired: unlock it and tap Trust"
fi
for f in "$HOME/.config/systemd/user/odx11.service" /usr/local/sbin/odx11-connector /etc/sudoers.d/odx11; do
  [[ -e $f ]] && fail "leftover from the pre-package developer install: $f — remove it (the user unit would shadow the packaged one)"
done

echo "== build tools"
MISSING=$(cd "$REPO" && dpkg-checkbuilddeps 2>&1 | sed -n 's/.*Unmet build dependencies: //p')
command -v dpkg-buildpackage >/dev/null || MISSING="dpkg-dev $MISSING"
if [[ -z $MISSING ]]; then ok "build dependencies present"
elif [[ $MODE == check ]]; then fail "missing build tools: $MISSING"
else echo "  installing build tools: $MISSING"; as_root apt-get install -y --no-install-recommends $MISSING || fail "could not install build tools"; fi

echo
[[ $FAILURES -eq 0 ]] || { echo "$FAILURES problem(s) above must be fixed first."; exit 1; }
[[ $MODE == check ]] && { echo "Ready. Run: bash install.sh"; exit 0; }

echo "== building"
( cd "$REPO" && dpkg-buildpackage -b -us -uc ) > "$REPO/dist.build.log" 2>&1 \
  || { echo "build failed — see dist.build.log" >&2; exit 1; }
mkdir -p "$REPO/dist"
mv "$REPO"/../opendisplay-x11_*_all.deb "$REPO"/../opendisplay-x11_*.buildinfo "$REPO"/../opendisplay-x11_*.changes "$REPO/dist/" 2>/dev/null
DEB=$(ls -t "$REPO"/dist/opendisplay-x11_*_all.deb | head -1)
ok "built $(basename "$DEB") (unit tests passed during the build)"
[[ $MODE == build ]] && exit 0

echo "== installing (password dialog)"
as_root apt-get install -y "$DEB" || { echo "install failed" >&2; exit 1; }
systemctl --user daemon-reload
odx11 autostart
echo
echo "Installed and started. Plug in the iPad, unlock it, open OpenDisplay."
echo "Logs: journalctl --user -u odx11 -f"
