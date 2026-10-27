"""A name is claimable only on a complete name index, and never while somebody's claim of it waits in the blocks past the
index's walk or in the mempool (2026-10-27; arcade/name_check.py, app._name_check)."""
import base64
import json

import pytest

from arcade import doginal_index as dx
from arcade import name_check, ord_rules, rawchain

from test_doginal_index import FUND, SIG, Chain, _envelope, _tx  # noqa: E402
from test_web import app_state, client  # noqa: F401,E402
from test_funding import _pubkey  # noqa: E402
from test_doginal_wallet import _doge_account  # noqa: E402


def _claim(name, tag=b""):
    body = json.dumps({"p": "dns", "op": "reg", "name": name}).encode()
    return _tx([(FUND, 0, _envelope(b"text/plain;charset=utf-8", ((0, body),)))], [100_000], tag=tag)


class Node(Chain):
    """The miniature node, with a mempool."""

    def __init__(self):
        super().__init__()
        self.pool: list[str] = []

    def wait(self, raw):
        txid = rawchain.read_tx(raw).txid
        self.txs[txid] = raw
        self.pool.append(txid)

    def call(self, method, *params):
        if method == "getrawmempool":
            return list(self.pool)
        return super().call(method, *params)

    def batch(self, calls):
        return [self.call(m, *p) for m, p in calls]


def _made(name):
    return {rule: ord_rules.normalise(rule, "json", name) for rule in ord_rules.NAME_RULES}


@pytest.fixture(autouse=True)
def fresh():
    name_check._BLOCKS.clear()


def test_a_claim_past_the_walk_or_in_the_mempool_is_found():
    node = Node()
    for _ in range(3):
        node.add([])
    node.add([_claim("Wow.doge")])                       # any reading of the same name: upper case is the same name
    tip = node.call("getblockcount")
    assert name_check.newest_claims(node, "doge", tip - 2, tip, _made("wow.doge")) == f"block {tip:,}"
    assert name_check.newest_claims(node, "doge", tip - 2, tip, _made("such.doge")) == ""
    node.wait(_claim("such.doge", tag=b"m")[0])
    assert name_check.newest_claims(node, "doge", tip, tip, _made("such.doge")) == "mempool"
    with pytest.raises(name_check.NameCheckError):
        name_check.newest_claims(node, "doge", tip - name_check.GAP_MAX - 1, tip, _made("x.doge"))


def test_a_plain_payment_claims_nothing():
    raw, _txid = _tx([(FUND, 0, SIG)], [100_000])
    assert name_check.claims_in(rawchain.read_tx(raw), "dogecoin") == []


def _route_setup(app, state, monkeypatch, tmp_path, walked_to, node):
    doge, _ = _doge_account(app, state, monkeypatch, tmp_path)

    class Index:
        def lookup(self, q):
            held = {"wow.doge": {"inscription": "ab" * 32 + "i0", "number": 7, "kind": "json", "output": None}}
            return {"name": {"name": q["name"], "readings": {r: {"winner": held.get(q["name"]), "later_claims": 0}
                                                              for r in ord_rules.NAME_RULES}},
                    "from": "this arcade", "walked_to": walked_to()}
    monkeypatch.setattr(state, "ord_indexes", lambda: {"doge": Index()})
    real_rpc = type(doge).rpc
    monkeypatch.setattr(type(doge), "rpc", lambda self: node if self.network == "doge" else real_rpc(self))
    return doge


def test_names_are_claimable_only_on_a_complete_index(client, monkeypatch, tmp_path):
    app, state = client
    node = Node()
    for _ in range(50):
        node.add([])
    tip = node.call("getblockcount")
    walked = {"to": dx.BASE + 5}
    _route_setup(app, state, monkeypatch, tmp_path, lambda: walked["to"], node)
    ask = lambda name: app.get("/account/ord/name", params={"chain": "doge", "name": name}).json()
    half = ask("free.doge")
    assert half["valid"] and not half["taken"] and not half["ready"]
    assert "once it has read the whole chain" in half["why"]
    walked["to"] = tip - 3
    whole = ask("free.doge")
    assert whole["ready"] and not whole["taken"] and not whole["why"]
    assert ask("wow.doge")["taken"], "claimed in the index"
    node.wait(_claim("soon.doge")[0])
    soon = ask("soon.doge")
    assert soon["taken"] and soon["pending"] == "mempool" and not soon["ready"]


def test_a_claim_of_a_taken_name_is_refused_when_inscribed(client, monkeypatch, tmp_path):
    """The same check where the ord starts, so no page can skip it."""
    app, state = client
    node = Node()
    for _ in range(5):
        node.add([])
    _route_setup(app, state, monkeypatch, tmp_path, lambda: node.call("getblockcount"), node)
    body = {"chain": "doge", "content_type": "text/plain;charset=utf-8", "name": "x",
            "pubkey": _pubkey(int("31" * 32, 16)).hex()}
    claim = lambda name: base64.b64encode(json.dumps({"p": "dns", "op": "reg", "name": name}).encode()).decode()
    taken = app.post("/account/ord/inscribe", json={**body, "content": claim("wow.doge")})
    assert taken.status_code == 400 and "taken" in taken.text
    node.wait(_claim("soon.doge")[0])
    pending = app.post("/account/ord/inscribe", json={**body, "content": claim("soon.doge")})
    assert pending.status_code == 400 and "mempool" in pending.text
