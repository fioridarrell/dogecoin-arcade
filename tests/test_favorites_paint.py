"""The favorites' public paint() repaints the stars and nothing else (2026-10-28).

The LLM tab's draw() ends with arcadeFavs.paint() and also listens with onChange(draw). When paint() called the
listeners too, draw -> paint -> draw ran out of stack and the Exchange's default tab threw "Maximum call stack size
exceeded" as it opened. A listener is called when the set changes, never because a page repainted its stars."""
import pathlib
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="needs playwright")

EXUI = pathlib.Path(__file__).resolve().parent.parent / "arcade/web/templates/_exui.html"


def _favs_script():
    text = EXUI.read_text()
    found = re.search(r"<script>\s*(window\.arcadeFavs = window\.arcadeFavs \|\| \(function \(\) \{.*?\}\)\(\);)", text, re.S)
    assert found, "the favorites script is in _exui.html"
    return found.group(1)


def test_a_listener_that_repaints_is_not_called_back():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(e.message))
        page.route("**/account/favorites", lambda r: r.fulfill(json={"favorites": ["m1"], "saved": True}))
        page.route("http://arcade.test/", lambda r: r.fulfill(
            content_type="text/html",
            body='<button class="ex-star" data-fav="m1"></button><button class="ex-star" data-fav="m2"></button>'))
        page.goto("http://arcade.test/")
        page.add_script_tag(content=_favs_script() + """
            window.calls = 0;
            window.arcadeFavs.ready.then(() => window.arcadeFavs.onChange(() => { window.calls++; window.arcadeFavs.paint(); }));
        """)
        page.wait_for_function("window.calls > 0")
        page.wait_for_timeout(200)
        assert errors == []
        assert page.evaluate("window.calls") == 1, "onChange calls a new listener once; paint() does not call it again"
        assert page.eval_on_selector_all("[data-fav]", "bs => bs.map(b => b.textContent)") == ["★", "☆"]
        page.click('[data-fav="m2"]')                                   # a real change still reaches the listener
        page.wait_for_function("window.calls === 2")
        assert page.eval_on_selector_all("[data-fav]", "bs => bs.map(b => b.textContent)") == ["★", "★"]
        browser.close()
