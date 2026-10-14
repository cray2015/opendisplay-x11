#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
"""M0 spike — AC 2/3/9 without iproxy. Throwaway; not product code.

Dials the OpenDisplay receiver on device port 9000 through usbmuxd, prints
`hello`, answers `welcome`/`pong`, pings every 2 s, and with --pattern streams
a VA-API test pattern so the iPad should show moving colour bars. With
--capture OUTPUT it streams that xrandr output's region of the X screen instead
(view only — touch is not injected).

  python3 spike/hello_probe.py [--seconds 20] [--pattern]
  python3 spike/hello_probe.py --capture HDMI-1 --seconds 120
  python3 spike/hello_probe.py --tcp 127.0.0.1:9000     # against a fake receiver
"""
import argparse, json, os, plistlib, re, select, socket, struct, subprocess, threading, time

USBMUXD = "/var/run/usbmuxd"


def mux_request(sock, msg, tag=1):
    body = plistlib.dumps(msg)
    sock.sendall(struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body)
    hdr = recv_exact(sock, 16)
    length = struct.unpack("<I", hdr[:4])[0]
    return plistlib.loads(recv_exact(sock, length - 16))


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("peer closed")
        buf += chunk
    return buf


def dial_usb(port):
    s = socket.socket(socket.AF_UNIX)
    s.connect(USBMUXD)
    devs = mux_request(s, {"MessageType": "ListDevices", "ProgName": "odx11-spike",
                           "ClientVersionString": "odx11-spike"})["DeviceList"]
    s.close()
    usb = [d for d in devs if d["Properties"].get("ConnectionType") == "USB"]
    if not usb:
        raise SystemExit("no USB device attached (is the iPad plugged in and trusted?)")
    dev = usb[0]
    print("device", dev["DeviceID"], dev["Properties"].get("SerialNumber"))
    s = socket.socket(socket.AF_UNIX)
    s.connect(USBMUXD)
    # PortNumber is network byte order: htons(9000) == 10275.
    res = mux_request(s, {"MessageType": "Connect", "DeviceID": dev["DeviceID"],
                          "PortNumber": socket.htons(port), "ProgName": "odx11-spike",
                          "ClientVersionString": "odx11-spike"})
    print("Connect result", res.get("Number"))
    if res.get("Number") != 0:
        raise SystemExit("refused: 3 = app not listening (open it, keep it foreground)")
    return s


lock = threading.Lock()


def send(sock, payload):
    with lock:
        sock.sendall(struct.pack(">I", len(payload)) + payload)


def send_json(sock, obj):
    send(sock, json.dumps(obj, separators=(",", ":")).encode())


def output_geometry(name):
    """(x, y, w, h) of an active xrandr output, e.g. 'HDMI-1 connected 1568x1088+1920+0'."""
    xr = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, check=True).stdout
    m = re.search(rf"^{re.escape(name)} connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", xr, re.M)
    if not m:
        raise SystemExit(f"{name} is not connected and enabled — run spike/m0_connector.sh first")
    w, h, x, y = map(int, m.groups())
    return x, y, w, h


def stream_pattern(sock, kf_event, stop, capture=None):
    import gi
    gi.require_version("Gst", "1.0"); gi.require_version("GstVideo", "1.0")
    from gi.repository import Gst, GstVideo
    Gst.init(None)
    # The one encoder path M0 found that honours force-key-unit with a real IDR.
    if capture:
        x, y, w, h = capture
        src = (f"ximagesrc display-name={os.environ.get('DISPLAY', ':0')} startx={x} starty={y} "
               f"endx={x + w - 1} endy={y + h - 1} use-damage=false show-pointer=true "
               "! video/x-raw,framerate=30/1 ! queue leaky=downstream max-size-buffers=1 ")
    else:
        src = "videotestsrc is-live=true pattern=smpte ! timeoverlay ! video/x-raw,width=1360,height=1024,framerate=30/1 "
    p = Gst.parse_launch(
        src + "! videoconvert ! video/x-raw,format=NV12 "
        "! vaapih264enc tune=low-power rate-control=cqp max-bframes=0 keyframe-period=1000000 "
        "! h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au "
        "! appsink name=out sync=false")
    out = p.get_by_name("out")
    p.set_state(Gst.State.PLAYING)
    n = 0
    while not stop.is_set():
        if kf_event.is_set():
            kf_event.clear()
            out.send_event(GstVideo.video_event_new_upstream_force_key_unit(Gst.CLOCK_TIME_NONE, True, 0))
        sample = out.emit("try-pull-sample", Gst.SECOND)
        if sample is None:
            continue
        b = sample.get_buffer()
        au = b.extract_dup(0, b.get_size())
        if au[4:5] and au[4] & 0x1F == 9:  # leading AUD (h264parse adds it): drop to next start code
            au = au[au.index(b"\x00\x00\x00\x01", 4):]
        send(sock, au)
        n += 1
    p.set_state(Gst.State.NULL)
    print("sent", n, "access units")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tcp", help="host:port to dial directly instead of usbmuxd")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--pattern", action="store_true")
    ap.add_argument("--capture", metavar="OUTPUT", help="stream this xrandr output, e.g. HDMI-1")
    a = ap.parse_args()
    capture = output_geometry(a.capture) if a.capture else None
    if capture:
        print("capturing %s at %dx%d+%d+%d" % (a.capture, capture[2], capture[3], capture[0], capture[1]))
    if a.tcp:
        host, port = a.tcp.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)))
    else:
        sock = dial_usb(9000)
    if sock.family == socket.AF_INET:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    stop, kf = threading.Event(), threading.Event()
    buf, seen, deadline, next_ping, streamer = b"", set(), time.time() + a.seconds, 0, None
    try:
        while time.time() < deadline:
            if time.time() >= next_ping:
                send_json(sock, {"type": "ping"})
                next_ping = time.time() + 2
            if select.select([sock], [], [], 0.5)[0]:
                chunk = sock.recv(65536)
                if not chunk:
                    print("receiver closed"); break
                buf += chunk
            while len(buf) >= 4:
                n = struct.unpack(">I", buf[:4])[0]
                if len(buf) < 4 + n:
                    break
                msg, buf = json.loads(buf[4:4 + n]), buf[4 + n:]
                t = msg.get("type")
                if t == "hello":
                    print("HELLO", json.dumps(msg))
                    send_json(sock, {"type": "welcome", "pv": 2, "min": 1})
                    if (a.pattern or capture) and streamer is None:
                        streamer = threading.Thread(target=stream_pattern, args=(sock, kf, stop, capture))
                        streamer.start()
                elif t == "ping":
                    send_json(sock, {"type": "pong", "t": msg["t"], "mt": int(time.time() * 1000)})
                elif t == "kf":
                    print("kf"); kf.set()
                elif t not in seen:
                    seen.add(t); print("first", t, json.dumps(msg)[:200])
    finally:
        stop.set()
        if streamer:
            streamer.join()
        sock.close()


if __name__ == "__main__":
    main()
