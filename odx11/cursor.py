# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The cursor channel (PROJECT_SPEC.md §7.4, upstream PROTOCOL.md §6.2): `cursor` + `cursorImg`.

Instead of drawing the cursor into the video — where it moves only as fast as
frames are captured, encoded and decoded — the pointer position goes to the
iPad as tiny JSON messages at input rate (up to 120/s, deduplicated, like the
official sender) and the sprite as a PNG whenever its shape changes. The iPad
draws the cursor itself, so it tracks the hand even while the video is paused.
Pure logic here; the X side (pointer position, XFixes sprite) is in activity.py.
"""
import base64
import struct
import zlib

from . import protocol

MAX_PNG = 24000              # upstream caps the PNG so base64 + JSON stays < 32768 (demux rule)


# --- sprite -----------------------------------------------------------------------------

def argb_to_rgba(pixels, width: int, height: int) -> bytes:
    """XFixes cursor pixels (premultiplied ARGB32 ints) → straight-alpha RGBA bytes."""
    out = bytearray(width * height * 4)
    for i, p in enumerate(pixels[:width * height]):
        a = (p >> 24) & 0xFF
        r, g, b = (p >> 16) & 0xFF, (p >> 8) & 0xFF, p & 0xFF
        if a and a < 255:                       # un-premultiply
            r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
        out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return bytes(out)


def png(width: int, height: int, rgba: bytes) -> bytes:
    """A minimal RGBA PNG (no image library needed)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def sprite_message(width, height, xhot, yhot, pixels, geom) -> dict | None:
    """`cursorImg`: size normalised to the display, hotspot normalised to the sprite."""
    if width <= 0 or height <= 0 or geom is None:
        return None
    data = png(width, height, argb_to_rgba(pixels, width, height))
    if len(data) > MAX_PNG:
        return None                             # absurdly large cursor: skip rather than break demux
    return {"type": "cursorImg", "nw": round(width / geom.w, 5), "nh": round(height / geom.h, 5),
            "ax": round(xhot / width, 4), "ay": round(yhot / height, 4),
            "png": base64.b64encode(data).decode("ascii")}


# --- position -----------------------------------------------------------------------------

def position_message(px: int, py: int, geom) -> dict:
    """`cursor` in normalised video coordinates, or hidden when off the virtual monitor."""
    if geom is None or not (geom.x <= px < geom.x + geom.w and geom.y <= py < geom.y + geom.h):
        return {"type": "cursor", "v": 0}
    return {"type": "cursor", "x": round((px - geom.x) / geom.w, 4),
            "y": round((py - geom.y) / geom.h, 4), "v": 1}


class CursorSender:
    """Rate-limited, deduplicated `cursor` messages plus `cursorImg` on shape changes.

    send(bytes) sends a framed control message; clock() is monotonic seconds;
    call_later(seconds, fn) -> cancel() schedules the trailing update."""

    def __init__(self, cfg, send, clock, call_later):
        self.cfg = cfg                          # the [cursor] section, read live
        self.send = send
        self.clock = clock
        self.call_later = call_later
        self.geom = None
        self._last = None                       # last position message sent
        self._last_at = -1.0
        self._pending = None                    # latest position waiting for the rate limit
        self._cancel = None
        self._serial = None

    def reset(self, geom, sprite=None, pointer=None):
        """New stream or new geometry: resend everything."""
        self.geom = geom
        self._last, self._serial = None, None
        self._last_at = -1.0                    # a new stream gets its position at once
        if self._cancel:
            self._cancel()
            self._cancel = None
        if sprite is not None:
            self.sprite(*sprite)
        if pointer is not None:
            self.update(*pointer)

    def update(self, px: int, py: int):
        if not self.cfg.enabled:
            return
        msg = position_message(px, py, self.geom)
        if msg == self._last:
            return
        wait = 1.0 / self.cfg.max_rate_hz - (self.clock() - self._last_at)
        if wait <= 0:
            self._emit(msg)
        else:
            self._pending = msg
            if self._cancel is None:
                self._cancel = self.call_later(wait, self._flush)

    def _flush(self):
        self._cancel = None
        if self._pending is not None:
            msg, self._pending = self._pending, None
            if msg != self._last:
                self._emit(msg)

    def _emit(self, msg):
        self._last, self._last_at = msg, self.clock()
        self._pending = None
        self.send(protocol.encode_control(msg))

    def sprite(self, width, height, xhot, yhot, pixels, serial=None):
        """Send the sprite if its *image* changed. X gives each window its own cursor object,
        so the serial changes whenever the pointer crosses windows even for the same arrow."""
        if not self.cfg.enabled:
            return
        key = (width, height, xhot, yhot, hash(tuple(pixels)))
        if key == self._serial:
            return
        msg = sprite_message(width, height, xhot, yhot, pixels, self.geom)
        if msg is not None:
            self._serial = key
            self.send(protocol.encode_control(msg))

    def stop(self):
        if self._cancel:
            self._cancel()
            self._cancel = None
