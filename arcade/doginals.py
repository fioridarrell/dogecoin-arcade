"""Doginals on the coins a Dogecoin wallet spends (2026-10-14: "identify which UTXOs are doginals to make sure they
are not spent unless somebody is trying to send a doginal to someone else").

A coin is kept OUT of an ordinary send when either
  1. Doge Soft's explorer (docs.dogesoft.io/explorer/api, `GET /api/explorer/tx/<txid>`, its `inscriptions` with `vout`)
     says an inscription sits on it, or
  2. it is worth POSTAGE_MAX or less and Doge Soft has not said it is clean -- Doginals sit on small coins (usually 0.001
     DOGE) and Doge Soft's index is still filling in, so a small coin nobody vouched for is treated as inscribed.
The server asks Doge Soft only when a send needs coins, and keeps every answer (an unspent coin's answer never changes):
one question per transaction, ever. Sending a Doginal ON PURPOSE spends its coin through doginal_send, not this filter.
"""

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

DOGESOFT = os.environ.get("ARCADE_DOGESOFT", "https://explorer.dogesoft.io")
# 2026-10-14: "We're going to use Doge soft for our doginals API for now. But if we can find a different API or if at
# some point, we can spin up our own. Wonkyord we will do that." The source is one setting: ARCADE_DOGINALS=dogesoft (default)
# or ARCADE_DOGINALS=ord with ARCADE_ORD_URL=<a wonky-ord / ord-style server>, asked per output (`GET /output/<txid>:<vout>`).
SOURCE = os.environ.get("ARCADE_DOGINALS", "dogesoft")
ORD_URL = os.environ.get("ARCADE_ORD_URL", "")
POSTAGE_MAX = 1_000_000          # koinu (0.01 DOGE): at or under this, a coin may be carrying a Doginal
ASK_TIMEOUT = 90                 # Doge Soft can take most of a minute to answer one transaction


def content_url(inscription: str) -> str:
    """Where a page shows a Doginal's content from: the ord-style server's /content/<id>, or Doge Soft's
    (ARCADE_DOGINAL_CONTENT overrides, with {id} for the inscription id) [Claude 2026-10-15]."""
    shape = os.environ.get("ARCADE_DOGINAL_CONTENT", "")
    if shape:
        return shape.replace("{id}", inscription)
    base = ORD_URL.rstrip("/") if SOURCE == "ord" and ORD_URL else DOGESOFT.rstrip("/")
    return f"{base}/content/{inscription}"


class Unknown(Exception):
    """Doge Soft could not be asked (down, slow, refused)."""


class Doginals:
    """What Doge Soft says sits on each output of a transaction, remembered for good."""

    def __init__(self, path: Path, fetch=None):
        self.path = Path(path)
        self.fetch = fetch or self._fetch
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tx (txid TEXT PRIMARY KEY, held TEXT NOT NULL, at REAL NOT NULL)")

    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        return db

    @staticmethod
    def _get(url: str) -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=ASK_TIMEOUT) as r:
            return json.load(r)

    @classmethod
    def _fetch(cls, txid: str) -> dict:
        """Doge Soft's answer for a transaction, or the same shape built from an ord-style server's per-output answers."""
        if SOURCE == "ord" and ORD_URL:
            tx = cls._get(f"{ORD_URL.rstrip('/')}/tx/{txid}")
            outs = tx.get("transaction", tx).get("output") or tx.get("outputs") or []
            found = []
            for vout in range(len(outs)):
                got = cls._get(f"{ORD_URL.rstrip('/')}/output/{txid}:{vout}")
                for iid in got.get("inscriptions") or []:
                    found.append({"vout": vout, "inscription_id": iid, "sat_offset": None})
            return {"txid": txid, "inscriptions": found}
        return cls._get(f"{DOGESOFT}/api/explorer/tx/{txid}")

    def held(self, txid: str) -> dict[int, list[dict]]:
        """{vout: [{"id", "offset"}]} for one transaction; {} when nothing is inscribed on any of its outputs."""
        with self._lock, self._db() as db:
            row = db.execute("SELECT held FROM tx WHERE txid=?", (txid,)).fetchone()
        if row:
            return {int(k): v for k, v in json.loads(row[0]).items()}
        try:
            said = self.fetch(txid) or {}
        except Exception as exc:                            # noqa: BLE001
            raise Unknown(f"Doge Soft did not answer for {txid}: {exc}") from exc
        if said.get("txid") not in (None, txid):
            raise Unknown(f"Doge Soft answered about another transaction ({said.get('txid')})")
        held: dict[int, list[dict]] = {}
        for ins in said.get("inscriptions") or []:
            try:
                vout = int(ins["vout"])
            except (KeyError, TypeError, ValueError):
                continue
            held.setdefault(vout, []).append({"id": ins.get("inscription_id"), "offset": ins.get("sat_offset")})
        with self._lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO tx VALUES (?,?,?)", (txid, json.dumps(held), time.time()))
        return held

    def kept(self, txid: str) -> dict[int, list[dict]] | None:
        """What is already known about a transaction, without asking anybody: None when it was never asked."""
        with self._lock, self._db() as db:
            row = db.execute("SELECT held FROM tx WHERE txid=?", (txid,)).fetchone()
        return {int(k): v for k, v in json.loads(row[0]).items()} if row else None

    def spendable_known(self, coins: list[dict]) -> list[dict]:
        """`spendable` from what is already known, asking Doge Soft nothing (it can take most of a minute a transaction):
        a coin known to carry a Doginal stays out; a small coin nobody vouched for stays out; anything else may go -- the
        rule `spendable` keeps while Doge Soft is away."""
        out = []
        for coin in coins:
            on = self.kept(coin["txid"])
            if on is None:
                if int(coin["value"]) > POSTAGE_MAX:
                    out.append(coin)
            elif not on.get(int(coin["vout"])):
                out.append(coin)
        return out

    def spendable(self, coins: list[dict]) -> list[dict]:
        """The coins an ordinary send may use: none Doge Soft says is inscribed, and no small one it has not vouched for."""
        out = []
        for coin in coins:
            try:
                on = self.held(coin["txid"]).get(int(coin["vout"]))
            except Unknown:
                if int(coin["value"]) > POSTAGE_MAX:
                    out.append(coin)                        # a big coin while Doge Soft is away: not a Doginal's postage
                continue
            if not on:
                out.append(coin)
        return out


    def vouch_broadcast(self, raw_hex: str, txid: str) -> bool:
        """Remember a transaction this arcade just broadcast as carrying no Doginal, when that is
        certain without asking: every input spends a coin already known clean (its transaction is
        kept here and that output holds nothing), and no input script carries an inscription
        envelope ("ord"). Then nothing inscribed went in, so nothing inscribed came out -- above all
        the small dust outputs a token transaction makes, which would otherwise wait for Doge Soft
        like any small coin it has not vouched for (2026-10-15: "If creating Omni assets
        creates a bunch of small dust UTXOs we don't want those to be saved like the rest of the
        small doginal UTXOs"). True if it was remembered."""
        try:
            inputs = _inputs_of(bytes.fromhex(raw_hex))
        except (ValueError, IndexError):
            return False
        if not inputs:
            return False
        with self._lock, self._db() as db:
            for prev, vout, script in inputs:
                if b"\x03ord" in script:
                    return False                        # a reveal: this transaction inscribes
                row = db.execute("SELECT held FROM tx WHERE txid=?", (prev,)).fetchone()
                if row is None or str(vout) in json.loads(row[0]):
                    return False                        # not known clean: leave it to Doge Soft
            db.execute("INSERT OR IGNORE INTO tx VALUES (?,?,?)", (txid, "{}", time.time()))
        return True


def _inputs_of(raw: bytes) -> list[tuple[str, int, bytes]]:
    """(previous txid, vout, input script) for each input of a serialized (non-segwit) transaction."""
    def varint(at: int) -> tuple[int, int]:
        first = raw[at]
        if first < 0xFD:
            return first, at + 1
        size = {0xFD: 2, 0xFE: 4, 0xFF: 8}[first]
        return int.from_bytes(raw[at + 1:at + 1 + size], "little"), at + 1 + size
    count, at = varint(4)
    out = []
    for _ in range(count):
        prev = raw[at:at + 32][::-1].hex()
        vout = int.from_bytes(raw[at + 32:at + 36], "little")
        length, at = varint(at + 36)
        out.append((prev, vout, raw[at:at + length]))
        at += length + 4
    return out


#: An inscription id as ord-dogecoin and Doge Soft write it: the reveal transaction's id, "i", its index.
INSCRIPTION_ID = re.compile(r"^[0-9a-f]{64}i[0-9]{1,6}$")


def exactly(on: list[dict] | None, inscription: str) -> str:
    """"" when what Doge Soft says sits on a coin (`held(txid)[vout]`) is exactly `inscription`, at the coin's first koinu;
    else why not. A coin with two inscriptions, or one sitting further in, would need an alignment output first, and moving it
    plainly could hand the wrong piece away or burn it to fees ([Claude 2026-10-15] shared by the plain send and the swap lock)."""
    on = on or []
    if [i.get("id") for i in on] != [inscription]:
        return "that coin does not hold exactly that Doginal (Doge Soft says: %s)" % (
            ", ".join(str(i.get("id")) for i in on) or "nothing")
    if int(on[0].get("offset") or 0) != 0:
        return "that Doginal does not sit at the start of its coin: it needs an alignment output first"
    return ""
