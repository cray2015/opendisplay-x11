#!/usr/bin/env python3
"""A strict stand-in for the iPad receiver, for testing without one.

Listens like the real receiver, sends `hello` and a ping every 2 s, then
checks everything the sender transmits against PROJECT_SPEC.md §7.5:
4-byte start codes only, no AUDs, SPS+PPS before every IDR, first frame an
IDR, control messages that satisfy the demux rule, and that a `kf` makes the
next video frame an IDR. Exits non-zero on any violation.

  python3 tools/fake_receiver.py --port 9000 [--seconds 10] [--kf-at 3] [--portrait]
  python3 -m odx11 run --tcp 127.0.0.1:9000           # in another terminal
"""
import argparse
import json
import socket
import struct
import sys
import time
import uuid

S4 = b"\x00\x00\x00\x01"


def nal_types(au: bytes):
    out, i = [], au.find(S4)
    while i >= 0:
        out.append(au[i + 4] & 0x1F)
        i = au.find(S4, i + 4)
    return out


def has_three_byte_code(au: bytes) -> bool:
    i = au.find(b"\x00\x00\x01")
    while i >= 0:
        if i == 0 or au[i - 1] != 0:
            return True
        i = au.find(b"\x00\x00\x01", i + 3)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--kf-at", type=float, default=3, help="send kf this many seconds in")
    ap.add_argument("--portrait", action="store_true")
    a = ap.parse_args()

    w, h = (1640, 2360) if a.portrait else (2360, 1640)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", a.port))
    srv.listen(1)
    print(f"fake receiver on 127.0.0.1:{a.port}, panel {w}x{h}")
    c, peer = srv.accept()
    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print("sender connected from", peer)

    def tx(obj):
        p = json.dumps(obj).encode()
        c.sendall(struct.pack(">I", len(p)) + p)

    tx({"type": "hello", "pixelsWide": w, "pixelsHigh": h, "scale": 2, "device": "iPad",
        "id": str(uuid.uuid4()).upper(), "pv": 3,
        "panel": {"pixelsWide": w, "pixelsHigh": h, "scale": 2}})

    errors, counts = [], {"video": 0, "idr": 0, "bytes": 0}
    latency, health = [], {}
    ctl_types = {}
    kf_sent_at_frame = None
    t0 = last_ping = time.monotonic()
    last_rx = t0
    buf = b""
    c.settimeout(0.2)
    while time.monotonic() - t0 < a.seconds:
        now = time.monotonic()
        if now - last_ping >= 2:
            tx({"type": "ping", "t": int(time.time() * 1000)})
            last_ping = now
        if kf_sent_at_frame is None and now - t0 >= a.kf_at:
            tx({"type": "kf"})
            kf_sent_at_frame = counts["video"]
        if now - last_rx > 5:
            errors.append("sender silent for more than 5 s")
            break
        try:
            data = c.recv(1 << 20)
        except socket.timeout:
            continue
        if not data:
            print("sender closed the connection")
            break
        last_rx = time.monotonic()
        buf += data
        while len(buf) >= 4:
            n = struct.unpack(">I", buf[:4])[0]
            if len(buf) < 4 + n:
                break
            p, buf = buf[4:4 + n], buf[4 + n:]
            if n < 32768 and p[:1] == b"{" and b"\x00" not in p:
                msg = json.loads(p)
                t = msg.get("type")
                ctl_types[t] = ctl_types.get(t, 0) + 1
                if t == "ping":
                    health = {k: v for k, v in msg.items() if k != "type"}
                if t == "welcome" and counts["video"]:
                    errors.append("welcome arrived after video started")
                continue
            counts["video"] += 1
            counts["bytes"] += n
            k = counts["video"]
            if p[:1] == b"{":                 # telemetry prefix: JSON up to the first start code
                cut = p.find(S4)
                try:
                    tel = json.loads(p[:cut])
                    latency.append(tel["snd"] - tel["cap"])
                except (ValueError, KeyError, TypeError):
                    errors.append(f"frame {k}: malformed telemetry prefix {p[:cut][:60]!r}")
                p = p[cut:]
            types = nal_types(p)
            if not p.startswith(S4):
                errors.append(f"frame {k}: does not start with a 4-byte start code")
            if has_three_byte_code(p):
                errors.append(f"frame {k}: 3-byte start code")
            if 9 in types:
                errors.append(f"frame {k}: AUD present")
            if 5 in types:
                counts["idr"] += 1
                first = types.index(5)
                if not {7, 8} <= set(types[:first]):
                    errors.append(f"frame {k}: IDR without SPS+PPS before it")
            if k == 1 and 5 not in types:
                errors.append("first frame is not an IDR")
            # The frame right after kf may already have been encoded; allow 3 frames.
            if kf_sent_at_frame is not None and kf_sent_at_frame < k <= kf_sent_at_frame + 3 and 5 in types:
                kf_sent_at_frame = -1   # satisfied
    c.close()

    secs = time.monotonic() - t0
    if "welcome" not in ctl_types:
        errors.append("no welcome")
    if ctl_types.get("ping", 0) < max(1, int(secs // 2) - 1):
        errors.append(f"too few pings ({ctl_types.get('ping', 0)} in {secs:.0f} s)")
    if kf_sent_at_frame not in (None, -1):
        errors.append("kf was not answered with an IDR within 3 frames")
    print(f"{counts['video']} AUs ({counts['video'] / secs:.1f} fps), {counts['idr']} IDRs, "
          f"{counts['bytes'] * 8 / secs / 1e6:.2f} Mb/s, control: {ctl_types}")
    if latency:
        latency.sort()
        print(f"capture→send latency ms: median {latency[len(latency) // 2]}, max {latency[-1]}; "
              f"last sender health: {health}")
    for e in errors[:20]:
        print("FAIL:", e)
    print("PASS" if not errors else f"{len(errors)} violation(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
