"""Favorite markets (2026-10-17, layout A): the Exchange opens on the markets a reader
starred, beside All markets and + New pair; a star on every market page and every row, saved
per account on this node like a follow."""
import pathlib
import sys

from nacl.signing import SigningKey

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_web import app_state, client                                  # noqa: F401,E402
from test_admin import EDGE, LOCAL, public                              # noqa: F401,E402
from test_auth_web import _sign_in                                      # noqa: E402
from test_asset_pair import COIN, _file, _gold, _order, _side           # noqa: E402

from arcade.web import door                                              # noqa: E402


def test_the_door_lets_an_account_read_and_set_its_own():
    assert door.public_path("/account/favorites")
    assert door.public_path("/account/favorites", "POST")


def test_an_account_stars_and_unstars_and_it_follows_the_account(public):
    app, state = public
    assert _sign_in(app, SigningKey.generate(), headers=EDGE).status_code == 200
    assert app.get("/account/favorites", headers=EDGE).json() == {"favorites": [], "saved": True}
    one, two = "/exchange/pair/14@test", "/exchange/swap/test-coin/main-coin"
    for key in (one, two):
        assert app.post("/account/favorites", headers=EDGE, json={"key": key, "on": True}).status_code == 200
    assert app.get("/account/favorites", headers=EDGE).json()["favorites"] == [two, one], "newest first"
    app.post("/account/favorites", headers=EDGE, json={"key": one, "on": False})
    assert app.get("/account/favorites", headers=EDGE).json()["favorites"] == [two]
    assert app.post("/account/favorites", headers=EDGE, json={"key": "https://elsewhere"}).status_code == 400
    assert state.setting("favorites:node") is None, "an account's stars are its own"


def test_a_stranger_keeps_them_in_the_browser_and_the_operator_on_the_node(public):
    app, state = public
    assert app.get("/account/favorites", headers=EDGE).json() == {"favorites": [], "saved": False}
    assert app.post("/account/favorites", headers=EDGE, json={"key": "/exchange/pair/1@x"}).status_code == 403
    assert app.post("/account/favorites", headers=LOCAL, json={"key": "/exchange/pair/1@x"}).status_code == 200
    assert state.setting("favorites:node") == ["/exchange/pair/1@x"]


def test_the_list_and_the_market_page_star_the_same_market(client, monkeypatch):
    app, state = client
    _gold(monkeypatch)
    net, main = state.messaging.network, state.ledger.network
    _file(state, monkeypatch, [
        _order("ask-0001", _side(net, 10 * COIN, "token", "59"), _side(main, 20 * COIN))])
    key = f"/exchange/swap/{net}-token-59/{main}-coin"
    listing = app.get("/exchange?tab=tokens").text
    assert 'id="mktmode"' in listing and "Favorites" in listing and "+ New pair" in listing
    assert f'data-fav-key="{key}"' in listing and f'data-fav="{key}"' in listing, "every row has its star"
    market = app.get(f"/exchange/swap/{net}-token-59/{main}-coin").text
    assert f'data-fav="{key}"' in market, "and the market page stars the same key"


def test_a_starred_market_stays_listed_with_nothing_on_its_book(client, monkeypatch):
    """2026-10-17: "Once a pair has been favored, it should not be removed from the users list" -- the
    list held only markets with orders or trades, so a starred one vanished when its last order went."""
    app, state = client
    _gold(monkeypatch)
    net, main = state.messaging.network, state.ledger.network
    key = f"/exchange/swap/{net}-token-59/{main}-coin"
    assert app.post("/account/favorites", headers=LOCAL, json={"key": key, "on": True}).status_code == 200
    page = app.get("/exchange?tab=tokens", headers=LOCAL).text
    assert f'data-fav-key="{key}"' in page, "no order on it, and still in the list"
    assert 'id="mktmode"' in page


def test_a_lock_script_market_with_trades_is_listed_with_its_price(client, monkeypatch):
    """2026-10-17: "The pair has activity, but it is not showing on the pair listing" -- a token/DOGE swap
    market lived only on its own page; now the list carries it, with its last trade, like every other market."""
    app, state = client
    _gold(monkeypatch)
    net = state.messaging.network
    state.swap_history().record("tr-0000000000000001", f"{net}-token-59", "doge-coin", 1000 * COIN, 114_000_000,
                                seller="nA", buyer="nB")
    page = app.get("/exchange?tab=tokens").text
    row = page[page.index(f'href="/exchange/swap/{net}-token-59/doge-coin"') - 400:]
    row = row[:row.index("</a>")]
    assert "LOGS" in row and "0.00114" in row, "named, and priced at its last swap"
    assert "1 trade today" in row


def test_the_list_and_the_market_page_say_the_same_last_price(client, monkeypatch):
    """2026-10-17: "Why the pair listing doesn't show the same market price as the pair page?" -- one list of
    trades (app._market_points) prices both, newest first."""
    app, state = client
    _gold(monkeypatch)
    net = state.messaging.network
    h = state.swap_history()
    h.record("tr-0000000000000002", f"{net}-token-59", "doge-coin", 1000 * COIN, 120_000_000, at=1_000)
    h.record("tr-0000000000000003", f"{net}-token-59", "doge-coin", 1000 * COIN, 114_000_000, at=2_000)
    listing = app.get("/exchange?tab=tokens").text
    row = listing[listing.index(f'href="/exchange/swap/{net}-token-59/doge-coin"') - 400:]
    row = row[:row.index("</a>")]
    page = app.get(f"/exchange/swap/{net}-token-59/doge-coin").text
    assert "0.00114" in row and "0.00114" in page, "the newest trade, in both"
    assert "0.0012" not in row.replace("0.00120", "")
