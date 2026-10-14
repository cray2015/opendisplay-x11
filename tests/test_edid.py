# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from odx11 import edid

# `cvt -r 1568 1088 60` on this machine.
CVT = "116.00 1568 1616 1648 1728 1088 1091 1101 1119 +hsync -vsync".split()


def blob(name="iPad", device_id="7C62722D-9FE3-4473-B88A-D30DFBE0A4DA"):
    return edid.build(edid.Timing.from_modeline(CVT), name, edid.serial_for(device_id))


def decode_dtd(d: bytes):
    """Independent decode of a detailed timing descriptor (VESA E-EDID 1.4 §3.10.2)."""
    clock_khz = int.from_bytes(d[0:2], "little") * 10
    hact = d[2] | (d[4] >> 4) << 8
    hblank = d[3] | (d[4] & 0xF) << 8
    vact = d[5] | (d[7] >> 4) << 8
    vblank = d[6] | (d[7] & 0xF) << 8
    hso = d[8] | (d[11] >> 6) << 8
    hsw = d[9] | ((d[11] >> 4) & 3) << 8
    vso = (d[10] >> 4) | ((d[11] >> 2) & 3) << 4
    vsw = (d[10] & 0xF) | (d[11] & 3) << 4
    return (clock_khz, hact, hact + hso, hact + hso + hsw, hact + hblank,
            vact, vact + vso, vact + vso + vsw, vact + vblank, bool(d[17] & 2), bool(d[17] & 4))


def test_block_shape_and_checksum():
    b = blob()
    assert len(b) == 128 and b[:8] == edid.HEADER and sum(b) % 256 == 0
    assert b[18:20] == b"\x01\x04"            # EDID 1.4
    assert b[126] == 0                        # no extensions


def test_manufacturer_id_decodes_to_odx():
    v = int.from_bytes(blob()[8:10], "big")
    letters = "".join(chr(((v >> s) & 0x1F) + ord("A") - 1) for s in (10, 5, 0))
    assert letters == "ODX"


def test_preferred_timing_round_trips():
    assert decode_dtd(blob()[54:72]) == tuple(edid.Timing.from_modeline(CVT))


def test_preferred_timing_flagged_native():
    assert blob()[24] & 0x02


def test_name_and_serial():
    name, serial = edid.parse_name_and_serial(blob(name="iPad Air"))
    assert name == "iPad Air" and serial == edid.serial_for("7C62722D-9FE3-4473-B88A-D30DFBE0A4DA")


def test_serial_is_stable_per_device_and_distinct_across_devices():
    assert edid.serial_for("a") == edid.serial_for("a") != edid.serial_for("b")
    assert edid.serial_for(None) == 0


def test_long_names_truncate_to_13():
    name, _ = edid.parse_name_and_serial(blob(name="A very long iPad name"))
    assert name == "A very long i"


def test_range_limits_contain_the_mode():
    t = edid.Timing.from_modeline(CVT)
    d = blob()[90:108]
    assert d[3] == 0xFD
    vmin, vmax, hmin, hmax, clk10 = d[5], d[6], d[7], d[8], d[9]
    assert vmin <= t.refresh_hz <= vmax
    assert hmin <= t.clock_khz / t.htotal <= hmax
    assert t.clock_khz <= clk10 * 10000


def test_modes_too_big_for_a_dtd_are_refused():
    big = "1000.00 5120 5168 5200 5280 2880 2883 2888 2963 +hsync -vsync".split()
    with pytest.raises(ValueError):
        edid.build(edid.Timing.from_modeline(big), "x", 1)
