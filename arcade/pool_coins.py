"""Coins on their way to an address: plain payments sitting in a chain's mempool (2026-10-17: "add incoming that
reads from the mempool to all of the chains" -- 0.764 DOGE sent to @apple showed nothing until its block).

The wallet already said "arriving" for what THIS node broadcast (the flight book: faucet gifts, the account's own change)
and for tokens and pieces moving on the tag chain (`pending_moves`). A payment from anybody else -- another wallet, an
exchange, a mint tool -- was invisible for a block. This reads each chain's mempool for outputs paying an address, one
`getrawmempool` a pass and each new transaction once, and forgets a transaction the moment it leaves the pool (mined: the
index files it; dropped: it never arrives).

What arrives this way is shown, never spent: an unconfirmed coin from a stranger can be replaced or dropped, so
`funding.choose` keeps using only the index and the node's own flights.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .script import OutputType, parse_output

#: New transactions read per pass at most; the rest are read next pass (a pool of thousands never stalls the loop).
READ_PER_PASS = 400
SPENDABLE = (OutputType.PUBKEYHASH, OutputType.SCRIPTHASH)


class PoolCoins:
    def __init__(self, network: str):
        self.network = network
        self._outs: dict[str, list[tuple[int, str, int]]] = {}     # txid -> [(vout, address, value)]
        self._lock = threading.Lock()
        self.read_at = 0.0

    def refresh(self, rpc: Any, params: Any) -> bool:
        """Read what is new in the pool and forget what left it. True when anything changed."""
        ids = set(rpc.call("getrawmempool") or [])
        with self._lock:
            gone = [t for t in self._outs if t not in ids]
            for t in gone:
                del self._outs[t]
            fresh = [t for t in ids if t not in self._outs][:READ_PER_PASS]
        found: dict[str, list[tuple[int, str, int]]] = {}
        for t in fresh:
            try:
                tx = rpc.call("getrawtransaction", t, True)
            except Exception:                                   # noqa: BLE001 -- left the pool between the two calls
                continue
            outs = []
            for out in tx.get("vout") or []:
                script = (out.get("scriptPubKey") or {}).get("hex", "")
                if not script:
                    continue
                parsed = parse_output(script, 0, params)
                if parsed.type in SPENDABLE and parsed.address:
                    outs.append((int(out.get("n", 0)), parsed.address,
                                 int(round(float(out.get("value", 0)) * 100_000_000))))
            found[t] = outs
        with self._lock:
            self._outs.update(found)
        self.read_at = time.time()
        return bool(gone or any(found.values()))

    def to(self, address: str) -> list[dict]:
        """Coins in the pool paying `address`."""
        with self._lock:
            return [{"txid": t, "vout": v, "value": value, "address": a}
                    for t, outs in self._outs.items() for v, a, value in outs if a == address]
