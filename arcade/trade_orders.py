"""Orders on the chain: what a trader will swap, visible to every node while it is online.

2026-10-13: a facilitating node's trades "should be visible to other node operators
as long as that node is online. They can be inscribed like any other order the only
difference is that the node operator is the one who is doing the listing." So an order is
an inscription, batched like a seller's listings (listing_announce.py): content type
`CONTENT_TYPE`, filed by every node that reads it.

    {"arcade": "orders", "v": 1,
     "orders":  [{id, give: {network, kind, asset, amount}, get: {...},
                  trade_key, msg_key, node, expires}],
     "cancels": [order id, ...]}

* `trade_key` is the lister's key in the locks (it gives `give` and takes `get`);
  `msg_key` is where trade steps are sealed to (the Messenger); `node` the mesh id of the
  node that answers for it -- the lister's own home node for a tab, itself for a
  facilitating node. The order shows on other nodes only while that node is online.
* Who listed it is the address that inscribed it, and only that address can cancel it.
  Nothing else here needs a signature: a lock pays only the key that is really in it, so
  an order naming somebody else's keys can waste a taker's time (they refund) and never
  their coins (arcade/trade.py checks every lock on the chain).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .config import NETWORKS

CONTENT_TYPE = "application/vnd.arcade.orders+json"
VERSION = 1
MAX_ORDERS = 50                 # per batch: an inscription, not a database
MAX_EXPIRY = 30 * 24 * 3600
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_KEY33 = re.compile(r"^0[23][0-9a-f]{64}$")
_HEX32 = re.compile(r"^[0-9a-f]{64}$")


class OrderError(ValueError):
    """Not an order this version reads."""


def _side(said: Any) -> dict:
    if not isinstance(said, dict):
        raise OrderError("a side of an order is an object")
    network, kind = said.get("network"), said.get("kind")
    if network not in NETWORKS:
        raise OrderError(f"no such network {network!r}")
    if kind not in ("coin", "token", "nft"):
        raise OrderError("an order moves a coin, a token or an nft")
    amount = said.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        raise OrderError("an amount is a positive whole number")
    asset = str(said.get("asset", ""))
    if (kind == "coin" and asset) or (kind == "token" and not asset.isdigit()) or \
            (kind == "nft" and (not _HEX32.match(asset) or amount != 1)):
        raise OrderError("that asset does not fit that kind")
    return {"network": network, "kind": kind, "asset": asset, "amount": amount}


def check_order(said: Any, now: float | None = None) -> dict:
    """One order, every field checked; returns the cleaned order."""
    if not isinstance(said, dict):
        raise OrderError("an order is an object")
    if not isinstance(said.get("id"), str) or not _ID.match(said["id"]):
        raise OrderError("an order id is 8-64 letters, digits, - or _")
    give, get = _side(said.get("give")), _side(said.get("get"))
    if (give["network"], give["kind"], give["asset"]) == (get["network"], get["kind"], get["asset"]):
        raise OrderError("an order swaps one thing for another")
    for field, pattern in (("trade_key", _KEY33), ("msg_key", _HEX32), ("node", _HEX32)):
        if not isinstance(said.get(field), str) or not pattern.match(said[field]):
            raise OrderError(f"an order's {field} is hex of the right length")
    expires = said.get("expires")
    now = now or time.time()
    if not isinstance(expires, int) or not now < expires <= now + MAX_EXPIRY:
        raise OrderError("an order expires in the future, within 30 days")
    return {"id": said["id"], "give": give, "get": get, "trade_key": said["trade_key"],
            "msg_key": said["msg_key"], "node": said["node"], "expires": expires}


def batch_json(orders: list[dict], cancels: list[str] = ()) -> bytes:
    """What goes on the chain: some orders (checked), and ids to cancel."""
    if len(orders) > MAX_ORDERS:
        raise OrderError(f"at most {MAX_ORDERS} orders to an inscription")
    clean = [check_order(o) for o in orders]
    for c in cancels:
        if not isinstance(c, str) or not _ID.match(c):
            raise OrderError("a cancel names an order id")
    return json.dumps({"arcade": "orders", "v": VERSION, "orders": clean,
                       "cancels": list(cancels)}, separators=(",", ":"), sort_keys=True).encode()


SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_order (
    id          TEXT NOT NULL,
    lister      TEXT NOT NULL,      -- the address that inscribed it
    network     TEXT NOT NULL,      -- the chain the inscription is on
    inscription TEXT NOT NULL,
    body        TEXT NOT NULL,      -- the checked order, JSON
    node        TEXT NOT NULL,
    expires     INTEGER NOT NULL,
    cancelled   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lister, id)
);
CREATE TABLE IF NOT EXISTS order_cursor (network TEXT PRIMARY KEY, number INTEGER NOT NULL);
"""


class OrderBook:
    """Every node's copy of the orders inscribed on the chains it reads."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as conn:
            conn.executescript(SCHEMA)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def file_batch(self, lister: str, network: str, inscription: str, body: bytes,
                   now: float | None = None) -> int:
        """File one batch as `lister` inscribed it. Orders that do not check out are
        skipped one by one; cancels touch only this lister's own orders."""
        try:
            said = json.loads(body)
        except ValueError:
            return 0
        if not isinstance(said, dict) or said.get("arcade") != "orders" or said.get("v") != VERSION:
            return 0
        filed = 0
        with self._open() as conn:
            for raw in (said.get("orders") or [])[:MAX_ORDERS]:
                try:
                    order = check_order(raw, now)
                except OrderError:
                    continue
                conn.execute("INSERT OR IGNORE INTO trade_order(id, lister, network, inscription, "
                             "body, node, expires) VALUES(?,?,?,?,?,?,?)",
                             (order["id"], lister, network, inscription, json.dumps(order),
                              order["node"], order["expires"]))
                filed += 1
            for oid in said.get("cancels") or []:
                if isinstance(oid, str):
                    conn.execute("UPDATE trade_order SET cancelled=1 WHERE lister=? AND id=?",
                                 (lister, oid))
        return filed

    def visible(self, online: Callable[[str], bool], now: float | None = None) -> list[dict]:
        """Orders to show: not expired, not cancelled, and their node online right now."""
        now = now or time.time()
        with self._open() as conn:
            rows = conn.execute("SELECT * FROM trade_order WHERE cancelled=0 AND expires>? "
                                "ORDER BY rowid", (int(now),)).fetchall()
        out = []
        for row in rows:
            if online(row["node"]):
                out.append({**json.loads(row["body"]), "lister": row["lister"],
                            "inscription": row["inscription"], "network": row["network"]})
        return out

    def cursor(self, network: str) -> int:
        with self._open() as conn:
            row = conn.execute("SELECT number FROM order_cursor WHERE network=?", (network,)).fetchone()
        return int(row["number"]) if row else 0

    def set_cursor(self, network: str, number: int) -> None:
        with self._open() as conn:
            conn.execute("INSERT OR REPLACE INTO order_cursor(network, number) VALUES(?,?)",
                         (network, int(number)))


def import_announced(book: OrderBook, index: Any, network: str, limit: int = 200) -> int:
    """File every order batch inscribed on `network` since the last look (by inscription
    number, as listing_announce does). Returns how many orders were filed."""
    last = book.cursor(network)
    with index.open() as db:
        rows = db.conn.execute(
            "SELECT txid, number, creator FROM inscription WHERE content_type=? AND number > ? "
            "ORDER BY number LIMIT ?", (CONTENT_TYPE, last, int(limit))).fetchall()
    filed = 0
    for row in rows:
        try:
            found = index.inscription_content(row["txid"])
            body = found[1] if found else b""
        except Exception:
            body = b""
        filed += book.file_batch(row["creator"], network, row["txid"], body)
        last = max(last, int(row["number"]))
    if rows:
        book.set_cursor(network, last)
    return filed
