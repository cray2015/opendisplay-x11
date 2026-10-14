# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""H.264 Annex B normalisation — every NAL-level rule lives here (PROJECT_SPEC.md §7.5).

Encoder output is never trusted: each access unit leaves with 4-byte start
codes only, AUDs stripped, and the current SPS+PPS in front of every IDR.
"""
NAL_SLICE = 1
NAL_IDR = 5
NAL_SEI = 6
NAL_SPS = 7
NAL_PPS = 8
NAL_AUD = 9

START = b"\x00\x00\x00\x01"


def split_nals(data: bytes) -> list[bytes]:
    """NAL unit payloads (no start codes) from an Annex B buffer with 3- or 4-byte codes."""
    nals, pos, start = [], 0, None
    while True:
        i = data.find(b"\x00\x00\x01", pos)
        if i < 0:
            break
        if start is not None:
            nals.append(data[start:i])
        start = pos = i + 3
    if start is not None:
        nals.append(data[start:])
    # A 4-byte code's leading zero (and any cabac_zero_words) land at the end of the
    # previous NAL; a NAL never legitimately ends in 0x00, so strip them.
    return [n.rstrip(b"\x00") for n in nals if n.rstrip(b"\x00")]


def nal_type(nal: bytes) -> int:
    return nal[0] & 0x1F


class Normaliser:
    """Stateful: caches the latest SPS/PPS so an IDR missing them gets them prepended."""

    def __init__(self):
        self.sps = None
        self.pps = None

    def access_unit(self, data: bytes):
        """(normalised AU bytes, is_idr). Returns (b"", False) if nothing is left to send."""
        nals = [n for n in split_nals(data) if nal_type(n) != NAL_AUD]
        for n in nals:
            if nal_type(n) == NAL_SPS:
                self.sps = n
            elif nal_type(n) == NAL_PPS:
                self.pps = n
        types = [nal_type(n) for n in nals]
        is_idr = NAL_IDR in types
        if is_idr:
            first_idr = types.index(NAL_IDR)
            head = nals[:first_idr]
            head_types = types[:first_idr]
            if NAL_SPS not in head_types or NAL_PPS not in head_types:
                if self.sps is None or self.pps is None:
                    raise ValueError("IDR before any SPS/PPS was seen")
                rest = [n for n in head if nal_type(n) not in (NAL_SPS, NAL_PPS)]
                nals = [self.sps, self.pps] + rest + nals[first_idr:]
        return b"".join(START + n for n in nals), is_idr
