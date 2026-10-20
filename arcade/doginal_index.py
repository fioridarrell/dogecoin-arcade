"""Our own index of every Doginal on the Dogecoin chain, built and checked by the arcades themselves (the operator
2026-10-20: "index all 8 million doginals ... mindful of our 500 GB hard drive and limited compute"; "the mesh network
shared, indexing and verification"; "it should get the block scan the block get the next block scan the next block";
"if one goes off-line, that small chunk of blocks can be allocated to someone new"). Read from the chain only: no
outside index is asked, ever. Plan: ~/docs/doginals-index-plan.md on the operator's machine.

Two jobs, three modes (setting `doginal_index`):

* `walk` -- an arcade with its own Dogecoin Core reads every block from BASE in order, one at a time: each
  inscription's birth (the transaction whose input opens an ord envelope, as doginals.trace has it), and every move
  after (koinu first-in-first-out, ord's rule), so it knows where each one is now. Kept in doginal-index.sqlite: about
  100 bytes an inscription, pointers into the chain, never the content.
* `help` (the default) -- the shared work: the chain is cut into CHUNK-block chunks; an arcade leases one over the
  mesh, scans it a block at a time (through its own Core or a borrowed one, every block proved by rawchain), and says
  the chunk's hash. A walker says the same hash for every chunk it passes. Two arcades with the same hash for a chunk:
  `agreed`. A different hash is a lie or a bug, and is shown.
* `off`.

What a chunk's hash covers (`block_hash_of`): each block's hash, and each birth in it (txid, the input that carries
the envelope, the content type). Births can be found by anybody holding the block; where an inscription is NOW needs
every earlier block in order and the values of the coins spent, so only a walker answers that.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from . import rawchain

log = logging.getLogger("arcade.doginal_index")

#: Where the chunks start (5 Dec 2022, before the first Doginal) and how big each is. Every arcade must cut the same
#: chunks, so these two never change without a new protocol word.
BASE = 4_500_000
CHUNK = 100
#: Blocks this far under the tip are scanned; the newest ones may still be replaced by a reorg.
SETTLE = 12
#: How many different arcades must say the same hash before a chunk is `agreed` (fewer than 5 arcades: every chunk
#: is scanned twice).
AGREE = 2
#: A lease lasts this long unless renewed by progress; a helper that goes quiet loses its chunk to the next one.
LEASE_SECONDS = 600
PROGRESS_EVERY = 10              # blocks between two progress words
#: Undo kept for this many blocks back, so a reorg rolls the walker back instead of stopping it.
UNDO_BLOCKS = 300
MODES = ("walk", "help", "off")


# --------------------------------------------------------------------------- reading a block

def _pushes(raw: bytes) -> list:
    from .doginals import _pushes as pushes
    return pushes(raw)


def envelope_input(tx: rawchain.Tx) -> int | None:
    """The first input of `tx` that opens an ord envelope (a push of "ord" first), or None (doginals.envelope)."""
    for j, i in enumerate(tx.vin):
        if i["vout"] != -1 and i["script"][:4] == b"\x03ord":
            return j
    return None


def _parts_end(data: list) -> bool:
    """Do these pushes close the envelope: (count, data) pairs down to a count of 0 (doginals.content's reading)?"""
    i = 0
    while i + 1 < len(data) and data[i] is not None and len(data[i]) <= 3 and data[i + 1] is not None:
        if int.from_bytes(data[i], "little") == 0:
            return True
        i += 2
    return False


def birth_of(tx: rawchain.Tx) -> dict | None:
    """{"vin", "ctype", "done"} when `tx` gives birth to an inscription, else None."""
    j = envelope_input(tx)
    if j is None:
        return None
    pushes = _pushes(tx.vin[j]["script"])
    ctype = pushes[2] if len(pushes) >= 3 and isinstance(pushes[2], bytes) else b""
    return {"vin": j, "ctype": ctype.hex(), "done": _parts_end(pushes[3:])}


def births(block: rawchain.Block) -> list[tuple[str, int, str]]:
    """(txid, envelope input, content type hex) for every birth in the block, in block order."""
    out = []
    for tx in block.txs:
        b = birth_of(tx)
        if b is not None:
            out.append((tx.txid, b["vin"], b["ctype"]))
    return out


def block_hash_of(block_hash: str, found: list[tuple[str, int, str]]) -> str:
    """One block's line in a chunk's hash: the block, and what was born in it."""
    h = hashlib.sha256(block_hash.encode() + b"\n")
    for txid, vin, ctype in found:
        h.update(f"{txid}:{vin}:{ctype}\n".encode())
    return h.hexdigest()


def chunk_hash_of(lines: list[str]) -> str:
    """A chunk's hash from its CHUNK block lines, in height order."""
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def chunk_of(height: int) -> int:
    return (height - BASE) // CHUNK


def chunk_range(c: int) -> tuple[int, int]:
    lo = BASE + c * CHUNK
    return lo, lo + CHUNK


def ready_chunks(tip: int) -> int:
    """How many chunks are wholly settled under this tip."""
    return max(0, (tip - SETTLE + 1 - BASE) // CHUNK)


# --------------------------------------------------------------------------- the store

class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=60, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            -- every block a walker or a helper read: its hash and its line in the chunk hash
            CREATE TABLE IF NOT EXISTS block (height INTEGER PRIMARY KEY, hash TEXT NOT NULL, line TEXT NOT NULL,
                                              n INTEGER NOT NULL DEFAULT 0, moves TEXT);
            -- the inscriptions (walker): where born, what type, and when its envelope closed
            CREATE TABLE IF NOT EXISTS insc (rid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, height INTEGER NOT NULL,
                                             txpos INTEGER NOT NULL, vin INTEGER NOT NULL, ctype TEXT NOT NULL,
                                             done_height INTEGER, pieces INTEGER NOT NULL DEFAULT 1,
                                             burned INTEGER);
            -- where each one is now (walker): the output and the koinu inside it
            CREATE TABLE IF NOT EXISTS at (rid INTEGER PRIMARY KEY, txid TEXT NOT NULL, vout INTEGER NOT NULL,
                                           off INTEGER NOT NULL, height INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS at_out ON at (txid, vout);
            -- envelopes still open (walker): the output whose spender carries the next piece
            CREATE TABLE IF NOT EXISTS pending (rid INTEGER PRIMARY KEY, txid TEXT NOT NULL, vout INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS undo (height INTEGER PRIMARY KEY, doc TEXT NOT NULL);
            -- every arcade's word about every chunk, this one's included
            CREATE TABLE IF NOT EXISTS seen (chunk INTEGER NOT NULL, node TEXT NOT NULL, hash TEXT NOT NULL,
                                             births INTEGER NOT NULL, at REAL NOT NULL, PRIMARY KEY (chunk, node));
        """)

    def q(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)

    def many(self, sql: str, rows: list) -> None:
        with self._lock:
            self._db.executemany(sql, rows)

    def tx(self):
        return _Tx(self)

    def meta(self, k: str, default: Any = None) -> Any:
        row = self.q("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(row[0][0]) if row else default

    def set_meta(self, k: str, v: Any) -> None:
        self.x("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))

    # --- chunks --------------------------------------------------------------------------------------------------

    def chunk_line(self, c: int) -> tuple[str, int] | None:
        """(this arcade's hash of chunk c, births in it) when every one of its blocks has been read here."""
        lo, hi = chunk_range(c)
        rows = self.q("SELECT height, line, n FROM block WHERE height>=? AND height<? ORDER BY height", (lo, hi))
        if len(rows) != CHUNK:
            return None
        return chunk_hash_of([r[1] for r in rows]), sum(r[2] for r in rows)

    def say(self, c: int, node: str, hash_: str, n: int) -> bool:
        """File an arcade's hash for a chunk (node "" is this arcade). True when it is new."""
        was = self.q("SELECT hash FROM seen WHERE chunk=? AND node=?", (c, node))
        if was and was[0][0] == hash_:
            return False
        self.x("INSERT OR REPLACE INTO seen VALUES (?,?,?,?,?)", (c, node, hash_, n, time.time()))
        return True

    def words(self, c: int) -> dict[str, str]:
        return {r[0]: r[1] for r in self.q("SELECT node, hash FROM seen WHERE chunk=?", (c,))}

    def all_words(self, below: int) -> dict[int, dict[str, str]]:
        out: dict[int, dict[str, str]] = {}
        for c, node, h in self.q("SELECT chunk, node, hash FROM seen WHERE chunk < ?", (below,)):
            out.setdefault(c, {})[node] = h
        return out


class _Tx:
    def __init__(self, store: Store):
        self.store = store

    def __enter__(self):
        self.store._lock.acquire()
        self.store._db.execute("BEGIN")
        return self.store._db

    def __exit__(self, kind, *exc):
        try:
            self.store._db.execute("ROLLBACK" if kind else "COMMIT")
        finally:
            self.store._lock.release()


# --------------------------------------------------------------------------- the walker

class Reorg(Exception):
    pass


def _outpoint(txid: str, vout: int) -> bytes:
    return bytes.fromhex(txid) + vout.to_bytes(4, "little")


class Walker:
    """Every block from BASE in order: births, moves, envelopes closing. Needs this arcade's OWN Dogecoin Core (the
    values of spent coins are asked of it)."""

    def __init__(self, store: Store):
        self.store = store
        self.tracked: dict[bytes, list[tuple[int, int]]] | None = None     # outpoint -> [(rid, koinu offset)]
        self.pending: dict[bytes, int] = {}                                # outpoint -> rid whose envelope is open
        self.values: OrderedDict[bytes, int] = OrderedDict()               # recent outputs' koinu
        self.rate = 0.0
        self.error = ""

    def next_height(self) -> int:
        return int(self.store.meta("walk_next", BASE))

    def _load(self) -> None:
        tracked: dict[bytes, list[tuple[int, int]]] = {}
        for rid, txid, vout, off in self.store.q("SELECT rid, txid, vout, off FROM at"):
            tracked.setdefault(_outpoint(txid, vout), []).append((rid, off))
        self.tracked = tracked
        self.pending = {_outpoint(t, v): rid for rid, t, v in self.store.q("SELECT rid, txid, vout FROM pending")}

    def _value(self, rpc, txid: str, vout: int) -> int:
        key = _outpoint(txid, vout)
        got = self.values.get(key)
        if got is not None:
            return got
        tx = rawchain.read_tx(bytes.fromhex(rpc.call("getrawtransaction", txid, 0)), txid)
        for n, (v, _s) in enumerate(tx.vout):
            self._keep(_outpoint(txid, n), v)
        return tx.vout[vout][0]

    def _keep(self, key: bytes, value: int) -> None:
        self.values[key] = value
        if len(self.values) > 2_000_000:
            self.values.popitem(last=False)

    def step(self, rpc, tip: int, budget: float = 5.0) -> int:
        """Walk on for about `budget` seconds, never past tip - SETTLE. Returns the blocks walked."""
        if self.tracked is None:
            self._load()
        start, done = time.monotonic(), 0
        while True:
            h = self.next_height()
            if h > tip - SETTLE or (done and time.monotonic() - start >= budget):
                break
            block_hash = rpc.call("getblockhash", h)
            block = rawchain.read_block(bytes.fromhex(rpc.call("getblock", block_hash, 0)), block_hash)
            if h > BASE:
                before = self.store.q("SELECT hash FROM block WHERE height=?", (h - 1,))
                if before and before[0][0] != block.prev:
                    self.rollback(h - 1)
                    continue
            self.walk_block(rpc, h, block)
            done += 1
        took = time.monotonic() - start
        if done:
            self.rate = done / max(took, 1e-6)
        return done

    def walk_block(self, rpc, h: int, block: rawchain.Block) -> list[str]:
        """One block: births, moves, closes; filed with its undo. Returns the moves lines (for tests)."""
        tracked, pending = self.tracked, self.pending
        assert tracked is not None
        undo: dict[str, list] = {"born": [], "at": [], "pending": [], "done": []}
        moves: list[str] = []
        born_here: list[tuple[str, int, str]] = []
        rows_at: dict[int, tuple | None] = {}
        rid_ids: dict[int, str] = {}
        next_rid = (self.store.q("SELECT COALESCE(MAX(rid), 0) FROM insc")[0][0]) + 1
        with self.store.tx() as db:
            for pos, tx in enumerate(block.txs):
                for n, (v, _s) in enumerate(tx.vout):
                    if v <= 100_000_000:
                        self._keep(_outpoint(tx.txid, n), v)
                if pos == 0:
                    continue
                # What this transaction carries in: tracked coins it spends (their inscriptions move), a birth.
                carried: list[tuple[int, int]] = []          # (rid, koinu position among the inputs)
                needs = [j for j, i in enumerate(tx.vin) if _outpoint(i["txid"], i["vout"]) in tracked]
                b = birth_of(tx)
                upto = max(needs + ([b["vin"]] if b else []), default=-1)
                if upto < 0 and not pending:
                    continue
                if upto >= 0:
                    sizes = [self._value(rpc, i["txid"], i["vout"]) for i in tx.vin[:upto]]
                    for j in needs:
                        base = sum(sizes[:j])
                        key = _outpoint(tx.vin[j]["txid"], tx.vin[j]["vout"])
                        for rid, off in tracked.pop(key):
                            carried.append((rid, base + off))
                    if b:
                        rid = next_rid
                        next_rid += 1
                        iid = f"{tx.txid}i0"
                        db.execute("INSERT INTO insc (rid, id, height, txpos, vin, ctype, done_height) VALUES "
                                   "(?,?,?,?,?,?,?)", (rid, iid, h, pos, b["vin"], b["ctype"], h if b["done"] else None))
                        undo["born"].append(rid)
                        born_here.append((tx.txid, b["vin"], b["ctype"]))
                        rid_ids[rid] = iid
                        rows_at[rid] = None                       # born here: nowhere before
                        undo["at"].append([rid, None])
                        moves.append(f"B {iid}\n")
                        carried.append((rid, sum(sizes[:b["vin"]])))
                        if not b["done"]:
                            key = _outpoint(tx.txid, 0)
                            pending[key] = rid
                            db.execute("INSERT OR REPLACE INTO pending VALUES (?,?,?)", (rid, tx.txid, 0))
                            undo["pending"].append([rid, None])
                # An open envelope continued: input 0's pushes, in the transaction spending its output 0.
                for i in tx.vin:
                    key = _outpoint(i["txid"], i["vout"])
                    rid = pending.pop(key, None)
                    if rid is None:
                        continue
                    undo["pending"].append([rid, [i["txid"], i["vout"]]])
                    db.execute("UPDATE insc SET pieces = pieces + 1 WHERE rid=?", (rid,))
                    if _parts_end(_pushes(tx.vin[0]["script"])):
                        db.execute("DELETE FROM pending WHERE rid=?", (rid,))
                        db.execute("UPDATE insc SET done_height=? WHERE rid=?", (h, rid))
                        undo["done"].append(rid)
                    else:
                        pending[_outpoint(tx.txid, 0)] = rid
                        db.execute("INSERT OR REPLACE INTO pending VALUES (?,?,?)", (rid, tx.txid, 0))
                if not carried:
                    continue
                # Where each carried koinu lands: the outputs in order; past the last one, it went to the fee.
                ends, total = [], 0
                for v, _s in tx.vout:
                    total += v
                    ends.append(total)
                for rid, p in sorted(carried, key=lambda c: c[1]):
                    if rid not in rows_at:
                        old = db.execute("SELECT txid, vout, off, height FROM at WHERE rid=?", (rid,)).fetchone()
                        rows_at[rid] = tuple(old) if old else None
                        undo["at"].append([rid, list(old) if old else None])
                    iid = rid_ids.get(rid) or db.execute("SELECT id FROM insc WHERE rid=?", (rid,)).fetchone()[0]
                    k = next((n for n, e in enumerate(ends) if p < e), None)
                    if k is None:
                        db.execute("DELETE FROM at WHERE rid=?", (rid,))
                        db.execute("UPDATE insc SET burned=? WHERE rid=?", (h, rid))
                        moves.append(f"X {iid}\n")
                        continue
                    off = p - (ends[k - 1] if k else 0)
                    db.execute("INSERT OR REPLACE INTO at VALUES (?,?,?,?,?)", (rid, tx.txid, k, off, h))
                    tracked.setdefault(_outpoint(tx.txid, k), []).append((rid, off))
                    moves.append(f"M {iid} {tx.txid}:{k}:{off}\n")
            line = block_hash_of(block.hash, born_here)
            moved = hashlib.sha256("".join(moves).encode()).hexdigest()
            db.execute("INSERT OR REPLACE INTO block VALUES (?,?,?,?,?)", (h, block.hash, line, len(born_here), moved))
            db.execute("INSERT OR REPLACE INTO undo VALUES (?,?)", (h, json.dumps(undo)))
            db.execute("DELETE FROM undo WHERE height<?", (h - UNDO_BLOCKS,))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('walk_next', ?)", (json.dumps(h + 1),))
            if (h + 1 - BASE) % CHUNK == 0:
                c = chunk_of(h)
                rows = db.execute("SELECT line, n FROM block WHERE height>=? AND height<=? ORDER BY height",
                                  (chunk_range(c)[0], h)).fetchall()
                if len(rows) == CHUNK:
                    db.execute("INSERT OR REPLACE INTO seen VALUES (?,?,?,?,?)",
                               (c, "", chunk_hash_of([r[0] for r in rows]), sum(r[1] for r in rows), time.time()))
        return moves

    def rollback(self, h: int) -> None:
        """Undo block h (a reorg replaced it), then reload what is tracked."""
        row = self.store.q("SELECT doc FROM undo WHERE height=?", (h,))
        if not row:
            self.error = f"a reorg deeper than {UNDO_BLOCKS} blocks at {h}: the walk stopped there"
            raise Reorg(self.error)
        undo = json.loads(row[0][0])
        with self.store.tx() as db:
            for rid, old in reversed(undo["at"]):
                if old is None:
                    db.execute("DELETE FROM at WHERE rid=?", (rid,))
                else:
                    db.execute("INSERT OR REPLACE INTO at VALUES (?,?,?,?,?)", (rid, *old))
                db.execute("UPDATE insc SET burned=NULL WHERE rid=? AND burned=?", (rid, h))
            for rid in undo["done"]:
                db.execute("UPDATE insc SET done_height=NULL WHERE rid=?", (rid,))
            for rid, was in reversed(undo["pending"]):
                db.execute("DELETE FROM pending WHERE rid=?", (rid,))
                if was is not None:
                    db.execute("INSERT INTO pending VALUES (?,?,?)", (rid, was[0], was[1]))
                    db.execute("UPDATE insc SET pieces = pieces - 1 WHERE rid=?", (rid,))
            for rid in undo["born"]:
                db.execute("DELETE FROM insc WHERE rid=?", (rid,))
                db.execute("DELETE FROM at WHERE rid=?", (rid,))
                db.execute("DELETE FROM pending WHERE rid=?", (rid,))
            db.execute("DELETE FROM block WHERE height=?", (h,))
            db.execute("DELETE FROM undo WHERE height=?", (h,))
            db.execute("DELETE FROM seen WHERE chunk=? AND node=''", (chunk_of(h),))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('walk_next', ?)", (json.dumps(h),))
        log.warning("doginal index: block %d was replaced (reorg); walked back", h)
        self.tracked = None
        self._load()


# --------------------------------------------------------------------------- the shared chunks

class Chunks:
    """The chunk leases this arcade knows of (its own and its linked arcades'), and choosing the next chunk to scan."""

    def __init__(self, store: Store):
        self.store = store
        self.leases: dict[int, dict[str, tuple[float, int]]] = {}       # chunk -> node -> (until, at height)
        self.peers: dict[str, dict] = {}                                 # node -> its last "hi"
        self.mine: dict | None = store.meta("lease")                     # {"c", "at"}

    def lease_from(self, node: str, c: int, at: int, until: float) -> None:
        self.leases.setdefault(int(c), {})[node] = (float(until), int(at))

    def drop_from(self, node: str, c: int) -> None:
        self.leases.get(int(c), {}).pop(node, None)

    def gone(self, node: str) -> None:
        for held in self.leases.values():
            held.pop(node, None)

    def wanted(self, c: int, now: float, words: dict[str, str] | None = None) -> int:
        """How many more scans chunk c wants, counting the leases running on it."""
        words = self.store.words(c) if words is None else words
        if "" in words:
            return 0
        tally: dict[str, int] = {}
        for h in words.values():
            tally[h] = tally.get(h, 0) + 1
        best = max(tally.values(), default=0)
        busy = sum(1 for until, _at in self.leases.get(c, {}).values() if until > now)
        return max(0, AGREE - best - busy)

    def choose(self, ready: int, now: float, walker_at: int | None = None) -> int | None:
        """The next chunk to take: one that some arcade has already said (so it becomes agreed) first, then the lowest
        nobody has; a random one of the first few either way, so two helpers rarely take the same."""
        said = self.store.all_words(ready)
        first = [c for c in sorted(said) if self.wanted(c, now, said[c]) > 0][:16]
        if not first:
            c = 0
            while c < ready and len(first) < 16:
                if c not in said and self.wanted(c, now, {}) > 0:
                    first.append(c)
                c += 1
        return random.choice(first) if first else None


def scan_block(rpc, h: int) -> tuple[str, str, int]:
    """(block hash, its line, births in it) for one block, read raw and proved (rawchain)."""
    block_hash = rpc.call("getblockhash", h)
    block = rawchain.read_block(bytes.fromhex(rpc.call("getblock", block_hash, 0)), block_hash)
    found = births(block)
    return block.hash, block_hash_of(block.hash, found), len(found)


# --------------------------------------------------------------------------- the worker

class DoginalIndex:
    """The thread's work: walk or help, a little at a time, and what the mesh hears and says (DxShare)."""

    def __init__(self, path: Path, rpc_factory: Callable[[], Any] | None, mode: Callable[[], str],
                 own_core: Callable[[], bool] = lambda: True):
        self.store = Store(path)
        self.rpc_factory = rpc_factory
        self.mode = mode
        self.own_core = own_core
        self.walker = Walker(self.store)
        self.chunks = Chunks(self.store)
        self.share: Any = None                       # DxShare, when the mesh runs
        self.tip = 0
        self.helped = int(self.store.meta("helped", 0))
        self.help_rate = 0.0
        self.error = ""
        self._said_upto = int(self.store.meta("said_upto", -1))

    # --- one pass --------------------------------------------------------------------------------------------------

    def step(self, budget: float = 5.0) -> float:
        """Do about `budget` seconds of work; returns how long to rest (as long as the work took: half the time)."""
        mode = self.mode()
        if mode not in ("walk", "help") or self.rpc_factory is None:
            return 10.0
        started = time.monotonic()
        try:
            with self.rpc_factory() as rpc:
                self.tip = int(rpc.call("getblockcount"))
                if mode == "walk" and self.own_core():
                    self.walker.step(rpc, self.tip, budget)
                else:
                    self._help(rpc, budget)
            self.error = ""
        except Reorg as exc:
            self.error = str(exc)
            return 60.0
        except Exception as exc:                         # noqa: BLE001 -- said in the admin panel, tried again
            self.error = str(exc)[:200]
            log.debug("doginal index pass failed", exc_info=True)
            return 30.0
        self._tell_new_chunks()
        return max(0.5, time.monotonic() - started)

    def _help(self, rpc, budget: float) -> None:
        now = time.time()
        ready = ready_chunks(self.tip)
        lease = self.chunks.mine
        if lease is None:
            c = self.chunks.choose(ready, now)
            if c is None:
                return
            lease = self.chunks.mine = {"c": c, "at": chunk_range(c)[0]}
            self.store.set_meta("lease", lease)
            self._say_lease(lease)
        c, (lo, hi) = lease["c"], chunk_range(lease["c"])
        start, done = time.monotonic(), 0
        while lease["at"] < hi and not (done and time.monotonic() - start >= budget):
            h = lease["at"]
            have = self.store.q("SELECT hash, line FROM block WHERE height=?", (h,))
            if not have:
                block_hash, line, n = scan_block(rpc, h)
                self.store.x("INSERT OR REPLACE INTO block (height, hash, line, n) VALUES (?,?,?,?)",
                             (h, block_hash, line, n))
            lease["at"] = h + 1
            done += 1
            if (lease["at"] - lo) % PROGRESS_EVERY == 0:
                self.store.set_meta("lease", lease)
                self._say_lease(lease)
        if done:
            self.help_rate = done / max(time.monotonic() - start, 1e-6)
        if lease["at"] >= hi:
            got = self.store.chunk_line(c)
            if got is not None:
                self.store.say(c, "", got[0], got[1])
                self.helped += 1
                self.store.set_meta("helped", self.helped)
            self.chunks.mine = None
            self.store.set_meta("lease", None)
            if self.share is not None:
                self.share.post({"op": "ld", "c": c})
        else:
            self.store.set_meta("lease", lease)

    def _say_lease(self, lease: dict) -> None:
        if self.share is not None:
            self.share.post({"op": "ls", "c": lease["c"], "at": lease["at"], "until": time.time() + LEASE_SECONDS})

    def _tell_new_chunks(self) -> None:
        """This arcade's hashes it has not said yet, to every linked arcade."""
        if self.share is None:
            return
        rows = self.store.q("SELECT rowid, chunk, hash, births FROM seen WHERE node='' AND rowid > ? "
                            "ORDER BY rowid LIMIT 500", (self._said_upto,))
        if not rows:
            return
        self.share.post({"op": "dn", "rows": [[c, h, n] for _r, c, h, n in rows]})
        self._said_upto = int(rows[-1][0])
        self.store.set_meta("said_upto", self._said_upto)

    def all_mine(self) -> list[list]:
        return [[c, h, n] for c, h, n in self.store.q("SELECT chunk, hash, births FROM seen WHERE node=''")]

    # --- what the mesh heard ---------------------------------------------------------------------------------------

    def heard(self, node: str, header: dict) -> None:
        op = header.get("op")
        if op == "hi":
            self.chunks.peers[node] = {k: header.get(k) for k in ("mode", "walk", "helped", "v")} | {"at": time.time()}
        elif op == "ls":
            self.chunks.lease_from(node, int(header["c"]), int(header.get("at") or 0),
                                   min(float(header.get("until") or 0), time.time() + LEASE_SECONDS))
        elif op == "ld":
            self.chunks.drop_from(node, int(header["c"]))
        elif op == "dn":
            for row in (header.get("rows") or [])[:1000]:
                try:
                    c, h, n = int(row[0]), str(row[1]), int(row[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if 0 <= c and len(h) == 64:
                    self.store.say(c, node, h, n)
                    self.chunks.drop_from(node, c)

    def hello(self) -> dict:
        return {"op": "hi", "mode": self.mode(), "walk": self.walker.next_height() if self.mode() == "walk" else None,
                "helped": self.helped, "v": 1}

    # --- for people ------------------------------------------------------------------------------------------------

    def status(self) -> dict:
        now = time.time()
        ready = ready_chunks(self.tip) if self.tip else 0
        per = self.store.all_words(max(ready, 1))
        agreed = differ = one = 0
        differs = []
        for c, words in per.items():
            tally: dict[str, int] = {}
            for h in words.values():
                tally[h] = tally.get(h, 0) + 1
            if max(tally.values()) >= AGREE:
                agreed += 1
            elif len(words) == 1:
                one += 1
            if len(tally) > 1:
                differ += 1
                if len(differs) < 10:
                    differs.append({"chunk": c, "blocks": list(chunk_range(c)),
                                    "says": {(n[:12] or "this arcade"): h[:16] for n, h in words.items()}})
        leases = []
        for c, held in self.chunks.leases.items():
            for node, (until, at) in held.items():
                if until > now:
                    lo, hi = chunk_range(c)
                    leases.append({"node": node[:12], "chunk": c, "blocks": [lo, hi], "at": at,
                                   "done": max(0, min(CHUNK, at - lo))})
        mine = self.chunks.mine
        walk = None
        if self.mode() == "walk":
            nxt = self.walker.next_height()
            counts = self.store.q("SELECT COUNT(*), COUNT(done_height), COUNT(burned) FROM insc")[0]
            walk = {"next": nxt, "tip": self.tip, "left": max(0, self.tip - SETTLE - nxt + 1),
                    "inscriptions": counts[0], "closed": counts[1], "burned": counts[2],
                    "rate": round(self.walker.rate, 2), "error": self.walker.error}
        return {"mode": self.mode(), "base": BASE, "chunk": CHUNK, "tip": self.tip, "ready": ready,
                "agreed": agreed, "one": one, "none": max(0, ready - len(per)), "differ": differ, "differs": differs,
                "lease": ({"chunk": mine["c"], "blocks": list(chunk_range(mine["c"])), "at": mine["at"],
                           "done": mine["at"] - chunk_range(mine["c"])[0]} if mine else None),
                "helped": self.helped, "help_rate": round(self.help_rate, 2), "leases": sorted(leases, key=lambda l: l["chunk"]),
                "peers": {n[:12]: p for n, p in self.chunks.peers.items()}, "walk": walk, "error": self.error}

    # --- lookups (walker) ------------------------------------------------------------------------------------------

    def where(self, inscription: str) -> dict | None:
        row = self.store.q("SELECT i.id, i.height, i.ctype, i.done_height, i.burned, a.txid, a.vout, a.off "
                           "FROM insc i LEFT JOIN at a ON a.rid = i.rid WHERE i.id=?", (inscription,))
        if not row:
            return None
        iid, height, ctype, done, burned, txid, vout, off = row[0]
        return {"id": iid, "height": height, "content_type": bytes.fromhex(ctype).decode("utf-8", "replace"),
                "closed": done is not None, "burned": burned,
                "output": f"{txid}:{vout}" if txid else None, "offset": off}

    def on(self, txid: str, vout: int) -> list[dict]:
        return [{"id": r[0], "offset": r[1]} for r in self.store.q(
            "SELECT i.id, a.off FROM at a JOIN insc i ON i.rid = a.rid WHERE a.txid=? AND a.vout=? ORDER BY a.off",
            (txid, vout))]


class Runner:
    """The daemon thread: DoginalIndex.step, resting as long as each pass took (half the machine at most)."""

    def __init__(self, index: DoginalIndex):
        self.index = index
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="arcade-doginal-index", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self._stop.wait(20)                              # let the node start first
        while not self._stop.is_set():
            try:
                rest = self.index.step()
            except Exception:                            # noqa: BLE001 -- never dies
                log.debug("doginal index step", exc_info=True)
                rest = 30.0
            self._stop.wait(rest)
