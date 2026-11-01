"""An arcade page that is open rings: the popup, its sound, and Answer. The rules are test_ring.py's."""

import pathlib
import sys
import time

import pytest

pytest.importorskip("selenium", reason="browser tests need selenium: pip install .[dev]")
from selenium.webdriver.common.by import By                          # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browsers                                                       # noqa: E402
from test_claim_browser import _free_port                            # noqa: E402
from test_contacts_browser import _signed_in                         # noqa: E402
from test_realtime_rejoin_browser import Server                      # noqa: E402
from test_realtime_web import make_state                             # noqa: E402

APP = "ab" * 32
#: One ring, from the route the page polls: what the node would answer.
RINGS = """
const real = window.fetch.bind(window); window.asked = 0;
window.fetch = function (url, opts) {
  if (String(url) === '/account/ring/live') { window.asked++;
    return Promise.resolve(new Response(JSON.stringify({rings: [{id: 'r1', from: '@wavecrest', app: 'Riffs', line: 'is in the Lounge',
      url: '/explore?ring=r1'}]}), {status: 200, headers: {'Content-Type': 'application/json'}})); }
  return real.apply(null, arguments);
};"""


@pytest.fixture(scope="module")
def browser():
    driver = browsers.launch()
    yield driver
    driver.quit()


@pytest.fixture
def base(tmp_path, no_nodes):
    from arcade.web.app import create_app
    server = Server(create_app(make_state(tmp_path, "node")), port := _free_port())
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.stop()


def _ringing(browser, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        found = browser.find_elements(By.CSS_SELECTOR, "[role=alertdialog][aria-label='Incoming call']")
        if found:
            return found[0]
        time.sleep(0.2)
    raise AssertionError("nothing rang: " + str(browser.execute_script(
        "return [typeof ARCADE_ME, typeof ARCADE_ME === 'undefined' ? null : ARCADE_ME, window.asked, document.body.innerHTML.indexOf('RINGING IN AN OPEN')]")))


def test_an_open_page_rings_once_and_answer_opens_the_app(browser, base):
    _signed_in(browser, base)
    browser.get(f"{base}/explore")
    browser.execute_script(RINGS)
    box = _ringing(browser)
    assert "@wavecrest" in box.text and "is in the Lounge" in box.text and "Riffs" in box.text
    box.find_elements(By.TAG_NAME, "button")[0].click()                       # Decline
    time.sleep(6)
    assert browser.execute_script("return window.asked") >= 2, "still looking"
    assert not browser.find_elements(By.CSS_SELECTOR, "[role=alertdialog]"), "the same ring does not come back"
    browser.get(f"{base}/explore")
    browser.execute_script(RINGS)
    box = _ringing(browser)
    box.find_elements(By.TAG_NAME, "button")[1].click()                       # Answer
    end = time.time() + 10
    while time.time() < end and "ring=r1" not in browser.current_url:
        time.sleep(0.1)
    assert "ring=r1" in browser.current_url


def test_nobody_signed_in_asks_for_rings():
    base_html = (pathlib.Path(__file__).resolve().parent.parent / "arcade/web/templates/base.html").read_text()
    body = base_html[base_html.index("RINGING IN AN OPEN PAGE"):]
    assert "!ARCADE_SIGNED" in body and "/account/ring/live" in body
    assert "textContent = r.from" in body and "innerHTML" not in body, "a caller's words are text, never markup"
