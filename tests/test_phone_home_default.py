"""A phone's browser opens the website as the installed app's home screen (2026-10-29: "if they are viewing on a mobile
the website make it look like the PWA by default"), until its reader picks Website under Themes on Your arcade."""
import pathlib
import sys

import pytest

pytest.importorskip("playwright.sync_api", reason="needs playwright")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_claim_browser import _free_port                          # noqa: E402
from test_realtime_rejoin_browser import Server                    # noqa: E402
from test_realtime_web import make_state                           # noqa: E402

from arcade.web.app import create_app                              # noqa: E402

pytestmark = pytest.mark.phone_default


@pytest.fixture
def base(tmp_path):
    port = _free_port()
    server = Server(create_app(make_state(tmp_path, "a")), port)
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 390, "height": 844})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(e.message))
        yield pg
        browser.close()


def _framed(page, path):
    page.wait_for_function("[...document.querySelectorAll('iframe')].some(f => { try { return f.contentWindow.location"
                           ".pathname === %r } catch (e) { return false } })" % path)


def test_a_phone_that_never_picked_opens_the_home_screen_with_the_page_in_a_window(base, page):
    page.goto(base + "/exchange")
    page.wait_for_url(base + "/home?tab=1")
    assert page.is_visible("#home")
    _framed(page, "/exchange")
    assert page.errors == []


def test_the_website_once_picked_stays_the_website(base, page):
    page.goto(base + "/home?tab=1")
    page.evaluate("localStorage.setItem('arcade.phoneview', 'web')")
    page.goto(base + "/exchange")
    page.wait_for_timeout(500)
    assert page.url == base + "/exchange"


def test_admin_and_a_computer_keep_the_website(base, page):
    page.goto(base + "/admin")
    page.wait_for_timeout(500)
    assert page.url.startswith(base + "/admin")
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(base + "/exchange")
    page.wait_for_timeout(500)
    assert page.url == base + "/exchange", "a computer: the desktop's own default, off in tests"


def test_the_sign_in_pages_stay_and_picking_the_home_screen_again_brings_it_back(base, page):
    page.goto(base + "/me")
    page.wait_for_url("**/join?next=%2Fme")          # nobody signed in: the sign-in page, never in a window
    page.wait_for_timeout(500)
    assert "/join" in page.url
    page.evaluate("localStorage.setItem('arcade.phoneview', 'web')")
    page.goto(base + "/feed")
    page.wait_for_timeout(500)
    assert page.url == base + "/feed"
    page.evaluate("localStorage.setItem('arcade.phoneview', 'home')")   # what Themes -> Home screen keeps
    page.goto(base + "/feed")
    page.wait_for_url(base + "/home?tab=1")
    _framed(page, "/feed")
    assert page.errors == []


def test_themes_on_a_phone_offers_the_home_screen_or_the_website():
    me = (pathlib.Path(__file__).resolve().parent.parent / "arcade/web/templates/me.html").read_text()
    assert 'id="phone-theme-pref"' in me
    assert 'data-phone-set="home"' in me and 'data-phone-set="web"' in me
    assert "localStorage.setItem('arcade.phoneview', want)" in me
