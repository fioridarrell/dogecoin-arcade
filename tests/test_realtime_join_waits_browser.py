"""join waits for a person unlocking their wallet (2026-10-30, from the Tank Simulator session; game-agnostic: every
page that joins a room). The viewer asked "Join this game as you?" for a locked wallet, realtime.js gave up after 20 s,
the page played offline and the viewer's late answer was dropped. Now the viewer says {pending: 'unlock'} first and
the page waits; if the person never unlocks, why says 'wallet-locked'. Here a stand-in viewer around realtime.js, with
the browser's clock run forward instead of waiting."""

import html
import pathlib
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright's Chromium")

REALTIME = (pathlib.Path(__file__).resolve().parents[1] / "arcade/web/templates/realtime.js").read_text().replace(
    "</script>", "<\\/script>")      # its guide shows a script tag


def _page(viewer: str) -> str:
    game = ("<!doctype html><body><script>" + REALTIME + "</script><script>"
            "window.room = null; arcade.realtime.join('town', {game: 'wait-test'})"
            ".then(function (r) { window.room = {online: r.online, why: r.why}; });</script>")
    return ("<!doctype html><body><script>window.asked = [];"
            "window.addEventListener('message', function (e) { var m = e.data || {};"
            " if (m.arcade !== 'realtime' || !m.seq) return; window.asked.push(m); window.src = e.source;"
            + viewer + "});</script><iframe id=g srcdoc=\"" + html.escape(game, quote=True) + "\"></iframe>")


def _until(page, script, what):
    end = time.time() + 15
    while time.time() < end:
        got = page.evaluate(script)
        if got:
            return got
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def _room(browser, viewer: str, run_for: int):
    page = browser.new_page()
    try:
        page.clock.install()
        page.set_content(_page(viewer))
        asked = _until(page, "window.asked[0] || null", "the page's join")
        time.sleep(0.3)                          # the pending answer (a real message, not a timer) arrives first
        page.clock.run_for(run_for)
        room = _until(page, "document.getElementById('g').contentWindow.room", "the room")
        return room, asked
    finally:
        page.close()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


# The viewer says it is waiting on the person, then answers 5 minutes later (well past the old 20 s).
LATE = ("if (m.op === 'join' && m.waits) { e.source.postMessage({arcade: 'realtime', seq: m.seq, pending: 'unlock'}, '*');"
        " setTimeout(function () { window.src.postMessage({arcade: 'realtime', seq: m.seq, ok: true, online: true,"
        " me: {id: 'p1'}, members: []}, '*'); }, 300000); }")
NEVER = ("if (m.op === 'join' && m.waits) e.source.postMessage({arcade: 'realtime', seq: m.seq, pending: 'unlock'}, '*');")


def test_join_waits_while_the_viewer_asks_for_the_password(browser):
    room, asked = _room(browser, LATE, 300500)
    assert asked["waits"] is True, "the page says it reads a pending answer"
    assert room == {"online": True, "why": ""}, room


def test_a_wallet_never_unlocked_says_so(browser):
    room, _ = _room(browser, NEVER, 601000)
    assert room["online"] is False and room["why"].startswith("wallet-locked"), room


def test_no_viewer_at_all_still_gives_up_after_20_s(browser):
    room, _ = _room(browser, "", 20500)
    assert room["online"] is False and room["why"].startswith("no viewer is listening"), room
