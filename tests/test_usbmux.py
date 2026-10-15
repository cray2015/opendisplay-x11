# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""usbmuxd client against a fake usbmuxd on a real Unix socket."""
import plistlib
import socket
import threading

import pytest

from odx11 import usbmux


class FakeMux:
    """Accepts one client, records its request, answers with `reply` (sent in `chunks` pieces)."""

    def __init__(self, path, reply, chunks=1, close_early=False):
        self.path, self.reply, self.chunks, self.close_early = str(path), reply, chunks, close_early
        self.request, self.header = None, None
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.server.accept()
        with conn:
            head = conn.recv(16, socket.MSG_WAITALL)
            self.header = usbmux.HEADER.unpack(head)
            self.request = plistlib.loads(conn.recv(self.header[0] - 16, socket.MSG_WAITALL))
            data = usbmux.encode(self.reply, tag=self.header[3]) if self.reply is not None else b""
            if self.close_early:
                data = data[:10]
            step = max(1, -(-len(data) // self.chunks))
            for i in range(0, len(data), step):
                conn.sendall(data[i:i + step])
            if not self.close_early:
                conn.recv(1)                      # hold the line open until the client closes

    def done(self):
        self.thread.join(timeout=5)
        self.server.close()


def usb(dev_id, kind="USB"):
    return {"DeviceID": dev_id, "Properties": {"ConnectionType": kind, "SerialNumber": f"S{dev_id}"}}


def test_request_header_and_identity():
    data = usbmux.encode({"MessageType": "Listen"}, tag=7)
    length, version, message, tag = usbmux.HEADER.unpack(data[:16])
    assert (length, version, message, tag) == (len(data), 1, 8, 7)
    assert plistlib.loads(data[16:]) == {"MessageType": "Listen", "ProgName": "odx11", "ClientVersionString": "odx11"}


def test_list_devices_keeps_usb_only(tmp_path):
    mux = FakeMux(tmp_path / "mux", {"DeviceList": [usb(1), usb(2, "Network"), usb(3)]})
    devs = usbmux.list_devices(mux.path)
    mux.done()
    assert mux.request["MessageType"] == "ListDevices"
    assert [d["DeviceID"] for d in devs] == [1, 3]


def test_list_devices_with_no_list_is_empty(tmp_path):
    mux = FakeMux(tmp_path / "mux", {})
    assert usbmux.list_devices(mux.path) == []
    mux.done()


def test_connect_sends_the_port_in_network_byte_order(tmp_path):
    mux = FakeMux(tmp_path / "mux", {"MessageType": "Result", "Number": 0}, chunks=5)   # reply split up
    s = usbmux.connect(mux.path, 42, 9000)
    s.close()
    mux.done()
    assert mux.request["MessageType"] == "Connect"
    assert (mux.request["DeviceID"], mux.request["PortNumber"]) == (42, 10275)          # htons(9000)


@pytest.mark.parametrize("number,text", [(3, "connection refused"), (2, "bad device"), (5, "usbmuxd result 5")])
def test_connect_failure_names_the_reason_and_closes(tmp_path, monkeypatch, number, text):
    opened = []
    real_open = usbmux._open
    monkeypatch.setattr(usbmux, "_open", lambda p: opened.append(real_open(p)) or opened[-1])
    mux = FakeMux(tmp_path / "mux", {"MessageType": "Result", "Number": number})
    with pytest.raises(usbmux.MuxError) as e:
        usbmux.connect(mux.path, 42, 9000)
    mux.done()
    assert (str(e.value), e.value.result) == (text, number)
    assert opened[0].fileno() == -1                                  # the socket was closed


def test_listen_returns_the_subscribed_socket(tmp_path):
    mux = FakeMux(tmp_path / "mux", {"MessageType": "Result", "Number": 0})
    s = usbmux.listen(mux.path)
    s.close()
    mux.done()
    assert mux.request["MessageType"] == "Listen"


def test_listen_failure_raises_and_closes(tmp_path, monkeypatch):
    opened = []
    real_open = usbmux._open
    monkeypatch.setattr(usbmux, "_open", lambda p: opened.append(real_open(p)) or opened[-1])
    mux = FakeMux(tmp_path / "mux", {"MessageType": "Result", "Number": 3})
    with pytest.raises(usbmux.MuxError, match="connection refused"):
        usbmux.listen(mux.path)
    mux.done()
    assert opened[0].fileno() == -1


def test_usbmuxd_hanging_up_mid_reply_is_an_eof(tmp_path):
    mux = FakeMux(tmp_path / "mux", {"MessageType": "Result", "Number": 0}, close_early=True)
    with pytest.raises(EOFError, match="usbmuxd closed the connection"):
        usbmux.connect(mux.path, 42, 9000)
    mux.done()


def test_no_usbmuxd_running_is_an_os_error(tmp_path):
    with pytest.raises(OSError):
        usbmux.list_devices(str(tmp_path / "missing"))


def test_reader_waits_for_a_whole_message_and_keeps_the_rest():
    first = usbmux.encode({"MessageType": "Attached", "DeviceID": 1})
    second = usbmux.encode({"MessageType": "Detached", "DeviceID": 1})
    r = usbmux.MessageReader()
    assert r.feed(first[:20]) == []
    got = r.feed(first[20:] + second[:5])
    assert [m["MessageType"] for m in got] == ["Attached"]
    assert [m["MessageType"] for m in r.feed(second[5:])] == ["Detached"]


def test_reader_rejects_a_length_shorter_than_its_header():
    with pytest.raises(ValueError) as e:
        usbmux.MessageReader().feed(usbmux.HEADER.pack(15, 1, 8, 0))
    assert str(e.value) == "bad usbmuxd message length 15"
