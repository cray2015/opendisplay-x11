# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from odx11.annexb import Normaliser, split_nals

SPS = b"\x67\x64\x00\x28\xac"
PPS = b"\x68\xee\x3c\x80"
IDR = b"\x65\x88\x84\x00\x10"
P = b"\x41\x9a\x02\x03"
AUD = b"\x09\x10"
S4 = b"\x00\x00\x00\x01"
S3 = b"\x00\x00\x01"


def types(au):
    return [n[0] & 0x1F for n in split_nals(au)]


def test_three_byte_codes_become_four():
    au, _ = Normaliser().access_unit(S3 + SPS + S3 + PPS + S4 + IDR)
    assert au == S4 + SPS + S4 + PPS + S4 + IDR
    assert au.count(S3) == au.count(S4)                  # every 00 00 01 is part of a 4-byte code


def test_aud_is_stripped():
    n = Normaliser()
    n.access_unit(S4 + SPS + S4 + PPS + S4 + IDR)
    au, is_idr = n.access_unit(S4 + AUD + S4 + P)
    assert au == S4 + P and not is_idr


def test_bare_idr_gets_cached_sps_pps():
    n = Normaliser()
    n.access_unit(S4 + SPS + S4 + PPS + S4 + IDR)
    au, is_idr = n.access_unit(S4 + AUD + S4 + IDR)
    assert is_idr and types(au) == [7, 8, 5]


def test_idr_with_only_pps_gets_both_in_order():
    n = Normaliser()
    n.access_unit(S4 + SPS + S4 + PPS + S4 + IDR)
    au, _ = n.access_unit(S4 + PPS + S4 + IDR)
    assert types(au) == [7, 8, 5]


def test_sps_change_is_cached():
    n = Normaliser()
    n.access_unit(S4 + SPS + S4 + PPS + S4 + IDR)
    new_sps = b"\x67\x64\x00\x29\xff"
    n.access_unit(S4 + new_sps + S4 + PPS + S4 + IDR)
    au, _ = n.access_unit(S4 + IDR)
    assert split_nals(au)[0] == new_sps


def test_multi_slice_idr_kept_together():
    au, is_idr = Normaliser().access_unit(S4 + SPS + S4 + PPS + S4 + IDR + S4 + IDR)
    assert is_idr and types(au) == [7, 8, 5, 5]


def test_trailing_zero_bytes_do_not_leak_into_nals():
    au, _ = Normaliser().access_unit(S4 + SPS + b"\x00\x00" + S4 + PPS + S4 + IDR)
    assert split_nals(au)[0] == SPS


def test_idr_before_any_parameter_sets_is_an_error():
    with pytest.raises(ValueError):
        Normaliser().access_unit(S4 + IDR)


def test_aud_only_buffer_yields_nothing():
    assert Normaliser().access_unit(S4 + AUD) == (b"", False)


# --- edges found by mutation testing ---------------------------------------------------

def test_only_trailing_zero_bytes_are_stripped():
    # Spaces and 'X' are ordinary payload bytes; a leading zero inside a NAL is kept.
    assert split_nals(S4 + b"\x41 X" + S4 + b"\x41\x00X\x00\x00") == [b"\x41 X", b"\x41\x00X"]
    assert split_nals(S4 + b" " + S4 + b"\x00\x00") == [b" "]          # all-zero NALs vanish


def test_the_first_idr_ever_needs_parameter_sets_by_name():
    with pytest.raises(ValueError) as e:
        Normaliser().access_unit(S4 + IDR)
    assert str(e.value) == "IDR before any SPS/PPS was seen"


def test_idr_with_sps_but_no_pps_gets_the_cached_pair():
    n = Normaliser()
    n.access_unit(S4 + SPS + S4 + PPS + S4 + IDR)
    au, is_idr = n.access_unit(S4 + SPS + S4 + IDR)
    assert is_idr and au == S4 + SPS + S4 + PPS + S4 + IDR


def test_a_cached_sps_alone_is_not_enough():
    n = Normaliser()
    n.access_unit(S4 + SPS + S4 + P)              # SPS seen, PPS never
    with pytest.raises(ValueError):
        n.access_unit(S4 + IDR)
    n2 = Normaliser()
    n2.access_unit(S4 + PPS + S4 + P)             # PPS seen, SPS never
    with pytest.raises(ValueError):
        n2.access_unit(S4 + IDR)


def test_a_nal_of_only_x_bytes_is_kept():
    assert split_nals(S4 + b"X" + S4 + b"XX\x00") == [b"X", b"XX"]
