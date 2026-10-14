#!/bin/bash
# M0 spike — AC 4/5/6/8 and the HuC/EDID open questions. Run in your own
# terminal (needs sudo). Reverts itself after the hold, on Ctrl-C and on error.
#   bash spike/m0_connector.sh [HDMI-A-1|HDMI-A-2] [hold seconds] [WxH]
# WxH defaults to the §8.2 mode for the iPad Air 5 (2360x1640 / 1.5).
set -u
KCONN=${1:-HDMI-A-1}                 # kernel name; xrandr name drops the "-A"
XOUT=${KCONN/HDMI-A-/HDMI-}
HOLD=${2:-30}
MODE=${3:-1568x1088}
W=${MODE%x*}; H=${MODE#*x}
CARD=$(basename /sys/class/drm/card*-"$KCONN" | cut -d- -f1)
STATUS=/sys/class/drm/$CARD-$KCONN/status
MAIN=$(xrandr --query | awk '/ connected primary/{print $1}')
MAIN_W=$(xrandr --query | awk '/ connected primary/{split($4,a,"x"); print a[1]}')
DRI=${CARD#card}
OUT=$(dirname "$0")/out
mkdir -p "$OUT"

revert() {
  echo "== reverting"
  xrandr --output "$XOUT" --off 2>/dev/null
  xrandr --delmode "$XOUT" "odx11_$MODE" 2>/dev/null
  xrandr --rmmode "odx11_$MODE" 2>/dev/null
  echo detect | sudo tee "$STATUS" >/dev/null
  sleep 1; xrandr --query | grep "^$XOUT"
  echo "AC 6: did windows on the virtual monitor return to $MAIN?"
}
trap revert EXIT

echo "== target $KCONN ($XOUT) on $CARD, main output $MAIN, mode $MODE"
sudo -v || exit 1

echo "== HuC firmware (decides whether VA-API CBR is possible)"
sudo sh -c "cat /sys/kernel/debug/dri/$DRI/gt*/uc/huc_info 2>/dev/null || cat /sys/kernel/debug/dri/$DRI/i915_huc_load_status" 2>&1 | head -8

echo "== forcing on"
sudo dmesg -c >/dev/null
echo on | sudo tee "$STATUS" >/dev/null
sleep 2
xrandr --query | grep "^$XOUT"

echo "== adding §8.2 mode $MODE (cvt -r) and enabling right of $MAIN"
MODELINE=$(cvt -r "$W" "$H" 60 | sed -n 's/^Modeline "[^"]*" //p')
xrandr --newmode "odx11_$MODE" $MODELINE 2>/dev/null
xrandr --addmode "$XOUT" "odx11_$MODE"
xrandr --output "$XOUT" --mode "odx11_$MODE" --right-of "$MAIN" && echo "xrandr OK"
sleep 3
xrandr --listmonitors
echo "== dmesg since force"
sudo dmesg | grep -iE 'drm|i915|link|hdmi|error|fail' | tail -15

echo "== AC 8: capture the virtual output region + VA-API encode, 20 s"
/usr/bin/time -f "capture+encode $MODE: wall=%es user=%Us sys=%Ss cpu=%P" \
  gst-launch-1.0 -q ximagesrc display-name="$DISPLAY" startx="$MAIN_W" starty=0 \
    endx=$((MAIN_W + W - 1)) endy=$((H - 1)) use-damage=false show-pointer=true num-buffers=600 \
  ! video/x-raw,framerate=30/1 ! queue leaky=downstream max-size-buffers=1 \
  ! videoconvert n-threads=2 ! video/x-raw,format=NV12 \
  ! vaapih264enc tune=low-power rate-control=cqp max-bframes=0 keyframe-period=1000000 \
  ! h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au \
  ! filesink location="$OUT/virtual.h264" 2>&1 | grep -v CRITICAL
echo "  bytes $(stat -c %s "$OUT/virtual.h264")  (play: gst-play-1.0 $OUT/virtual.h264)"

echo "== holding $HOLD s — drag a window onto the virtual monitor now (AC 6)"
sleep "$HOLD"
