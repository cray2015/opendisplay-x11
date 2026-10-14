# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenDisplay wire format: framing, demux rule and control messages.

The only module that knows the wire format (PROJECT_SPEC.md §7.2). Protocol v4
replaces the demux heuristic with a typed frame header; that swap is this file.
"""
import json
import struct

HEADER = struct.Struct(">I")
MAX_INBOUND = 1 << 20          # receiver → sender payloads are 1 .. 2^20-1 bytes
MAX_CONTROL = 32768            # sender → receiver control must stay below this

SENDER_PV = 2                  # stays 2 until Pencil handling exists (§4)
SENDER_MIN_PV = 1


class ProtocolError(Exception):
    pass


def frame(payload: bytes) -> bytes:
    return HEADER.pack(len(payload)) + payload


def is_control(payload: bytes) -> bool:
    """The receiver's demux rule: JSON iff short, starts with '{', and NUL-free."""
    return len(payload) < MAX_CONTROL and payload[:1] == b"{" and b"\x00" not in payload


def encode_control(msg: dict) -> bytes:
    """A framed control message, refusing anything the receiver would demux as video."""
    payload = json.dumps(msg, separators=(",", ":")).encode()
    if not is_control(payload):
        raise ProtocolError(f"control message would demux as video ({len(payload)} bytes)")
    return frame(payload)


def encode_video(access_unit: bytes, cap_ms: int | None = None, snd_ms: int | None = None) -> bytes:
    """A framed video access unit, optionally behind the telemetry prefix
    `{"cap":<ms>,"snd":<ms>}` (upstream §5.1) the receiver uses for end-to-end latency.
    Annex B start codes put NUL bytes in every frame, so it still demuxes as video."""
    payload = access_unit
    if cap_ms is not None and snd_ms is not None:
        payload = json.dumps({"cap": int(cap_ms), "snd": int(snd_ms)}, separators=(",", ":")).encode() + access_unit
    if is_control(payload):
        raise ProtocolError("video frame would demux as control")
    return frame(payload)


def welcome() -> bytes:
    return encode_control({"type": "welcome", "pv": SENDER_PV, "min": SENDER_MIN_PV})


def pong(t, now_ms: int) -> bytes:
    return encode_control({"type": "pong", "t": t, "mt": now_ms})


def ping(**health) -> bytes:
    return encode_control({"type": "ping", **health})


class Deframer:
    """Reassembles receiver → sender frames across arbitrary socket reads."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out = []
        while len(self._buf) >= HEADER.size:
            (n,) = HEADER.unpack_from(self._buf)
            if n == 0 or n >= MAX_INBOUND:
                raise ProtocolError(f"bad inbound frame length {n}")
            if len(self._buf) < HEADER.size + n:
                break
            out.append(bytes(self._buf[HEADER.size:HEADER.size + n]))
            del self._buf[:HEADER.size + n]
        return out


def decode_control(payload: bytes):
    """The message dict, or None for anything unparseable or untyped (ignored, never fatal)."""
    try:
        msg = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None
    return msg


def _int_at_least(v, lo):
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo


def panel_pixels(hello: dict):
    """Physical panel (w, h) in the current orientation, from `panel` when valid (upstream
    §6.7), else the deprecated `pixelsWide/High`. None if neither is usable."""
    panel = hello.get("panel")
    if isinstance(panel, dict):
        pw, ph = panel.get("pixelsWide"), panel.get("pixelsHigh")
        scale = panel.get("scale")
        points = (panel.get("pointsWide"), panel.get("pointsHigh"))
        points_ok = points == (None, None) or all(_int_at_least(p, 2) for p in points)
        scale_ok = isinstance(scale, (int, float)) and not isinstance(scale, bool) and 0 < scale < float("inf")
        if _int_at_least(pw, 2) and _int_at_least(ph, 2) and scale_ok and points_ok:
            return pw, ph
    pw, ph = hello.get("pixelsWide"), hello.get("pixelsHigh")
    if _int_at_least(pw, 2) and _int_at_least(ph, 2):
        return pw, ph
    return None
