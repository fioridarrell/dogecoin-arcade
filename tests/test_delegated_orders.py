"""Delegated swap orders (2026-10-17: "I want pre-signed partially signed transactions for all orders. So that
orders can be filled without the counterparty being on Arcade"; he chose: the seller's own node finishes it, with a key
for that one order). The seller's side waits at the order key's address; the node answers, locks from there, and claims
the taker's coins to the seller's own address -- with the node's own trading switch off and no page of the seller's open."""

import pathlib
import secrets
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_htlc import doge                                            # noqa: E402,F401
from test_node_trader import _mine, _scan                             # noqa: E402
from test_trade_tab_browser import _catch_up, world                   # noqa: E402,F401

from arcade import trade as T                                         # noqa: E402
from arcade.node_trader import HeldKey                                # noqa: E402
from arcade.script import b58check_encode, hash160                    # noqa: E402

COIN = 100_000_000


def test_a_delegated_order_fills_with_the_seller_away(world):
    base, a, b, pep, dg = world
    seller_node = a.node_trader
    seller_node.enabled = lambda: False                               # the operator's own trading is off
    secret = f"{secrets.randbelow(2 ** 250) + 1:064x}"
    held = HeldKey(secret)
    deposit = b58check_encode(a.doge.params.pubkeyhash_version, hash160(held.pubkey))
    payout = pep.call("getnewaddress")                               # the seller's own PEP address
    dg.call("sendtoaddress", deposit, 2.2)                           # 2 DOGE for the order, 0.2 for its fees
    _mine(dg)
    _catch_up(a)
    key = seller_node.delegate("order-away", {"network": "doge", "kind": "coin", "amount": 2 * COIN},
                               {"network": "regtest", "kind": "coin", "amount": 3 * COIN},
                               secret, {"doge": deposit}, {"regtest": payout}, owner="seller")
    assert key == held.pubkey
    from arcade import trade_courier
    trade_courier.mesh_inbox(a)                                      # the seller's node is on the mesh

    give = b.leg("regtest", 3 * COIN, key)                           # the taker gives 3 PEP
    get = T.Leg("doge", "coin", "", 2 * COIN, key.hex(), b.key.pubkey.hex())
    trade = b.propose(a.identity.public_bytes, "order-away", give, get)
    log = []
    for _ in range(40):
        for t in (b, seller_node):
            _scan(t)
            _catch_up(t.state)
            log.extend(t.tick())
        _mine(pep, dg)
        if b.book.get(trade.id).state == "done" and (seller_node.book.get(trade.id) or trade).state == "done":
            break
    assert b.book.get(trade.id).state == "done", log[-6:]
    assert seller_node.book.get(trade.id).state == "done", log[-6:]
    got = sum(u["amount"] for u in pep.call("listunspent", 0, 9999999, [payout]))
    assert got >= 2.98, f"the seller's 3 PEP (less the claim fee) at its own address: {got}"
    assert seller_node.delegated("order-away")["state"] == "filled", "taken whole, off the book"


def test_a_delegated_token_order_fills_with_the_seller_away(world):
    """The seller's GOLD for the taker's DOGE: the token waits at the order key's address with a little coin for its
    lock; the node locks it there with one OP_RETURN, and the DOGE goes to the seller's own Dogecoin address."""
    from test_trade_tab_lists import _gold_everywhere
    base, a, b, pep, dg = world
    seller_node = a.node_trader
    seller_node.enabled = lambda: False
    secret = f"{secrets.randbelow(2 ** 250) + 1:064x}"
    held = HeldKey(secret)
    deposit = b58check_encode(a.messaging.params.pubkeyhash_version, hash160(held.pubkey))
    pep.call("sendtoaddress", deposit, 1)                             # the lock's reserve and fees
    _mine(pep)
    _gold_everywhere(a, b, deposit, 100 * COIN)                       # the seller's GOLD at the order key's address
    _catch_up(a)
    payout = dg.call("getnewaddress")
    key = seller_node.delegate("order-gold-away",
                               {"network": "regtest", "kind": "token", "asset": "150", "amount": 40 * COIN},
                               {"network": "doge", "kind": "coin", "amount": 2 * COIN},
                               secret, {"regtest": deposit}, {"doge": payout}, owner="seller")
    from arcade import trade_courier
    trade_courier.mesh_inbox(a)
    give = b.leg("doge", 2 * COIN, key)
    get = T.Leg("regtest", "token", "150", 40 * COIN, key.hex(), b.key.pubkey.hex())
    trade = b.propose(a.identity.public_bytes, "order-gold-away", give, get)
    log = []
    for _ in range(40):
        for t in (b, seller_node):
            _scan(t)
            _catch_up(t.state)
            log.extend(t.tick())
        _mine(pep, dg)
        if b.book.get(trade.id).state == "done" and (seller_node.book.get(trade.id) or trade).state == "done":
            break
    assert b.book.get(trade.id).state == "done", log[-6:]
    assert seller_node.book.get(trade.id).state == "done", log[-6:]
    got = sum(u["amount"] for u in dg.call("listunspent", 0, 9999999, [payout]))
    assert got >= 1.98, f"the seller's 2 DOGE (less the claim fee) at its own address: {got}"
