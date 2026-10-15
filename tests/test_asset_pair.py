"""Cross-chain asset markets through lock scripts (2026-10-15: pairs like ASHVALE
GOLD/PEP "should automatically be switched over to our new non-custodial swap mechanism"):
a testnet token or piece against mainnet PEP, out of the swaps every arcade lists, priced in
PEP per whole unit, each taken whole. The node-held book stays behind ?held=1."""

import pathlib
import re
import secrets
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client                               # noqa: F401,E402
from test_trade_orders_web import NODE, _Mesh                         # noqa: E402

from arcade import trade_orders as O                                  # noqa: E402

COIN = 100_000_000


def _side(network, amount, kind="coin", asset=""):
    return {"network": network, "kind": kind, "asset": asset, "amount": amount}


def _order(oid, give, get):
    return {"id": oid, "give": give, "get": get,
            "trade_key": "02" + secrets.token_hex(32), "msg_key": secrets.token_hex(32),
            "node": NODE, "expires": int(time.time()) + 3600}


def test_the_book_prices_a_token_in_pep_per_whole_unit():
    gold = lambda n: _side("test", n, "token", "26")                  # noqa: E731
    orders = [
        {**_order("ask-dear", gold(10 * COIN), _side("main", 30 * COIN)), "lister": "x"},   # 3.0
        {**_order("ask-cheap", gold(100 * COIN), _side("main", 200 * COIN)), "lister": "x"},  # 2.0
        {**_order("bid-high", _side("main", 15 * COIN), gold(10 * COIN)), "lister": "x"},   # 1.5
        {**_order("bid-low", _side("main", 1 * COIN), gold(1 * COIN)), "lister": "x"},      # 1.0
        {**_order("other-token", _side("test", COIN, "token", "27"), _side("main", COIN)), "lister": "x"},
        {**_order("doge", gold(COIN), _side("doge", COIN)), "lister": "x"},
        {**_order("coins", _side("test", COIN), _side("main", COIN)), "lister": "x"},
    ]
    book = O.asset_book(orders, "test", "token", "26", "main", divisible=True)
    assert [o["id"] for o in book["asks"]] == ["ask-cheap", "ask-dear"]
    assert [o["id"] for o in book["bids"]] == ["bid-high", "bid-low"]
    assert book["asks"][0]["price"] == 2.0 and book["bids"][0]["price"] == 1.5
    assert book["spread"] == 0.5
    assert book["bids"][0]["base_amount"] == 10 * COIN and book["bids"][0]["quote_amount"] == 15 * COIN
    whole = O.asset_book([{**_order("w", _side("test", 4, "token", "9"), _side("main", 2 * COIN)), "lister": "x"}],
                         "test", "token", "9", "main", divisible=False)
    assert whole["asks"][0]["price"] == 0.5, "an indivisible token: 2 PEP for 4 units"
    assert O.asset_markets(orders, "test", "main") == {("token", "26"): 4, ("token", "27"): 1}


def test_a_piece_is_priced_for_itself():
    piece = "ab" * 32
    orders = [{**_order("sell", _side("test", 1, "nft", piece), _side("main", 7 * COIN)), "lister": "x"},
              {**_order("offer", _side("main", 5 * COIN), _side("test", 1, "nft", piece)), "lister": "y"}]
    book = O.asset_book(orders, "test", "nft", piece, "main", divisible=False)
    assert book["asks"][0]["price"] == 7.0 and book["bids"][0]["price"] == 5.0


def _gold(monkeypatch):
    import arcade.ledger as L
    monkeypatch.setattr(L.LedgerIndex, "property", lambda self, pid: {
        "property_id": 59, "name": "LOGS", "divisible": True, "managed": 0} if int(pid) == 59 else None)


def _file(state, monkeypatch, orders):
    net = state.messaging.network
    batch = O.batch_json(orders)
    state.trade_orders.file_batch("nListerAddressXXXXXXXXXXXXXXXXXXXXX", net, "aa" * 32, batch)
    monkeypatch.setattr(state, "mesh", _Mesh({NODE}), raising=False)


def test_the_market_page_is_lock_scripts_and_lists_the_swaps(client, monkeypatch):
    app, state = client
    _gold(monkeypatch)
    net, main = state.messaging.network, state.ledger.network
    _file(state, monkeypatch, [
        _order("ask-0001", _side(net, 10 * COIN, "token", "59"), _side(main, 20 * COIN)),
        _order("bid-0001", _side(main, 3 * COIN), _side(net, 2 * COIN, "token", "59")),
    ])
    page = app.get("/exchange/x/token/59").text
    assert "Lock scripts" in page and "Held by this node" not in page and "Not trustless" not in page
    assert "Selling LOGS" in page and "Buying LOGS" in page
    assert re.search(r'cp-ask">2(\.0+)?<', page) and re.search(r'cp-bid">1\.5', page), "PEP per LOGS"
    assert "not open on this node" not in page
    held = app.get("/exchange/x/token/59?held=1").text
    assert "Held by this node" in held, "the old book is still there for what stands on it"
    assert app.get("/exchange/x/coin/coin", follow_redirects=False).headers["location"] \
        == f"/exchange/swap/{net}-coin/{main}-coin", "testnet coins are a coin pair"

    listing = app.get("/exchange?tab=tokens").text
    assert f"/exchange/swap/{net}-token-59/{main}-coin" in listing, "a token with swaps standing is a row of the table"


def test_the_operator_posts_a_bid_for_a_token_it_does_not_hold(client, monkeypatch):
    """Buying is getting the token: the node's home address need not hold it yet."""
    app, state = client
    _gold(monkeypatch)
    state.set_setting("node_trades", True)
    stood = []
    monkeypatch.setattr(type(state.node_trader), "stand",
                        lambda self, oid, give, get: stood.append((give, get)), raising=False)
    monkeypatch.setattr(state, "mesh", None, raising=False)
    net, main = state.messaging.network, state.ledger.network
    page = app.get("/exchange/x/token/59").text
    assert f'name="get_network" value="token:{net}:59:1"' in page, "the Buy form gets the token"
    went = app.post("/exchange/trade-orders/stand", data={
        "give_network": main, "give_amount": "3", "get_network": f"token:{net}:59:1", "get_amount": "2",
        "back": "/exchange/x/token/59", "csrf_token": state.csrf_token}, follow_redirects=False)
    assert went.headers["location"] == "/exchange/x/token/59"
    page = app.get("/exchange/x/token/59").text
    assert stood, re.findall(r'class="flash[^"]*"[^>]*>(.*?)<', page) or re.findall(r'class="msg err"[^>]*>(.*?)<', page)
    assert stood == [({"network": main, "kind": "coin", "asset": "", "amount": 3 * COIN},
                      {"network": net, "kind": "token", "asset": "59", "amount": 2 * COIN})]


def test_one_picker_opens_every_pair(client, monkeypatch):
    """2026-10-15: "I want all pairs to be established the same way on the clients page
    whether it is a pairing on one chain or cross chains. Every doge/pep and doge/test pep and
    pep/test and tokens." One box: two chains open their swap market (base first, whichever
    side was picked first), one chain opens that chain's own instant market."""
    app, state = client
    _gold(monkeypatch)
    net, main = state.messaging.network, state.ledger.network
    go = lambda b, q: app.get(f"/exchange/pairs?base={b}&quote={q}", follow_redirects=False).headers["location"]  # noqa: E731
    assert go(f"{main}-coin", f"{net}-coin") == f"/exchange/swap/{net}-coin/{main}-coin", "PEP TEST/PEP, either way round"
    assert go(f"{net}-coin", f"{main}-coin") == f"/exchange/swap/{net}-coin/{main}-coin"
    assert go(f"{main}-coin", f"{net}-token-59") == f"/exchange/swap/{net}-token-59/{main}-coin", "the token is the base"
    state.switch_token_chain(net)
    assert go(f"{net}-token-59", f"{net}-coin") == "/exchange/pair/59", "one chain: its own instant market"
    assert go(f"{net}-coin", f"{net}-coin").startswith("/exchange?tab=tokens"), "a pair is two things"
    assert go("59", "coin") == "/exchange/pair/59", "the old values still work"
    page = app.get("/exchange?tab=tokens").text
    for key in (f"{net}-coin", f"{main}-coin"):
        assert f'value="{key}"' in page, key
    assert 'action="/exchange/pairs"' in page and 'name="csrf_token"' in page, "the operator's box is a form"
    assert f"/exchange/swap/{net}-coin/{main}-coin" in page, "every two chains' coin pair is listed"
    swap = app.get(f"/exchange/swap/{net}-coin/{main}-coin").text
    assert "Lock scripts" in swap and "PEP TEST" in swap
    assert app.get(f"/exchange/swap/{main}-coin/{main}-coin").status_code == 404
    assert app.get(f"/exchange/swap/{net}-token-59/{net}-coin", follow_redirects=False).headers["location"] \
        == "/exchange/pair/59", "a one-chain pair is its chain's own market"
