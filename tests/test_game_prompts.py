"""Honest prompts over games (2026-10-26, Arcade Hardening round 2, wave 3, issue #7; SadFrog: "all five").

The markup and the numbers the browser tests (`test_game_prompts_browser.py`) rest on, read without a browser: the
camera layer's place under the arcade's cards, and the trusted strip on a game in full screen."""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client                          # noqa: F401,E402
from test_games import _inscribe, MAKER                           # noqa: E402

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "arcade" / "web" / "templates"


def test_the_camera_layer_sits_under_the_arcades_cards():
    """(a) It sat at z-index 2147483000, over every card that asks the player, so a game could place a picture over
    a card's words or its buttons."""
    doors = (TEMPLATES / "_page_doors.js.html").read_text()
    css = (TEMPLATES / "base_css.css").read_text()
    media = int(re.search(r"var MEDIA_Z = (\d+);", doors).group(1))
    card = int(re.search(r"\.askcard-back\{[^}]*z-index:(\d+)", css).group(1))
    assert media < card, (media, card)
    assert "2147483000" not in doors
    assert "!!document.querySelector('.askcard-back')" in doors, "hidden while a card is open"


def test_a_game_in_full_screen_has_no_strip_under_it(client):
    """(e) taken back (2026-10-27: "Remove this"): no line of the arcade's under a game in full screen; the
    game's frame fills the screen."""
    app, state = client
    page = "d1" * 32
    _inscribe(state, page, 70, MAKER, {})
    full = app.get(f"/inscriptions/{page}/full").text
    assert 'class="trustbar"' not in full and "never asks for your password inside a game" not in full
    assert "--trust" not in full