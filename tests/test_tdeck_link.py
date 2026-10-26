"""T-Decks over WiFi (tdecklink.py): the LoRa frames posted over HTTPS to a gateway with no radio."""
import threading
import time

import pytest

from arcade.lora import frames as F
from arcade.lora.device import DeviceClient
from arcade.lora.gateway import Gateway
from arcade.web import door, tdecklink as L

from test_lora_gateway import ACCT, FakeArcade


class State:
    home = "/nonexistent"


def wifi():
    link = L.WifiLink(State())
    link.factory = lambda: Gateway("http://127.0.0.1:8420", link._send, session_factory=FakeArcade, flush_delay=0)
    return link


def device(link, token):
    """A DeviceClient whose 'radio' is one post per frame, as the T-Deck's link task does."""
    box = {}

    def send(frame):
        for answer in link.post(token, [frame], wait=0):
            box["dev"].receive(answer)
    dev = DeviceClient(ACCT, send, timeout=2)
    box["dev"] = dev
    return dev


def test_pack_and_unpack_round_trip_and_refuse_garble():
    token = bytes(range(16))
    frames = [b"\xa7\x02abc", b"\xa7\x10" + b"x" * 200]
    got_token, got = L.unpack(token + L.pack(frames))
    assert got_token == token and got == frames
    for bad in (b"short", token + b"\x00", token + b"\x00\x00", token + b"\x00\x05ab", token + b"\x01\x01" + b"x" * 257):
        with pytest.raises(ValueError):
            L.unpack(bad)


def test_sign_in_and_balances_over_wifi():
    link = wifi()
    dev = device(link, b"t" * 16)
    assert dev.login() == "tester"
    assert dev.balances() == {"test": (7000, 9), "main": (1, 0)}
    assert L.node_of(b"t" * 16) >= 0xE0000000


def test_one_tokens_answers_never_reach_another_token():
    link = wifi()
    device(link, b"a" * 16).login()
    # a stranger asking with another token gets nothing of that T-Deck's
    assert link.post(b"b" * 16, [], wait=0) == []
    assert L.node_of(b"a" * 16) != L.node_of(b"b" * 16)


def test_an_empty_post_waits_for_what_comes_and_wakes_at_once():
    link = wifi()
    token = b"w" * 16
    link.post(token, [], wait=0)                     # the gateway exists now
    node = L.node_of(token)
    got = {}

    def poll():
        t = time.monotonic()
        got["frames"] = link.post(token, [], wait=3)
        got["took"] = time.monotonic() - t
    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.3)
    link._send(node, b"\xa7\x30letter")              # a letter the gateway hands over
    th.join(5)
    assert got["frames"] == [b"\xa7\x30letter"]
    assert got["took"] < 2


def test_the_route_is_open_to_t_decks():
    assert door.public_path("/r/tdeck/link", "POST")
