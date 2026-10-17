"""Message outputs come back by themselves (2026-10-17, of "Sweep back 400 of 2412":
"Can this be automatically swept back?"): wallet.autoSweep, run from every arcade page while
the wallet is open, signs the Wallet's own sweep without asking -- and only a sweep that pays
nobody but this wallet."""

import pathlib
import sys

import pytest

pytest.importorskip("playwright.sync_api", reason="needs playwright")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_htlc import doge                                            # noqa: E402,F401
from test_trade_tab_browser import _mine, _scan_a, world              # noqa: E402,F401

COIN = 100_000_000


class _Mesh:
    node_id = "ab" * 32

    def online(self, node_id):
        return node_id == self.node_id


def test_an_open_wallet_sweeps_its_message_outputs_back_by_itself(world, monkeypatch):
    from playwright.sync_api import sync_playwright
    base, a, _b, pep, _dg = world
    monkeypatch.setattr(a, "mesh", _Mesh(), raising=False)
    net = {"network": "regtest", "version": 111}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page()
        tab.goto(f"{base}/join")
        tab.evaluate("async () => { window.w = await import('/wallet.js'); }")
        made = tab.evaluate("""async (net) => {
            const r = await w.signUp('sweeper' + Math.floor(Math.random() * 1e6), 'a long enough password', net);
            return r.wallet.on.regtest.address; }""", net)
        pep.call("sendtoaddress", made, 10)
        _mine(pep)
        _scan_a(a)
        # An order batch is written from the account in Class B: it leaves payload outputs.
        tab.evaluate("""async ([net, give, get]) => { await w.listOrder(await w.opened(net), give, get); }""",
                     [net, {"network": "regtest", "amount": 3 * COIN}, {"network": "doge", "amount": 2 * COIN}])
        _mine(pep)
        _scan_a(a)
        dust = tab.evaluate("async () => (await (await fetch('/account/dust')).json()).chains")
        assert dust and dust[0]["count"] > 0, dust
        tab.evaluate("() => { globalThis.ARCADE_SWEEP_MIN = 1; localStorage.removeItem('arcade-autosweep'); }")
        done = tab.evaluate("async () => await w.autoSweep()")
        assert len(done) == 1 and done[0]["count"] == dust[0]["count"], done
        assert tab.evaluate("async () => (await w.autoSweep()).length") == 0, "not again before the gap"
        _mine(pep)
        _scan_a(a)
        left = tab.evaluate("async () => (await (await fetch('/account/dust')).json()).chains")
        assert not left, left
        browser.close()
