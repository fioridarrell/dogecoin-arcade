"""The Exchange's "Swaps between arcades" (2026-10-13, trustless-trading-plan.md phases 4-5):
orders every arcade inscribed, shown while their node is online, taken by this node's own
lock-script trader -- and only while the operator has node-facilitated trades on."""

import pathlib
import secrets
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client                               # noqa: F401,E402

from arcade import trade_orders as O                                  # noqa: E402

COIN = 100_000_000
NODE = "ab" * 32


def _file(state, oid="order-0001", node=NODE):
    order = {"id": oid, "give": {"network": "main", "kind": "coin", "asset": "", "amount": 2 * COIN},
             "get": {"network": "regtest", "kind": "coin", "asset": "", "amount": 3 * COIN},
             "trade_key": "02" + secrets.token_hex(32), "msg_key": secrets.token_hex(32),
             "node": node, "expires": int(time.time()) + 3600}
    state.trade_orders.file_batch("nListerAddressXXXXXXXXXXXXXXXXXXXXX", "regtest", "aa" * 32,
                                  O.batch_json([order]))


class _Mesh:
    node_id = "cd" * 32

    def __init__(self, up):
        self.up = set(up)

    def online(self, node_id):
        return node_id in self.up


def test_other_arcades_orders_show_while_their_node_is_online(client, monkeypatch):
    app, state = client
    _file(state)
    monkeypatch.setattr(state, "mesh", _Mesh({NODE}), raising=False)
    page = app.get("/exchange?tab=tokens").text
    assert "Swaps between arcades" in page and 'action="/exchange/trade-orders/take"' in page
    assert "Post swap" in page, "the operator may post one (node-facilitated trades on)"
    monkeypatch.setattr(state, "mesh", _Mesh(set()), raising=False)
    page = app.get("/exchange?tab=tokens").text
    assert 'name="order" value="order-0001"' not in page, "its arcade went offline: not shown"


def test_the_operator_posts_a_swap(client, monkeypatch):
    app, state = client
    monkeypatch.setattr(state, "mesh", None, raising=False)
    said = app.post("/exchange/trade-orders/stand", data={
        "give_network": "regtest", "give_amount": "3", "get_network": "main", "get_amount": "2",
        "csrf_token": state.csrf_token}, follow_redirects=False)
    assert said.status_code == 303
    standing = state.node_trader.standing()
    assert len(standing) == 1 and standing[0]["give"] == {"network": "regtest", "kind": "coin",
                                                          "asset": "", "amount": 3 * COIN}


@pytest.mark.node_trades_default
def test_with_the_switch_off_the_node_neither_posts_nor_takes(client, monkeypatch):
    app, state = client
    _file(state)
    monkeypatch.setattr(state, "mesh", _Mesh({NODE}), raising=False)
    page = app.get("/exchange?tab=tokens").text
    assert "Swaps between arcades" in page, "the orders still show"
    assert "Post swap" not in page and 'action="/exchange/trade-orders/take"' not in page
    app.post("/exchange/trade-orders/stand", data={
        "give_network": "regtest", "give_amount": "3", "get_network": "main", "get_amount": "2",
        "csrf_token": state.csrf_token}, follow_redirects=False)
    assert state.node_trader.standing() == []
