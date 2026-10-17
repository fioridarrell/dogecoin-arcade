"""Stars on the Wallet's tokens, favorites on top (2026-10-17). A token's star is its
market's favorite, so starring it in the wallet stars its market on the Exchange too."""

import pathlib
import sys

import pytest

pytest.importorskip("playwright.sync_api", reason="needs playwright")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_htlc import doge                                            # noqa: E402,F401
from test_trade_tab_browser import _mine, _scan_a, world              # noqa: E402,F401

COIN = 100_000_000


def test_a_starred_token_goes_on_top_and_stars_its_market(world):
    from playwright.sync_api import sync_playwright
    from test_account_tokens import _balance, _token
    base, a, _b, pep, _dg = world
    net = {"network": "regtest", "version": 111}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page(viewport={"width": 430, "height": 900})
        tab.goto(f"{base}/join")
        tab.evaluate("async () => { window.w = await import('/wallet.js'); }")
        made = tab.evaluate("""async (net) => {
            const r = await w.signUp('starrer' + Math.floor(Math.random() * 1e6), 'a long enough password', net);
            return r.wallet.on.regtest.address; }""", net)
        pep.call("sendtoaddress", made, 1)
        _mine(pep)
        _scan_a(a)
        for pid, name in ((150, "Apples"), (151, "Bread"), (152, "Cheese")):
            _token(a, property_id=pid, name=name)
            _balance(a, made, pid, 10 * COIN)
        tab.goto(f"{base}/me/wallet")
        # The wallet opens on the chain it showed last: the test chain here.
        tab.evaluate("""() => { for (const k of Object.keys(localStorage)) if (k.startsWith('arcade.wallet.net.'))
            localStorage.removeItem(k);
            localStorage.setItem('arcade.wallet.net.' + ((typeof ARCADE_ME === 'string' && ARCADE_ME) || 'me'), JSON.stringify('regtest')); }""")
        tab.reload()
        tab.click("#tab-tokens")
        tab.wait_for_selector("#panel-tokens .wl-tokrow")
        names = lambda: tab.eval_on_selector_all(  # noqa: E731
            "#panel-tokens .wl-tok .wl-mid b", "els => els.map(e => e.textContent)")
        assert names() == ["Apples", "Bread", "Cheese"]
        tab.click('#panel-tokens [data-fav="/exchange/pair/152@regtest"]')
        tab.wait_for_function("() => document.querySelector('#panel-tokens .wl-mid b').textContent === 'Cheese'")
        assert names() == ["Cheese", "Apples", "Bread"], "the starred one on top"
        assert tab.eval_on_selector_all("#panel-tokens .wl-favhead", "els => els.map(e => e.textContent)") == ["Favorites", "Other tokens"]
        saved = tab.evaluate("async () => (await (await fetch('/account/favorites')).json()).favorites")
        assert saved == ["/exchange/pair/152@regtest"], "the same key the Exchange stars its market by"
        browser.close()
