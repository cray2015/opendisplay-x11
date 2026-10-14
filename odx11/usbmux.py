# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal usbmuxd client over /var/run/usbmuxd (PROJECT_SPEC.md §7.1).

Each request: 16-byte little-endian header + XML plist. After a successful
`Connect` the socket is a raw byte pipe to the device port.
"""
import plistlib
import socket
import struct

HEADER = struct.Struct("<IIII")       # length incl. header, version, message, tag
VERSION_PLIST = 1
MESSAGE_PLIST = 8
PROG = "odx11"

RESULT_OK = 0
RESULT_BADDEV = 2
RESULT_REFUSED = 3


class MuxError(Exception):
    def __init__(self, result):
        super().__init__({RESULT_BADDEV: "bad device", RESULT_REFUSED: "connection refused"}
                         .get(result, f"usbmuxd result {result}"))
        self.result = result


def encode(msg: dict, tag: int = 1) -> bytes:
    body = plistlib.dumps({**msg, "ProgName": PROG, "ClientVersionString": PROG})
    return HEADER.pack(HEADER.size + len(body), VERSION_PLIST, MESSAGE_PLIST, tag) + body


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("usbmuxd closed the connection")
        buf += chunk
    return bytes(buf)


def read_message(sock) -> dict:
    length = HEADER.unpack(_recv_exact(sock, HEADER.size))[0]
    return plistlib.loads(_recv_exact(sock, length - HEADER.size))


def _open(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    return s


def list_devices(socket_path: str) -> list[dict]:
    """USB-attached devices only; network-paired entries are ignored."""
    with _open(socket_path) as s:
        s.sendall(encode({"MessageType": "ListDevices"}))
        devs = read_message(s).get("DeviceList", [])
    return [d for d in devs if d.get("Properties", {}).get("ConnectionType") == "USB"]


def connect(socket_path: str, device_id: int, port: int) -> socket.socket:
    """A byte pipe to `port` on the device. PortNumber goes in network byte order."""
    s = _open(socket_path)
    try:
        s.sendall(encode({"MessageType": "Connect", "DeviceID": device_id,
                          "PortNumber": socket.htons(port)}))
        result = read_message(s).get("Number")
        if result != RESULT_OK:
            raise MuxError(result)
    except BaseException:
        s.close()
        raise
    return s


def listen(socket_path: str) -> socket.socket:
    """A socket subscribed to Attached/Detached events, positioned after the OK result."""
    s = _open(socket_path)
    try:
        s.sendall(encode({"MessageType": "Listen"}))
        result = read_message(s).get("Number")
        if result != RESULT_OK:
            raise MuxError(result)
    except BaseException:
        s.close()
        raise
    return s


class MessageReader:
    """Incrementally parses usbmuxd plist messages from a Listen socket's byte stream."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data
        out = []
        while len(self._buf) >= HEADER.size:
            length = HEADER.unpack_from(self._buf)[0]
            if length < HEADER.size:
                raise ValueError(f"bad usbmuxd message length {length}")
            if len(self._buf) < length:
                break
            out.append(plistlib.loads(bytes(self._buf[HEADER.size:length])))
            del self._buf[:length]
        return out


def is_usb(msg: dict) -> bool:
    return msg.get("Properties", {}).get("ConnectionType") == "USB"
