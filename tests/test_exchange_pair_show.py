"""The Tokens tab's chips (2026-10-13: "Add a mix, testnet only, mainnet
only, and cross chain only Sort buttons to the token exchange")."""

import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client                                 # noqa: F401,E402
from fractions import Fraction                                         # noqa: E402

COIN = 100_000_000


def _markets(client, monkeypatch):
    """A token pair on every indexed chain, and one open cross-chain market."""
    app, state = client
    import arcade.ledger as L
    props = {7: {"property_id": 7, "name": "LOGS", "divisible": True, "managed": 0},
             8: {"property_id": 8, "name": "GOLD", "divisible": True, "managed": 0},
             59: {"property_id": 59, "name": "WOOD", "divisible": True, "managed": 0}}
    rest = {"txid": "e" * 64, "block_height": 1, "position": 0, "address": "x",
            "base": 5 * COIN, "quote": 9 * COIN, "price": Fraction(9, 5)}
    monkeypatch.setattr(L.LedgerIndex, "token_pairs", lambda self: [(7, 8)], raising=False)
    monkeypatch.setattr(L.LedgerIndex, "pair_trades", lambda self, a, b, limit=500: [], raising=False)
    monkeypatch.setattr(L.LedgerIndex, "pair_book", lambda self, a, b, limit=50: {"asks": [dict(rest)], "bids": []},
                        raising=False)
    real_property = L.LedgerIndex.property
    monkeypatch.setattr(L.LedgerIndex, "property", lambda self, pid: props.get(pid) or real_property(self, pid))
    monkeypatch.setattr(type(state.xchain), "address", lambda self, which: f"n{which}Exchange")
    book = state.xchain.book
    o = book.place(owner="x", side="sell", kind="token", asset="59", amount=10 * COIN, pepe=5 * COIN,
                   pay_test="nT", pay_main="PM")
    book.deposited(o["id"], "dep1")
    book.count_deposit(o["id"])
    return app, state


def _nets(page: str) -> list[str]:
    return re.findall(r'class="ex-mrow"[^>]*data-net="(\w+)"', page)


def test_mix_lists_both_chains_and_the_cross_chain_book(client, monkeypatch):
    app, state = _markets(client, monkeypatch)
    page = app.get("/exchange?tab=tokens").text
    for label in ("Mix", "Testnet only", "Mainnet only", "Cross chain only"):
        assert f">{label}</a>" in page, label
    assert 'href="/exchange?tab=tokens" aria-current="true">Mix' in page, "Mix is the default"
    assert sorted(set(_nets(page))) == ["cross", "mainnet", "testnet"], _nets(page)
    assert "/exchange/x/token/59" in page


def test_each_chip_shows_only_its_own_markets(client, monkeypatch):
    app, state = _markets(client, monkeypatch)
    for show in ("testnet", "mainnet", "cross"):
        page = app.get(f"/exchange?tab=tokens&show={show}").text
        assert set(_nets(page)) == {show}, (show, _nets(page))
        assert f'show={show}" aria-current="true"' in page, show
    cross = app.get("/exchange?tab=tokens&show=cross").text
    assert "/exchange/x/token/59" in cross and "/exchange/pairs/7/8" not in cross
    assert "/exchange/x/token/59" not in app.get("/exchange?tab=tokens&show=mainnet").text
    assert set(_nets(app.get("/exchange?tab=tokens&show=nonsense").text)) == {"cross", "mainnet", "testnet"}, \
        "an unknown chip is the mix"


def test_a_row_on_the_other_chain_switches_before_it_opens(client, monkeypatch):
    """A pair page reads the browser's chain, so a row from the other chain has to
    switch it first or it opens the same number on the wrong chain."""
    app, state = _markets(client, monkeypatch)
    here = state.token_chain
    other = next(c for c in state.token_chains if c is not here)
    other_net = "mainnet" if other.is_mainnet else "testnet"
    page = app.get("/exchange?tab=tokens").text
    rows = re.findall(r'<a class="ex-mrow"[^>]*>', page)
    theirs = [r for r in rows if f'data-net="{other_net}"' in r]
    ours = [r for r in rows if f'data-net="{"mainnet" if here.is_mainnet else "testnet"}"' in r]
    assert theirs and ours
    assert all(f'data-switch="{other.network}"' in r and 'data-back="/exchange/pairs/7/8"' in r for r in theirs), theirs
    assert not any("data-switch" in r for r in ours), "a row on this page's chain opens as it is"
    assert 'id="pairswitch"' in page and 'action="/tokens/chain"' in page
