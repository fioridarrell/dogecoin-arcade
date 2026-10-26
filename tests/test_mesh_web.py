"""The .mesh web (arcade/mesh_web.py, web/meshweb.py; 2026-10-26): a .mesh name's page is the newest "p":"mesh"
inscription made to the address holding the name now ("Latest by the holder")."""
import hashlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from arcade import doginal_index as dx  # noqa: E402
from arcade import mesh_web as M  # noqa: E402
from test_doginal_index import FUND, SIG, Chain, _envelope, _tx_to  # noqa: E402

TEXT = b"text/plain;charset=utf-8"
A = b"\x76\xa9\x14" + b"\xaa" * 20 + b"\x88\xac"
B = b"\x76\xa9\x14" + b"\xbb" * 20 + b"\x88\xac"


@pytest.fixture(autouse=True)
def small(monkeypatch):
    monkeypatch.setattr(dx, "CHUNK", 4)
    monkeypatch.setattr(dx, "SETTLE", 0)
    monkeypatch.setattr(dx, "WALK_SETTLE", 0)


def _j(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


def _ins(fund_vout, body, to):
    return _tx_to([(FUND, fund_vout, _envelope(TEXT, parts=((0, body),)))], [(100_000, to)])


# --------------------------------------------------------------------------- the format

def test_a_page_is_json_p_mesh_with_its_name_and_pages():
    body = M.make("Me-Seeks.mesh", {"/": "# Hello\n=> about.mesh More", "about": "## About"})
    page = M.parse(body)
    assert page == {"name": "me-seeks.mesh", "pages": {"/": "# Hello\n=> about.mesh More", "/about": "## About"}}
    assert M.title_of(page["pages"]["/"]) == "Hello"
    assert M.links_of("x\n=> a.mesh/b Label here\n```\n=> not a link\n```\n=> /c") == [("a.mesh/b", "Label here"),
                                                                                     ("/c", "/c")]
    assert M.split_address("mesh://Me-Seeks.mesh/About/") == ("me-seeks.mesh", "/about")
    assert M.split_address("me-seeks.mesh") == ("me-seeks.mesh", "/")
    for bad in ({"p": "mesh", "name": "x.pepe", "pages": {"/": "hi"}},            # not .mesh
                {"p": "dns", "name": "x.mesh", "pages": {"/": "hi"}},
                {"p": "mesh", "name": "x.mesh", "pages": {"no slash": "hi"}},
                {"p": "mesh", "name": "x.mesh"}):
        assert M.parse(_j(bad)) is None, bad
    with pytest.raises(M.MeshError, match="at most"):
        M.make("x.mesh", {"/": "a" * 4000})
    with pytest.raises(M.MeshError, match="front page"):
        M.make("x.mesh", {"/about": "hi"})


# --------------------------------------------------------------------------- the walk resolves it

def _story():
    chain = Chain()
    claim = _ins(0, _j({"p": "dns", "op": "reg", "name": "me-seeks.mesh"}), A)
    page_a = _ins(1, M.make("me-seeks.mesh", {"/": "# From A"}), A)
    chain.add([claim, page_a])
    page_b = _ins(2, M.make("me-seeks.mesh", {"/": "# From B", "/about": "B's about"}), B)   # newer, B holds nothing
    other = _ins(3, M.make("other.mesh", {"/": "# Unclaimed"}), A)                             # its name never claimed
    chain.add([page_b, other])
    return chain, claim, page_a, page_b


def _index(tmp_path, chain):
    # walked as the test chains are (from dx.BASE); the resolver reads no chain-specific rule
    index = dx.DoginalIndex(tmp_path / "w.sqlite", lambda: chain, lambda: "walk")
    index.mesh_reader = lambda iid: ("text/plain", _body_of(chain, iid))
    index.step()
    return index


def _body_of(chain, iid):
    from arcade import ord_rules, rawchain
    tx = rawchain.read_tx(chain.txs[iid[:-2]])
    return ord_rules.read([tx.vin[0]["script"]])[2]


def test_the_newest_page_by_the_names_holder_counts_and_follows_a_sale(tmp_path):
    chain, claim, page_a, page_b = _story()
    index = _index(tmp_path, chain)
    got = index.mesh("me-seeks.mesh")
    assert got["claim"] == f"{claim[1]}i0" and got["holder"]
    assert got["page"]["id"] == f"{page_a[1]}i0", "B's newer page does not count: B does not hold the name"
    assert got["page"]["pages"] == {"/": "# From A"}
    assert index.mesh("other.mesh")["claim"] is None and index.mesh("other.mesh")["page"] is None
    assert [d["name"] for d in index.mesh_directory()] == ["me-seeks.mesh"]
    # A sells the name to B: B's page counts now, A's no longer
    sale = _tx_to([(claim[1], 0, SIG)], [(100_000, B)])
    chain.add([sale])
    chain.add([])
    index.step()
    got = index.mesh("me-seeks.mesh")
    assert got["page"]["id"] == f"{page_b[1]}i0" and got["page"]["pages"]["/about"] == "B's about"
    assert index.answer({"what": "mesh", "name": "Me-Seeks.mesh"})["mesh"]["page"]["id"] == f"{page_b[1]}i0"
    assert index.answer({"what": "mesh_directory"})["mesh_directory"][0]["title"] == "From B"


# --------------------------------------------------------------------------- the routes

from test_web import app_state, client  # noqa: E402,F401


def test_the_routes_serve_pages_the_directory_and_a_preview(client, tmp_path, monkeypatch):
    app, state = client
    chain, claim, page_a, page_b = _story()
    index = _index(tmp_path, chain)
    monkeypatch.setattr(state, "ord_indexes", lambda: {"main": index})
    page = app.get("/r/mesh/me-seeks.mesh").json()
    assert page["body"] == "# From A" and page["page"] == f"{page_a[1]}i0" and page["title"] == "From A"
    assert page["paths"] == ["/"]
    same = app.get("/r/mesh/me-seeks.mesh", params={"have": page["page"]}).json()
    assert same["unchanged"] and "body" not in same, "the T-Deck's copy is the newest: nothing sent again"
    assert app.get("/r/mesh/me-seeks.mesh/about").status_code == 404
    assert "no page yet" in app.get("/r/mesh/other.mesh").json()["detail"] or \
        "Nobody has claimed" in app.get("/r/mesh/other.mesh").json()["detail"]
    assert app.get("/r/mesh/not-a-name").status_code == 400
    assert [n["name"] for n in app.get("/r/mesh").json()["names"]] == ["me-seeks.mesh"]
    outside = {"host": "app.example.com", "cf-ray": "x"}
    shown = app.get("/meshweb/me-seeks.mesh", headers=outside)
    assert shown.status_code == 200 and "<h1>From A</h1>" in shown.text
    home = app.get("/meshweb", headers=outside)
    assert home.status_code == 200 and 'href="/meshweb/me-seeks.mesh"' in home.text
    assert app.get("/r/mesh/me-seeks.mesh", headers=outside).status_code == 200, "the page API's tree is open"


def test_only_the_names_holder_gets_a_draft_to_publish(client, tmp_path, monkeypatch):
    """Creator -> Names -> Publish a page: the site and its draft, only for the account whose Pepecoin mainnet address
    holds the name."""
    import base64
    from arcade.script import b58check_encode
    from test_doginal_wallet import _seat
    app, state = client
    _seat(app)
    chain, claim, page_a, page_b = _story()
    index = _index(tmp_path, chain)
    # the test chain's owners as Pepecoin mainnet addresses (version 56), as the real walk of that chain says them
    index.walker.owner_of = lambda spk: b58check_encode(56, spk[3:23]) if len(spk) == 25 else None
    index.__dict__.pop("_owners", None)
    monkeypatch.setattr(state, "ord_indexes", lambda: {"main": index})
    a_address = b58check_encode(56, b"\xaa" * 20)
    assert index.mesh("me-seeks.mesh")["holder"] == a_address
    stranger = app.get("/account/mesh/site", params={"name": "me-seeks.mesh"}).json()
    assert not stranger["mine"] and "not by your Pepecoin address" in stranger["why"]
    assert app.post("/account/mesh/draft", json={"name": "me-seeks.mesh", "pages": {"/": "# Mine?"}}).status_code == 403
    said = app.post("/account/address", json={"address": a_address, "chain": "main"})
    assert said.status_code == 200, said.text
    site = app.get("/account/mesh/site", params={"name": "me-seeks.mesh"}).json()
    assert site["mine"] and site["page"]["pages"] == {"/": "# From A"}, "the holder's editor opens on the page now"
    draft = app.post("/account/mesh/draft", json={"name": "me-seeks.mesh", "pages": {"/": "# New front", "about": "Hi"}})
    assert draft.status_code == 200 and draft.json()["chain"] == "main"
    assert M.parse(base64.b64decode(draft.json()["content"]))["pages"] == {"/": "# New front", "/about": "Hi"}
    too_big = app.post("/account/mesh/draft", json={"name": "me-seeks.mesh", "pages": {"/": "x" * 4000}})
    assert too_big.status_code == 400 and "at most" in too_big.json()["detail"]
    assert app.post("/account/mesh/draft", json={"name": "other.mesh", "pages": {"/": "# No"}}).status_code == 403
