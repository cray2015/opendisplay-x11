# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""EDID 1.4 base block for the virtual monitor (PROJECT_SPEC.md §4, §8.1).

Gives the forced connector a stable identity per iPad (manufacturer + product
+ a serial derived from `hello.id`), a readable name in Cinnamon's Display
settings, and our logical mode as the preferred timing — so no `--newmode`
is needed when the kernel honours the override. Landscape only; portrait is
`xrandr --rotate` (§4).
"""
import hashlib
from typing import NamedTuple

HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
MANUFACTURER = "ODX"
PRODUCT_CODE = 0x0001
# Standard sRGB chromaticity coordinates, as most monitors encode them.
SRGB_CHROMA = bytes.fromhex("ee91a3544c99260f5054")


class Timing(NamedTuple):
    """A modeline: clock in kHz, then horizontal and vertical timings, sync polarities."""
    clock_khz: int
    hdisp: int
    hsync_start: int
    hsync_end: int
    htotal: int
    vdisp: int
    vsync_start: int
    vsync_end: int
    vtotal: int
    hsync_pos: bool
    vsync_pos: bool

    @classmethod
    def from_modeline(cls, fields: list[str]) -> "Timing":
        """From `cvt -r` modeline fields after the name: clock MHz, 8 timings, flags."""
        clock = round(float(fields[0]) * 1000)
        nums = [int(f) for f in fields[1:9]]
        flags = {f.lower() for f in fields[9:]}
        return cls(clock, *nums, "+hsync" in flags, "+vsync" in flags)

    @property
    def refresh_hz(self) -> float:
        return self.clock_khz * 1000 / (self.htotal * self.vtotal)


def _manufacturer_id(code: str) -> bytes:
    v = 0
    for ch in code:
        v = (v << 5) | (ord(ch) - ord("A") + 1)
    return v.to_bytes(2, "big")


def serial_for(device_id: str | None) -> int:
    """Stable 32-bit serial from the receiver's per-install id; 0 when unknown."""
    if not device_id:
        return 0
    return int.from_bytes(hashlib.sha256(device_id.encode()).digest()[:4], "little") or 1


def _dtd(t: Timing) -> bytes:
    hblank, vblank = t.htotal - t.hdisp, t.vtotal - t.vdisp
    hso, hsw = t.hsync_start - t.hdisp, t.hsync_end - t.hsync_start
    vso, vsw = t.vsync_start - t.vdisp, t.vsync_end - t.vsync_start
    clock10k = round(t.clock_khz / 10)
    if not (0 < clock10k < 65536 and t.hdisp < 4096 and hblank < 4096 and t.vdisp < 4096
            and vblank < 4096 and hso < 1024 and hsw < 1024 and vso < 64 and vsw < 64):
        raise ValueError(f"timing does not fit a detailed timing descriptor: {t}")
    flags = 0x18 | (0x04 if t.vsync_pos else 0) | (0x02 if t.hsync_pos else 0)
    return bytes([
        clock10k & 0xFF, clock10k >> 8,
        t.hdisp & 0xFF, hblank & 0xFF, ((t.hdisp >> 8) << 4) | (hblank >> 8),
        t.vdisp & 0xFF, vblank & 0xFF, ((t.vdisp >> 8) << 4) | (vblank >> 8),
        hso & 0xFF, hsw & 0xFF, ((vso & 0xF) << 4) | (vsw & 0xF),
        ((hso >> 8) << 6) | ((hsw >> 8) << 4) | ((vso >> 4) << 2) | (vsw >> 4),
        0, 0, 0,                 # image size unknown (0 mm, as the forced connector reports today)
        0, 0,                    # no borders
        flags,                   # digital separate sync
    ])


def _text_descriptor(tag: int, text: str) -> bytes:
    raw = text.encode("ascii", "replace")[:13]
    if len(raw) < 13:
        raw += b"\x0a" + b"\x20" * (12 - len(raw))
    return bytes([0, 0, 0, tag, 0]) + raw


def _range_descriptor(t: Timing) -> bytes:
    hfreq_khz = t.clock_khz / t.htotal
    max_clock_10mhz = min(255, -(-t.clock_khz // 10000) + 1)
    return bytes([0, 0, 0, 0xFD, 0,
                  max(1, int(t.refresh_hz) - 10), min(255, int(t.refresh_hz) + 10),
                  max(1, int(hfreq_khz) - 10), min(255, int(hfreq_khz) + 10),
                  max_clock_10mhz, 0x01, 0x0A]) + b"\x20" * 6


def build(timing: Timing, name: str, serial: int, year: int = 2026) -> bytes:
    """A 128-byte EDID 1.4 block with `timing` as the preferred (and only) mode."""
    b = bytearray(HEADER)
    b += _manufacturer_id(MANUFACTURER)
    b += PRODUCT_CODE.to_bytes(2, "little")
    b += (serial & 0xFFFFFFFF).to_bytes(4, "little")
    b += bytes([0, year - 1990])                # week unknown, year
    b += bytes([1, 4])                          # EDID 1.4
    b += bytes([0x80, 0, 0])                    # digital input; screen size unknown
    b += bytes([120, 0x06])                     # gamma 2.2; sRGB default, preferred timing native
    b += SRGB_CHROMA
    b += bytes([0, 0, 0])                       # no established timings
    b += b"\x01\x01" * 8                        # no standard timings
    b += _dtd(timing)
    b += _text_descriptor(0xFC, name)           # monitor name
    b += _range_descriptor(timing)
    b += _text_descriptor(0xFF, f"{serial:08X}")   # serial string, shown by some tools
    b += bytes([0])                             # no extension blocks
    b += bytes([(-sum(b)) & 0xFF])
    if len(b) != 128:
        raise ValueError(f"EDID base block must be 128 bytes, got {len(b)}")
    return bytes(b)


def parse_name_and_serial(blob: bytes) -> tuple[str | None, int]:
    """(monitor name, numeric serial) — for tests and for checking what the kernel holds."""
    serial = int.from_bytes(blob[12:16], "little")
    name = None
    for off in (54, 72, 90, 108):
        d = blob[off:off + 18]
        if d[:3] == b"\x00\x00\x00" and d[3] == 0xFC:
            name = d[5:].split(b"\x0a")[0].decode("ascii").rstrip()
    return name, serial
