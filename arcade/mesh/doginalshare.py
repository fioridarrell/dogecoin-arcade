"""The Doginals index's shared work over the mesh (arcade/doginal_index.py; 2026-10-20: "the mesh network
shared, indexing and verification").

On the wire (frames of kind "dx" on an ordinary link, never flooded), each one small:

    hi  {mode, walk, helped, v}          what this arcade does: walk (its own full index) or help (chunks)
    ls  {c, at, until}                   it holds chunk c's lease, has read up to block `at`, until `until`
    ld  {c}                              it let chunk c go (finished, or gave up)
    dn  {rows: [[c, hash, births]...]}   its hashes of finished chunks; every one of them when a link comes up

A frame names no other arcade: what a link carries is that arcade's own word (the link proves who it is), so nobody
can speak for another. Leases are a courtesy -- two helpers on one chunk cost a scan, never a wrong answer.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger("arcade.mesh")

ROWS_PER_FRAME = 500           # a "dn" frame stays well under the link's 64 KB frame


class DxShare:
    kinds = ("dx",)

    def __init__(self, index: Any):
        self.index = index
        self.node = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        index.share = self

    # ---------------------------------------------------------------- hooks
    async def link_up(self, link) -> None:
        self.loop = asyncio.get_running_loop()
        try:
            await link.send({"t": "dx", **await asyncio.to_thread(self.index.hello)})
            rows = await asyncio.to_thread(self.index.all_mine)
            for i in range(0, len(rows), ROWS_PER_FRAME):
                await link.send({"t": "dx", "op": "dn", "rows": rows[i:i + ROWS_PER_FRAME]})
            mine = self.index.chunks.mine
            if mine:
                import time
                from ..doginal_index import LEASE_SECONDS
                await link.send({"t": "dx", "op": "ls", "c": mine["c"], "at": mine["at"],
                                 "until": time.time() + LEASE_SECONDS})
        except Exception:                                      # noqa: BLE001 -- the link went
            log.debug("dx link_up", exc_info=True)

    async def link_down(self, peer_id: str) -> None:
        self.index.chunks.gone(peer_id)

    async def rebalance(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self._send_all({"t": "dx", **await asyncio.to_thread(self.index.hello)})

    async def on_frame(self, link, header: dict, blob: bytes) -> None:
        try:
            await asyncio.to_thread(self.index.heard, link.peer_id, header)
        except Exception:                                      # noqa: BLE001 -- not a word this version reads
            log.debug("dx frame", exc_info=True)

    # ---------------------------------------------------------------- saying
    def post(self, frame: dict) -> None:
        """From the index's thread: say this to every linked arcade."""
        loop = self.loop
        if loop is None or self.node is None:
            return
        rows = frame.get("rows")
        frames = ([{**frame, "rows": rows[i:i + ROWS_PER_FRAME]} for i in range(0, len(rows), ROWS_PER_FRAME)]
                  if isinstance(rows, list) else [frame])
        for f in frames:
            try:
                asyncio.run_coroutine_threadsafe(self._send_all({"t": "dx", **f}), loop)
            except RuntimeError:                               # the loop stopped
                return

    async def _send_all(self, frame: dict) -> None:
        for link in list((self.node.links if self.node else {}).values()):
            try:
                await link.send(frame)
            except Exception:                                  # noqa: BLE001
                pass
