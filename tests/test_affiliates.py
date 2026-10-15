"""The Affiliates tab (2026-10-15: "Between the exchange and the Docs tab, add an affiliate
tab. Add a link to Doge soft ... I also want you to add as an affiliate antidoginaldogs.com"), and
Doge Soft credited where Doginals show."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client                                 # noqa: F401,E402


def test_the_tab_sits_between_the_exchange_and_the_docs(client):
    app, state = client
    page = app.get("/affiliates").text
    assert 'href="https://dogesoft.io"' in page and 'href="https://antidoginaldogs.com"' in page
    assert 'rel="noopener"' in page
    nav = app.get("/").text
    assert nav.index('href="/exchange"') < nav.index('href="/affiliates"') < nav.index('href="/docs"')
    from arcade.web.app import ACCOUNT_NAV, NAV
    for tabs in (NAV, ACCOUNT_NAV):
        paths = [t[0] for t in tabs]
        assert paths.index("/exchange") < paths.index("/affiliates") < paths.index("/docs")


def test_a_stranger_may_read_it():
    from arcade.web.door import PUBLIC_PAGES
    assert "/affiliates" in PUBLIC_PAGES


def test_doge_soft_is_credited_where_doginals_show():
    here = pathlib.Path(__file__).resolve().parent.parent / "arcade" / "web" / "templates"
    for name in ("my_wallet.html", "_doginals_tab.html"):
        text = (here / name).read_text()
        assert "dg-credit" in text and "https://dogesoft.io" in text and "Doge Soft" in text, name
