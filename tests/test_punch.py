"""The punch probe (arcade/mesh/punch.py, 2026-10-30): two linked arcades map one UDP socket each through two STUN
servers, swap addresses over the link, and send to each other at the same time; each side says what got through."""

import asyncio
import ipaddress
import struct

from arcade.mesh import punch, stun


class _Link:
    """A mesh link that hands frames straight to the other side's extension."""

    def __init__(self, peer_id):
        self.peer_id, self.other = peer_id, None

    async def send(self, header, blob=b""):
        await self.other.on_frame(self.back, header, blob)


class _Node:
    def __init__(self):
        self.links = {}


def _pair(duration=1.0, servers=()):
    a = punch.PunchShare(lambda: list(servers), duration=duration, public_stun=())
    b = punch.PunchShare(lambda: list(servers), duration=duration, public_stun=())
    a.node, b.node = _Node(), _Node()
    to_b, to_a = _Link("bb" * 32), _Link("aa" * 32)
    to_b.other, to_b.back = b, to_a
    to_a.other, to_a.back = a, to_b
    a.node.links[to_b.peer_id] = to_b
    b.node.links[to_a.peer_id] = to_a
    return a, b


async def _stun_servers():
    loop = asyncio.get_running_loop()
    out = []
    for host in ("127.0.0.1", "127.0.0.2"):
        t, _ = await loop.create_datagram_endpoint(lambda: stun._Server({}), local_addr=(host, 0))
        out.append(t)
    return out, [t.get_extra_info("sockname")[:2] for t in out]


def test_two_nodes_punch_through_and_both_say_so():
    async def run():
        transports, servers = await _stun_servers()
        try:
            a, b = _pair(servers=servers)
            rec = await a.run("bb" * 32)
            return rec, b.results
        finally:
            for t in transports:
                t.close()
    rec, theirs = asyncio.run(run())
    assert rec["ok"] and rec["direct"], rec
    assert len(rec["mine"]["maps"]) == 2 and all(m["host"] for m in rec["mine"]["maps"]), "two STUN answers"
    assert rec["mine"]["heard"] and rec["theirs"]["heard"], "each got an answer to its own packets"
    assert rec["theirs"]["got_from"], "the other side's packets count is passed back"
    assert theirs and theirs[-1]["role"] == "answered" and theirs[-1]["direct"]


def test_the_nat_kind_comes_from_the_two_stun_answers():
    same = [{"server": "1.1.1.1:3478", "host": "129.222.44.64", "port": 40000},
            {"server": "2.2.2.2:3478", "host": "129.222.44.64", "port": 40000}]
    assert punch.nat_kind(same, "192.168.1.20") == "same-port"
    moved = [dict(same[0]), dict(same[1], port=40001)]
    assert punch.nat_kind(moved, "192.168.1.20") == "new-port"
    assert punch.nat_kind(same[:1], "192.168.1.20") == "unknown"
    assert punch.nat_kind([dict(m, host="156.67.28.112") for m in same], "156.67.28.112") == "open"


def test_a_node_answers_one_probe_at_a_time_and_says_no_to_quick_repeats():
    async def run():
        a, b = _pair(duration=0.3)
        first = await a.run("bb" * 32)
        again = await a.run("bb" * 32)
        return first, again
    first, again = asyncio.run(run())
    assert first["ok"], first
    assert not again["ok"] and "busy" in again["detail"], again


def test_only_ipv4_addresses_and_only_a_few_are_aimed_at():
    raw = [["10.0.0.1", 5], ["::1", 5], ["10.0.0.2", 0], ["10.0.0.3", "x"], ["host.example", 5],
           ["10.0.0.4", 6], ["10.0.0.5", 7], ["10.0.0.6", 8], ["10.0.0.7", 9]]
    got = punch._addrs(raw)
    assert got == [("10.0.0.1", 5)], "only the first MAX_ADDRS are even looked at, junk dropped"
    assert len(punch._addrs([["10.0.0.%d" % i, 9] for i in range(1, 10)])) == punch.MAX_ADDRS


def test_not_linked_says_so():
    a, _ = _pair()
    rec = asyncio.run(a.run("cc" * 32))
    assert not rec["ok"] and "not linked" in rec["detail"]


def test_an_old_arcades_ipv6_mapped_answer_is_the_same_ipv4_address():
    """Arcades before the 2026-10-29 STUN fix answer ::ffff:81e0:d389 for 129.224.211.137 (seen in the first live
    probe, 2026-10-30: Starlink was called "new-port" because of it)."""
    class _Old(stun._Server):
        def datagram_received(self, data, addr):
            packet = stun.answer(data, addr)
            tid = data[8:20]
            value = struct.pack("!BBH", 0, 2, addr[1] ^ (stun.MAGIC >> 16)) + (
                int(ipaddress.ip_address("::ffff:" + addr[0]))
                ^ int.from_bytes(struct.pack("!I", stun.MAGIC) + tid, "big")).to_bytes(16, "big")
            attrs = struct.pack("!HH", stun.XOR_MAPPED_ADDRESS, len(value)) + value
            self.transport.sendto(struct.pack("!HHI", stun.BINDING_SUCCESS, len(attrs), stun.MAGIC) + tid + attrs,
                                  addr)

    async def run():
        loop = asyncio.get_running_loop()
        new, _ = await loop.create_datagram_endpoint(lambda: stun._Server({}), local_addr=("127.0.0.1", 0))
        old, _ = await loop.create_datagram_endpoint(lambda: _Old({}), local_addr=("127.0.0.2", 0))
        s = punch.Session("x" * 12, "a")
        await s.open()
        try:
            await s.map([new.get_extra_info("sockname")[:2], old.get_extra_info("sockname")[:2]])
            return s.maps
        finally:
            s.close()
            new.close()
            old.close()
    maps = asyncio.run(run())
    assert [m["host"] for m in maps] == ["127.0.0.1", "127.0.0.1"], maps
    assert punch.nat_kind(maps, "10.9.9.9") == "same-port"
