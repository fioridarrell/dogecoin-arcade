"""The trade state machine: one swap, two traders, every step decided from the chains.

The plan is ~/arcade-work/trustless-trading-plan.md (2026-10-13: trades "100%
trustless with lock scripts"; a facilitating node "just like a regular lock script that
the client side does"). This file is the brain both kinds of trader run -- a browser
tab (ported to JS) and a facilitating node -- so the two can never disagree about
what is safe.

A trade is two LEGS: what the initiator gives, on its chain, and what the responder
gives, on its chain (the same chain or another). The INITIATOR makes the secret.

    initiator locks its leg     (lock time T1, the longer one)
    responder checks that lock, then locks its own leg   (T2, well before T1)
    initiator checks that lock, then CLAIMS it -- the secret is now on that chain
    responder reads the secret there and claims the initiator's lock before T1
    either side, if the other never moves, refunds its own lock after its own time

`next_step(trade, facts, now)` is a pure function: given what the chains show (the
`Facts`), it returns the one thing this trader should do next. Nothing in here
trusts a message from the other side -- a message only says where to look; every
decision is made on what is confirmed on-chain, checked against the agreed terms.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import htlc as H

INITIATOR, RESPONDER = "initiator", "responder"

#: How long the initiator's lock stands, in seconds. The responder's stands half as
#: long, so after the initiator claims (revealing the secret) the responder always has
#: at least half the window to claim the other side.
LOCK_SECONDS = {"mainnet": 48 * 3600, "testnet": 6 * 3600}
#: Confirmations a lock needs before the other side acts on it.
CONFIRMATIONS = {"mainnet": 3, "testnet": 1}
#: Never claim the responder's lock when less than this is left on it: the responder
#: must still have time to see the secret and claim the initiator's lock before T1.
CLAIM_MARGIN = 3600
#: Never start a lock with less than this left before the other lock's refund opens.
LOCK_MARGIN = 2 * 3600

# The steps next_step can answer.
LOCK, WAIT, CLAIM, REFUND, DONE, ABORT = "lock", "wait", "claim", "refund", "done", "abort"


@dataclasses.dataclass
class Leg:
    """One side of a trade: what moves, on which chain, from whom to whom."""
    network: str                 # "test", "main", "doge", "regtest"...
    kind: str                    # "coin", "token", "nft"
    asset: str                   # "" for coin, property id for a token, txid for an NFT
    amount: int                  # sats for coin, units for a token, 1 for an NFT
    giver_key: str               # hex pubkey of whoever locks it (refunds to them)
    taker_key: str               # hex pubkey of whoever claims it

    def tier(self) -> str:
        return "mainnet" if self.network in ("main", "doge") else "testnet"


@dataclasses.dataclass
class Trade:
    id: str
    role: str                    # INITIATOR or RESPONDER, from this trader's side
    give: Leg                    # what THIS trader locks
    get: Leg                     # what THIS trader claims
    secret_hash: str             # hex
    secret: str = ""             # hex; the initiator's from the start, the responder's once read
    initiator_locktime: int = 0
    responder_locktime: int = 0
    state: str = "proposed"
    note: str = ""
    created: float = dataclasses.field(default_factory=time.time)

    @property
    def my_locktime(self) -> int:
        return self.initiator_locktime if self.role == INITIATOR else self.responder_locktime

    @property
    def their_locktime(self) -> int:
        return self.responder_locktime if self.role == INITIATOR else self.initiator_locktime

    def my_redeem(self) -> bytes:
        return H.redeem_script(bytes.fromhex(self.secret_hash), bytes.fromhex(self.give.taker_key),
                               bytes.fromhex(self.give.giver_key), self.my_locktime)

    def their_redeem(self) -> bytes:
        return H.redeem_script(bytes.fromhex(self.secret_hash), bytes.fromhex(self.get.taker_key),
                               bytes.fromhex(self.get.giver_key), self.their_locktime)


#: A token or NFT lock carries this much coin in its lock output besides the asset: it
#: pays for the one transaction that moves the asset out again, claim or refund
#: (htlc.move_out), so neither side has to bring coin of its own to finish.
LOCK_RESERVE = H.SPEND_FEE + H.DUST


def lock_value(leg: Leg) -> int:
    """The coin a lock output for `leg` must hold: the amount for coin, the reserve else."""
    return leg.amount if leg.kind == "coin" else LOCK_RESERVE


def move_data(leg: Leg) -> bytes:
    """The OP_RETURN data that moves a token or NFT leg out of its lock (claim or refund)."""
    from . import escrow as E
    if leg.kind == "token":
        return E.token_payload(int(leg.asset), leg.amount)
    if leg.kind == "nft":
        return E.nft_payload(leg.asset)
    raise ValueError("a coin leg moves no asset")


def asset_at(index: Any, leg: Leg, address: str, lock_height: int | None) -> bool | None:
    """Does the ledger show `leg`'s token or NFT at the lock `address`? None when the
    ledger has not yet read the lock's block (`lock_height`, None while unconfirmed):
    a lagging index must make a lock look not-yet-there, never wrong -- a wrong lock is
    walked away from (next_step ABORT)."""
    if leg.kind == "coin":
        return True
    if lock_height is None:
        return None
    with index.open() as db:
        tip = db.tip()
    if tip is None or int(tip["height"]) < int(lock_height):
        return None
    if leg.kind == "token":
        return int(index.balance(address, int(leg.asset))) >= leg.amount
    row = index.inscription(leg.asset)
    return bool(row) and row["owner"] == address


@dataclasses.dataclass
class LockSeen:
    """A lock as a chain shows it: where, how much, how deep, and whether it was spent."""
    txid: str
    vout: int
    value: int                   # coin in the lock output (sats)
    script_hex: str              # the output's scriptPubKey
    confirmations: int
    asset_ok: bool = True        # the ledger shows the token/NFT at the lock address (coin: True)
    spent_by_script_sig: str = ""   # hex input script of the spend, if it was spent
    spent_by_me: bool = False


@dataclasses.dataclass
class Facts:
    """What this trader's chains show right now, for both locks."""
    mine: LockSeen | None = None
    theirs: LockSeen | None = None


def plan_locktimes(give: Leg, get: Leg, role: str, now: int) -> tuple[int, int]:
    """(initiator's T1, responder's T2) for a trade starting now. The tier of a cross
    chain trade is the stricter of the two (any mainnet leg -> mainnet times)."""
    tier = "mainnet" if "mainnet" in (give.tier(), get.tier()) else "testnet"
    t1 = int(now) + LOCK_SECONDS[tier]
    t2 = int(now) + LOCK_SECONDS[tier] // 2
    return t1, t2


def check_lock(trade: Trade, seen: LockSeen, theirs: bool) -> str:
    """"" if `seen` is exactly the lock the terms say, else why not."""
    leg = trade.get if theirs else trade.give
    redeem = trade.their_redeem() if theirs else trade.my_redeem()
    if seen.script_hex != H.p2sh_script(redeem).hex():
        return "that lock's script is not the one agreed (hash, keys or lock time differ)"
    if seen.value < lock_value(leg):
        if leg.kind == "coin":
            return f"that lock holds {seen.value}, less than the {leg.amount} agreed"
        return "that lock holds too little coin to move its token or NFT out again"
    if leg.kind != "coin" and not seen.asset_ok:
        return "the ledger does not show the agreed token or NFT at that lock"
    return ""


def next_step(trade: Trade, facts: Facts, now: int) -> tuple[str, str]:
    """(step, why). The one thing this trader should do next, from the chains alone."""
    need = max(CONFIRMATIONS[trade.give.tier()], CONFIRMATIONS[trade.get.tier()])
    mine, theirs = facts.mine, facts.theirs

    # Finished: I claimed theirs (the trade went through), or mine came back to me.
    if theirs is not None and theirs.spent_by_me:
        return DONE, "I claimed their lock"
    if mine is not None and mine.spent_by_me:
        return DONE, "my lock came back to me (refunded)"

    # The other lock, if it is there, must be exactly what was agreed.
    if theirs is not None and not theirs.spent_by_script_sig:
        bad = check_lock(trade, theirs, theirs=True)
        if bad:
            if mine is not None and not mine.spent_by_script_sig:
                return (REFUND, bad) if now >= trade.my_locktime else (WAIT, bad + "; waiting to refund mine")
            return ABORT, bad

    if trade.role == INITIATOR:
        if mine is None:
            return LOCK, "I lock first"
        if theirs is None or theirs.confirmations < need:
            if now >= trade.my_locktime:
                return REFUND, "the other side never locked; my lock time has passed"
            return WAIT, "waiting for the other lock to confirm"
        if theirs.spent_by_script_sig:
            return WAIT if now < trade.my_locktime else REFUND, "their lock went back to them"
        if now < trade.their_locktime - CLAIM_MARGIN:
            return CLAIM, "their lock is confirmed and agreed: claim it (this reveals the secret)"
        if now >= trade.my_locktime:
            return REFUND, "too late to claim safely; my lock time has passed"
        return WAIT, "too close to their lock time to claim safely; waiting to refund"

    # RESPONDER
    if theirs is None or theirs.confirmations < need:
        if mine is not None and now >= trade.my_locktime:
            return REFUND, "my lock time has passed"
        return WAIT, "waiting for the initiator's lock to confirm"
    if mine is None:
        if now >= trade.their_locktime - LOCK_MARGIN or now >= trade.responder_locktime - LOCK_MARGIN:
            return ABORT, "too little time left to lock safely"
        return LOCK, "their lock is confirmed and agreed: lock mine"
    # Both locked. Has the initiator claimed mine (revealing the secret)?
    if mine.spent_by_script_sig and not mine.spent_by_me:
        secret = H.secret_from_script_sig(bytes.fromhex(mine.spent_by_script_sig), trade.my_redeem())
        if secret is None:
            return WAIT, "my lock was spent without the secret?"
        trade.secret = secret.hex()
        if now < trade.their_locktime:
            return CLAIM, "the secret is on the chain: claim their lock"
        return ABORT, "the secret came too late to claim their lock"
    if now >= trade.my_locktime:
        return REFUND, "the initiator never claimed; my lock time has passed"
    return WAIT, "waiting for the initiator to claim (that reveals the secret)"


# --- persistence --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade (
    id      TEXT PRIMARY KEY,
    body    TEXT NOT NULL,
    state   TEXT NOT NULL,
    updated REAL NOT NULL
);
"""


class Book:
    """This trader's trades, kept so a restart never forgets a lock (or its secret)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as conn:
            conn.executescript(SCHEMA)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, trade: Trade) -> None:
        body = json.dumps(dataclasses.asdict(trade))
        with self._open() as conn:
            conn.execute("INSERT INTO trade(id, body, state, updated) VALUES(?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET body=excluded.body, "
                         "state=excluded.state, updated=excluded.updated",
                         (trade.id, body, trade.state, time.time()))

    def get(self, trade_id: str) -> Trade | None:
        with self._open() as conn:
            row = conn.execute("SELECT body FROM trade WHERE id=?", (trade_id,)).fetchone()
        return _load(row["body"]) if row else None

    def open_trades(self) -> list[Trade]:
        with self._open() as conn:
            rows = conn.execute("SELECT body FROM trade WHERE state NOT IN ('done','aborted')").fetchall()
        return [_load(r["body"]) for r in rows]


def _load(body: str) -> Trade:
    said: dict[str, Any] = json.loads(body)
    said["give"], said["get"] = Leg(**said["give"]), Leg(**said["get"])
    return Trade(**said)
