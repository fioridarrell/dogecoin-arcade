"""The shared walk's reading side: one arcade's part of a walk, from its node's block files or over RPC
(2026-10-25: "Fast full walks"; plan in utxo-plan.md).

A walk job is "the outputs paying these addresses in blocks `top` down to `bottom`". An arcade reads its jobs from its
node's `blk*.dat` files when it can (blkscan: disk speed, every core), and over RPC when it cannot (a node whose files
it may not read, a borrowed Core, a chain the file reader does not know). Jobs from other arcades run one at a time,
in the background, and are fetched by id when done (`Jobs`).
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from . import blkscan

#: Addresses one job may ask about (a walk with more is cut into several).
ADDRESSES_MAX = 20_000
#: Finished jobs kept for their asker this long.
KEEP_SECONDS = 3600.0
#: Jobs waiting at once (others are refused: the asker deals them elsewhere).
QUEUE_MAX = 8


def files_ok(params) -> bool:
    """The file reader knows this chain's blocks: no timestamped transactions (proof of stake) and no MWEB."""
    return not getattr(params, "tx_time", False) and not getattr(params, "mweb", False)


def blocks_dir(chain) -> Path | None:
    """Where `chain`'s node keeps its block files, when the node is this machine's own."""
    if getattr(chain, "borrowed", False) or getattr(chain, "datadir", None) is None:
        return None
    sub = chain.params.datadir_subdir
    base = Path(chain.datadir)
    return (base / sub / "blocks") if sub else (base / "blocks")


class Reader:
    """One arcade's reading of walk jobs for its chains."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self._files: dict[str, blkscan.BlockFiles] = {}
        self._lock = threading.Lock()

    def _block_files(self, chain) -> blkscan.BlockFiles | None:
        if not files_ok(chain.params):
            return None
        d = blocks_dir(chain)
        if d is None or not d.is_dir():
            return None
        bf = self._files.get(chain.network)
        if bf is None:
            bf = blkscan.BlockFiles(str(d), str(self.home / f"blkindex-{chain.network}.sqlite"),
                                    auxpow=not getattr(chain.params, "block_sig", False))
            if not bf.readable():
                return None
            self._files[chain.network] = bf
        return bf

    def can(self, chain) -> dict[str, Any]:
        """What this arcade can read for `chain`: {"files": bool, "rpc": bool}."""
        try:
            files = self._block_files(chain) is not None
        except Exception:                                   # noqa: BLE001
            files = False
        return {"files": files, "rpc": True}

    def read(self, chain, top: int, bottom: int, addresses: Iterable[str],
             progress: dict[str, Any] | None = None) -> list[list]:
        """[[txid, vout, sats, height, address]] paying `addresses` in `top`..`bottom`."""
        addresses = list(dict.fromkeys(addresses))[:ADDRESSES_MAX]
        progress = progress if progress is not None else {}
        bf = None
        try:
            bf = self._block_files(chain)
        except Exception:                                   # noqa: BLE001 -- unreadable: RPC
            bf = None
        if bf is not None:
            with self._lock:                                # one refresh at a time; it is incremental
                bf.refresh()
                with chain.rpc() as rpc:
                    tip = int(rpc.get_block_count())
                    bf.chain(rpc.get_block_hash(tip), tip)
            progress["how"] = "files"
            return blkscan.scan_parallel(bf, str(self.home / f"blkindex-{chain.network}.sqlite"), top, bottom,
                                         addresses, chain.params)
        progress["how"] = "rpc"
        return self._read_rpc(chain, top, bottom, addresses, progress)

    def _read_rpc(self, chain, top: int, bottom: int, addresses: list[str], progress: dict[str, Any]) -> list[list]:
        want = blkscan.targets(addresses, chain.params)
        out: list[list] = []
        if not want:
            return out
        auxpow = not getattr(chain.params, "block_sig", False)
        plain = files_ok(chain.params)
        with chain.rpc() as rpc:
            for h in range(int(top), int(bottom) - 1, -1):
                block_hash = rpc.get_block_hash(h)
                if plain:
                    raw = bytes.fromhex(rpc.call("getblock", block_hash, False))
                    out.extend(blkscan.block_matches(raw, want, h, auxpow=auxpow))
                else:                                       # a chain the lean reader does not know: Core decodes
                    block = rpc.get_block(block_hash, 2)
                    for tx in block.get("tx") or []:
                        for o in tx.get("vout") or []:
                            spk = o.get("scriptPubKey") or {}
                            k = blkscan._kind(bytes.fromhex(spk.get("hex", "")))
                            sats = int(round(float(o.get("value", 0)) * 100_000_000))
                            if k in want and sats > 0:
                                out.append([tx["txid"], int(o.get("n", 0)), sats, h, want[k]])
                progress["height"] = h
        return out


class Jobs:
    """Walk jobs other arcades hand this one: run one at a time in the background, fetched by id."""

    def __init__(self, reader: Reader):
        self.reader = reader
        self.jobs: dict[str, dict[str, Any]] = {}
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="walk jobs").start()

    def submit(self, chain, top: int, bottom: int, addresses: list[str], asker: str = "") -> dict[str, Any]:
        with self._lock:
            self._forget_old()
            if len(self._queue) >= QUEUE_MAX:
                return {"refused": "busy: too many walk jobs waiting here"}
            jid = uuid.uuid4().hex
            self.jobs[jid] = {"id": jid, "chain": chain, "top": int(top), "bottom": int(bottom),
                              "addresses": list(addresses)[:ADDRESSES_MAX], "asker": asker, "state": "waiting",
                              "progress": {}, "at": time.time()}
            self._queue.append(jid)
        self._wake.set()
        return {"id": jid, "state": "waiting"}

    def get(self, jid: str) -> dict[str, Any] | None:
        j = self.jobs.get(jid)
        if j is None:
            return None
        out = {k: j[k] for k in ("id", "state", "top", "bottom") if k in j}
        out["progress"] = dict(j.get("progress") or {})
        if j["state"] == "done":
            out["matches"] = j.get("matches", [])
        if j["state"] == "failed":
            out["error"] = j.get("error", "")
        return out

    def _forget_old(self) -> None:
        now = time.time()
        for jid in [k for k, j in self.jobs.items() if j["state"] in ("done", "failed")
                    and now - j.get("ended", now) > KEEP_SECONDS]:
            self.jobs.pop(jid, None)

    def _run(self) -> None:
        while True:
            self._wake.wait(30)
            self._wake.clear()
            while True:
                with self._lock:
                    jid = self._queue.pop(0) if self._queue else None
                if jid is None:
                    break
                j = self.jobs.get(jid)
                if j is None:
                    continue
                j["state"] = "running"
                try:
                    j["matches"] = self.reader.read(j["chain"], j["top"], j["bottom"], j["addresses"], j["progress"])
                    j["state"] = "done"
                except Exception as exc:                    # noqa: BLE001 -- said to the asker, who deals it elsewhere
                    j["state"], j["error"] = "failed", str(exc)[:300]
                j["ended"] = time.time()
                j.pop("chain", None)
