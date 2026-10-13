"""A browser tab LISTS a swap, and another arcade's node takes it -- end to end.

2026-10-13: orders are "visible to other node operators as long as that node is
online. They can be inscribed like any other order". A person on node A lists 3 PEP for
2 DOGE from their tab (wallet.listOrder): the tab inscribes the order batch from its own
account, naming a key per chain (`take_key`), its messaging key and node A. Every node
reads it off the chain; node B (a NodeTrader) takes it, the tab answers the proposal
(wallet.answerProposals) and both sides settle through lock scripts.
"""

import os
import pathlib
import sys

import pytest

pytest.importorskip("playwright.sync_api", reason="needs playwright")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_htlc import doge                                            # noqa: E402,F401
from test_node_trader import _scan                                    # noqa: E402
from test_trade_tab_browser import _mine, _scan_a, world              # noqa: E402,F401

from arcade import trade as T, trade_orders as O                      # noqa: E402

COIN = 100_000_000
NODE_A = "ab" * 32


class _Mesh:
    node_id = NODE_A

    def online(self, node_id):
        return node_id == NODE_A


def test_a_tab_lists_a_swap_and_another_node_takes_it(world, monkeypatch):
    from playwright.sync_api import sync_playwright
    base, a, b, pep, dg = world
    monkeypatch.setattr(a, "mesh", _Mesh(), raising=False)
    net = {"network": "regtest", "version": 111}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page()
        tab.goto(f"{base}/join")
        tab.evaluate("async () => { window.w = await import('/wallet.js'); }")
        made = tab.evaluate("""async (net) => {
            const r = await w.signUp('lister' + Math.floor(Math.random() * 1e6), 'a long enough password', net);
            return {regtest: r.wallet.on.regtest.address, doge: r.wallet.on.doge && r.wallet.on.doge.address}; }""", net)
        assert made["doge"]
        pep.call("sendtoaddress", made["regtest"], 10)
        _mine(pep)
        _scan_a(a)
        listed = tab.evaluate("""async ([net, give, get]) => {
            const wal = await w.opened(net);
            const o = await w.listOrder(wal, give, get);
            return [o, w.myOrders(wal).length]; }""",
            [net, {"network": "regtest", "amount": 3 * COIN}, {"network": "doge", "amount": 2 * COIN}])
        mine, count = listed
        assert count == 1 and mine["trade_key"] != mine["take_key"], "a key per chain"
        _mine(pep)
        _scan_a(a)
        # The first inscription on this chain is number 0: the importer must not skip it.
        assert O.import_announced(a.trade_orders, a.token_index(a.messaging), "regtest") == 1
        shown = [o for o in a.trade_orders.visible(a.mesh.online) if o["id"] == mine["id"]]
        assert len(shown) == 1, "the tab's order is on the chain and its node is online"
        found = shown[0]
        assert found["node"] == NODE_A and found["take_key"] == mine["take_key"]
        assert found["lister"] == made["regtest"], "listed by the account that inscribed it"
        assert not [o for o in a.trade_orders.visible(lambda n: False) if o["id"] == mine["id"]]

        # The account's Exchange shows its listed swap, with Withdraw, and the List form.
        tab.goto(f"{base}/exchange?tab=tokens")
        tab.wait_for_selector("#tab-orders:not([hidden])", timeout=15000)
        assert "for 2 DOGE" in tab.inner_text("#tab-orders-list") and tab.is_visible("#tab-list-form")
        if os.environ.get("ARCADE_SHOT"):
            # A still picture of the card (the live page never settles for a screenshot).
            pathlib.Path(os.environ["ARCADE_SHOT"]).write_text(tab.evaluate("""() => {
                let css = "";
                for (const sheet of document.styleSheets) { try { for (const r of sheet.cssRules) css += r.cssText + "\\n"; } catch (e) {} }
                const theme = document.documentElement.getAttribute("data-theme") || "";
                return `<!doctype html><html data-theme="${theme}"><head><meta charset="utf-8"><style>${css}</style></head>`
                     + `<body style="padding:16px;max-width:900px">${document.getElementById("trade-orders").outerHTML}</body></html>`; }"""))
        # Off the page again: its own tick would answer alongside this test's calls.
        tab.goto(f"{base}/join")
        tab.evaluate("async () => { window.w = await import('/wallet.js'); }")

        # Node B takes it exactly as its Take button does.
        give = b.leg(found["get"]["network"], found["get"]["amount"], bytes.fromhex(found["take_key"]))
        get = T.Leg(found["give"]["network"], "coin", "", found["give"]["amount"],
                    found["trade_key"], b.key.pubkey.hex())
        trade = b.propose(bytes.fromhex(found["msg_key"]), found["id"], give, get).id

        steps = []
        for _ in range(45):
            _mine(pep, dg)
            _scan_a(a)
            _scan(b)
            for done in b.tick():
                steps.append(("b",) + done[1:2])
            _scan_a(a)
            got = tab.evaluate("""async ([net, id]) => {
                const wal = await w.opened(net);
                await w.answerProposals(wal);
                const t = w.myTrades(wal).find((t) => t.id === id);
                if (!t) return ['none', '', ''];
                const after = await w.tradeTick(wal, id);
                return [after.state, after.step || '', after.note || '']; }""", [net, trade])
            steps.append(("tab",) + tuple(got))
            if got[0] == "done" and b.book.get(trade).state == "done":
                break
        browser.close()
    tab_steps = [s[2] for s in steps if s[0] == "tab"]
    assert b.book.get(trade).state == "done", steps[-8:]
    assert steps[-1][1] == "done", steps[-8:]
    assert "lock" in tab_steps and "claim" in tab_steps, tab_steps


def test_a_tab_turns_down_terms_that_are_not_its_order(world, monkeypatch):
    from playwright.sync_api import sync_playwright
    base, a, b, pep, dg = world
    monkeypatch.setattr(a, "mesh", _Mesh(), raising=False)
    net = {"network": "regtest", "version": 111}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page()
        tab.goto(f"{base}/join")
        tab.evaluate("async () => { window.w = await import('/wallet.js'); }")
        made = tab.evaluate("""async (net) => {
            const r = await w.signUp('picky' + Math.floor(Math.random() * 1e6), 'a long enough password', net);
            return r.wallet.on.regtest.address; }""", net)
        pep.call("sendtoaddress", made, 10)
        _mine(pep)
        _scan_a(a)
        mine = tab.evaluate("""async ([net, give, get]) => w.listOrder(await w.opened(net), give, get)""",
                            [net, {"network": "regtest", "amount": 3 * COIN}, {"network": "doge", "amount": 2 * COIN}])
        _mine(pep)
        _scan_a(a)
        O.import_announced(a.trade_orders, a.token_index(a.messaging), "regtest")
        found = next(o for o in a.trade_orders.visible(a.mesh.online) if o["id"] == mine["id"])
        # One DOGE short of what the tab asked for.
        give = b.leg("doge", 1 * COIN, bytes.fromhex(found["take_key"]))
        get = T.Leg("regtest", "coin", "", 3 * COIN, found["trade_key"], b.key.pubkey.hex())
        trade = b.propose(bytes.fromhex(found["msg_key"]), found["id"], give, get).id
        for _ in range(6):
            _mine(pep)
            _scan_a(a)
            n = tab.evaluate("""async (net) => { const wal = await w.opened(net);
                await w.answerProposals(wal); return w.myTrades(wal).length; }""", net)
            assert n == 0, "nothing accepted"
            _scan(b)
            b.tick()
            with b._db() as conn:
                agreed = conn.execute("SELECT agreed FROM link WHERE id=?", (trade,)).fetchone()["agreed"]
            if agreed == -1:
                break
        browser.close()
    assert agreed == -1, "the tab said no"
    assert b.book.get(trade).state != "done"
