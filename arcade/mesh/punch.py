"""The punch probe (2026-10-30): can two linked arcades behind home NAT talk to each other directly over UDP?

2026-10-29: "nodes in the mesh that are able to be servers become servers and nodes that cannot be servers
connect to those servers" -- and, between two that cannot, punch through. Starlink's NAT keeps one outside port per
inside socket whatever the destination (endpoint-independent mapping) and lets in only what answers a packet it sent
(address-and-port filtering), so two such NATs can be crossed when both send to each other at the same time. This
probe finds out, between real arcades, before anything is built on it.

How: the two arcades are already linked (the link carries the arrangement; no third server is needed). Each opens
one UDP socket, asks two STUN servers through that same socket what address the internet sees (mesh nodes' STUN
first, stun.py; public ones only when the mesh knows fewer than two), and the two compare the ports: the same port
from both means the NAT keeps one port per socket (punchable), different ports mean a new port per destination
(hard). Then each sends small "APUNCH" packets to the other's outside (and home-network) addresses for a few seconds,
answering each one it gets. A side that got an answer knows its packets went all the way.

Frames "pu": {"op": "try", "n", "a": [[host, port]...], "m": maps} asks a linked node to punch with us;
{"op": "ready", "n", "a", "m"} is its yes (both start sending then); {"op": "no", "n", "why"} its no;
{"op": "result", "n", "got": [...], "heard": bool} what it saw. A node answers one probe at a time, and the same
node at most once per COOLDOWN, and sends at most MAX_ADDRS * DURATION / EVERY tiny packets, so a probe cannot be
used to send anybody much of anything.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import time

from . import stun

log = logging.getLogger("arcade.mesh.punch")

MAGIC = b"APUNCH1"
DURATION = 8.0              # seconds each side sends
EVERY = 0.2                 # seconds between packets to one address
BUFFER = 4 * 1024 * 1024    # bytes a UDP socket may hold for us
MAX_ADDRS = 4               # addresses one side may give
COOLDOWN = 20.0             # seconds between probes answered for one node
KEEP = 20                   # results kept for Admin
PUBLIC_STUN = (("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478))


def _v4(host: str) -> bool:
    try:
        return ipaddress.ip_address(str(host)).version == 4
    except ValueError:
        return False


def _addrs(raw) -> list[tuple[str, int]]:
    out = []
    for a in raw[:MAX_ADDRS] if isinstance(raw, list) else []:
        if isinstance(a, list) and len(a) == 2 and isinstance(a[1], int) and 0 < a[1] < 65536 and _v4(a[0]):
            out.append((str(a[0]), int(a[1])))
    return out


def udp_socket() -> socket.socket:
    """An IPv4 UDP socket on any port. On Windows a send to a closed port makes the NEXT receive fail with
    WSAECONNRESET, and punching sends to addresses that may not answer; SIO_UDP_CONNRESET turns that off."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    no_reset(sock)
    for option in (socket.SO_RCVBUF, socket.SO_SNDBUF):  # the default drops a window's worth arriving at once
        try:
            sock.setsockopt(socket.SOL_SOCKET, option, BUFFER)
        except OSError:
            pass
    sock.setblocking(False)
    sock.bind(("0.0.0.0", 0))
    return sock


def no_reset(sock: socket.socket) -> None:
    code = getattr(socket, "SIO_UDP_CONNRESET", None)   # Windows only
    if code is not None:
        sock.ioctl(code, False)


def local_ipv4() -> str | None:
    """This machine's address on its home network (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def nat_kind(maps: list[dict], local: str | None) -> str:
    """'open' (no NAT), 'same-port' (one outside port per socket: punchable), 'new-port' (a new outside port per
    destination: hard), or 'unknown' (fewer than two STUN answers)."""
    seen = [(m["host"], m["port"]) for m in maps if m.get("host")]
    if seen and local and all(h == local for h, _ in seen):
        return "open"
    if len(seen) < 2:
        return "unknown"
    return "same-port" if len(set(seen)) == 1 else "new-port"


class _Proto(asyncio.DatagramProtocol):
    """One socket for both STUN and the punch: the NAT's port for it is what the other side must aim at."""

    def __init__(self):
        self.stun_wait: dict[bytes, asyncio.Future] = {}
        self.on_punch = None
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.startswith(MAGIC):
            if self.on_punch is not None:
                self.on_punch(data, addr)
            return
        if len(data) >= 20:
            fut = self.stun_wait.get(data[8:20])
            if fut is not None and not fut.done():
                found = stun.mapped_address(data, data[8:20])
                if found:
                    fut.set_result(found)

    def error_received(self, exc):
        pass


class Session:
    """One side of one probe: a socket, what STUN said about it, and what went through."""

    def __init__(self, nonce: str, side: str):
        self.nonce, self.side = nonce, side
        self.proto: _Proto | None = None
        self.local_port = 0
        self.maps: list[dict] = []
        self.got: dict[str, int] = {}          # "host:port" -> packets from the other side
        self.heard = False                     # an answer to one of ours came back
        self.first_at: float | None = None

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        transport, self.proto = await loop.create_datagram_endpoint(_Proto, sock=udp_socket())
        self.local_port = transport.get_extra_info("sockname")[1]
        self.proto.on_punch = self._on_punch

    def close(self) -> None:
        if self.proto is not None and self.proto.transport is not None:
            self.proto.transport.close()

    async def _ask(self, host: str, port: int, timeout: float = 1.5) -> tuple[str, int] | None:
        loop = asyncio.get_running_loop()
        packet, tid = stun.binding_request()
        fut = loop.create_future()
        self.proto.stun_wait[tid] = fut
        try:
            for _ in range(3):
                self.proto.transport.sendto(packet, (host, port))
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout / 3)
                except asyncio.TimeoutError:
                    continue
            return None
        finally:
            self.proto.stun_wait.pop(tid, None)

    async def map(self, servers: list[tuple[str, int]]) -> None:
        """Ask up to two STUN servers (different machines) what this socket looks like from outside."""
        tried = 0
        for host, port in servers:
            if tried == 2:
                break
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(host, port, family=socket.AF_INET,
                                                                     type=socket.SOCK_DGRAM)
                ip = infos[0][4][0]
            except (OSError, IndexError):
                continue
            if any(m["server"].startswith(ip + ":") for m in self.maps):
                continue
            tried += 1
            said = await self._ask(ip, port)
            if said and said[0].lower().startswith("::ffff:"):
                # Arcades before the 2026-10-29 STUN fix answer an IPv4 asker as ::ffff:a.b.c.d; it is the same
                # address, and comparing it as another one would call every NAT "new-port".
                said = (str(ipaddress.ip_address(said[0]).ipv4_mapped), said[1])
            self.maps.append({"server": f"{ip}:{port}", "host": said[0] if said else None,
                              "port": said[1] if said else None})

    def offer(self) -> list[list]:
        """Where the other side should aim: the outside address(es) STUN saw, then the home-network one (two
        arcades in one house reach each other there)."""
        out = []
        for m in self.maps:
            if m.get("host") and _v4(m["host"]) and [m["host"], m["port"]] not in out:
                out.append([m["host"], m["port"]])
        lan = local_ipv4()
        if lan and [lan, self.local_port] not in out:
            out.append([lan, self.local_port])
        return out[:MAX_ADDRS]

    def _packet(self, kind: bytes, seq: int) -> bytes:
        return MAGIC + self.nonce.encode()[:12].ljust(12, b"-") + self.side.encode()[:1] + kind + str(seq).encode()

    def _on_punch(self, data: bytes, addr) -> None:
        body = data[len(MAGIC):]
        if body[:12] != self.nonce.encode()[:12].ljust(12, b"-") or body[12:13] == self.side.encode()[:1]:
            return
        where = f"{addr[0]}:{addr[1]}"
        self.got[where] = self.got.get(where, 0) + 1
        if self.first_at is None:
            self.first_at = time.time()
        if body[13:14] == b"p":
            self.proto.transport.sendto(self._packet(b"a", 0), addr)
        elif body[13:14] == b"a":
            self.heard = True

    async def punch(self, targets: list[tuple[str, int]], duration: float = DURATION) -> None:
        end = time.monotonic() + duration
        seq = 0
        while time.monotonic() < end:
            for t in targets:
                try:
                    self.proto.transport.sendto(self._packet(b"p", seq), t)
                except OSError:
                    pass
            seq += 1
            await asyncio.sleep(EVERY)

    def report(self) -> dict:
        return {"local_port": self.local_port, "lan": local_ipv4(), "maps": self.maps,
                "nat": nat_kind(self.maps, local_ipv4()), "got_from": dict(self.got), "heard": self.heard}


class PunchShare:
    """A mesh extension (node.add_extension). `stun_servers()` lists STUN to map through, mesh ones first."""

    kinds = ("pu",)

    def __init__(self, stun_servers=None, duration: float = DURATION, public_stun=PUBLIC_STUN):
        self.stun_servers = stun_servers or (lambda: [])
        self.duration, self.public_stun = duration, tuple(public_stun)
        self.node = None
        self.busy = False
        self.last_for: dict[str, float] = {}
        self.waiting: dict[str, asyncio.Future] = {}
        self.results: list[dict] = []

    def _servers(self) -> list[tuple[str, int]]:
        try:
            mesh = [(h, p) for h, p in (self.stun_servers() or []) if _v4(h)]
        except Exception:                                    # noqa: BLE001
            mesh = []
        return mesh + [s for s in self.public_stun if s not in mesh]

    def _keep(self, rec: dict) -> dict:
        self.results = (self.results + [rec])[-KEEP:]
        return rec

    async def link_up(self, link) -> None:
        pass

    async def link_down(self, peer_id: str) -> None:
        pass

    async def rebalance(self) -> None:
        pass

    async def run(self, peer_id: str) -> dict:
        """Punch with the linked node `peer_id`; both sides' reports."""
        link = self.node.links.get(peer_id) if self.node is not None else None
        rec = {"at": int(time.time()), "peer": peer_id, "role": "asked"}
        if link is None:
            return self._keep({**rec, "ok": False, "detail": "not linked to that node"})
        if self.busy:
            return {**rec, "ok": False, "detail": "a probe is already running"}
        self.busy = True
        nonce = os.urandom(6).hex()
        me = Session(nonce, "a")
        fut = asyncio.get_running_loop().create_future()
        self.waiting[nonce] = fut
        try:
            await me.open()
            await me.map(self._servers())
            await link.send({"t": "pu", "op": "try", "n": nonce, "a": me.offer(), "m": me.maps})
            try:
                ready = await asyncio.wait_for(fut, 15.0)
            except asyncio.TimeoutError:
                return self._keep({**rec, "ok": False, "mine": me.report(),
                                   "detail": "the other node did not answer (is it on this release?)"})
            if ready.get("op") == "no":
                return self._keep({**rec, "ok": False, "mine": me.report(), "detail": f"it said no: {ready.get('why')}"})
            fut2 = asyncio.get_running_loop().create_future()
            self.waiting[nonce] = fut2
            theirs_at = _addrs(ready.get("a"))
            await me.punch(theirs_at, self.duration)
            try:
                result = await asyncio.wait_for(fut2, 10.0)
            except asyncio.TimeoutError:
                result = {}
            theirs = {"maps": ready.get("m") if isinstance(ready.get("m"), list) else [],
                      "offered": [f"{h}:{p}" for h, p in theirs_at],
                      "got_from": result.get("got") if isinstance(result.get("got"), dict) else {},
                      "heard": bool(result.get("heard"))}
            theirs["nat"] = nat_kind([m for m in theirs["maps"] if isinstance(m, dict)], None)
            mine = me.report()
            direct = bool(mine["got_from"]) and bool(theirs["got_from"])
            rec.update(ok=True, direct=direct, one_way=bool(mine["got_from"]) != bool(theirs["got_from"]),
                       mine=mine, theirs=theirs)
            log.info("punch with %s: %s (mine %s, theirs %s)", peer_id[:8], "DIRECT" if direct else "no",
                     mine["nat"], theirs["nat"])
            return self._keep(rec)
        finally:
            self.waiting.pop(nonce, None)
            me.close()
            self.busy = False

    async def _answer(self, link, header: dict) -> None:
        nonce = str(header.get("n", ""))[:12]
        now = time.time()
        if self.busy or now - self.last_for.get(link.peer_id, 0) < COOLDOWN:
            await link.send({"t": "pu", "op": "no", "n": nonce, "why": "busy, ask again shortly"})
            return
        self.busy = True
        self.last_for[link.peer_id] = now
        me = Session(nonce, "b")
        try:
            await me.open()
            await me.map(self._servers())
            targets = _addrs(header.get("a"))
            await link.send({"t": "pu", "op": "ready", "n": nonce, "a": me.offer(), "m": me.maps})
            await me.punch(targets, self.duration)
            await asyncio.sleep(1.0)                         # late answers still count
            await link.send({"t": "pu", "op": "result", "n": nonce, "got": dict(list(me.got.items())[:8]),
                             "heard": me.heard})
            self._keep({"at": int(now), "peer": link.peer_id, "role": "answered", "ok": True,
                        "direct": bool(me.got), "mine": me.report()})
        finally:
            me.close()
            self.busy = False

    async def on_frame(self, link, header: dict, blob: bytes) -> None:
        op = header.get("op")
        if op == "try":
            asyncio.get_running_loop().create_task(self._answer(link, header))
        elif op in ("ready", "no", "result"):
            fut = self.waiting.get(str(header.get("n", ""))[:12])
            if fut is not None and not fut.done():
                fut.set_result(header)
