"""The sites search (arcade/sites_index.py; 2026-10-27): every site's pages in a full-text index an operator may
host, searched at /r/search, the directory a page at a time."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from arcade import doginal_index as dx  # noqa: E402
from arcade import mesh_web as M  # noqa: E402
from arcade import sites_index as S  # noqa: E402
from test_mesh_web import A, B, _index, _ins, _j, _story, small  # noqa: E402,F401
from test_doginal_index import Chain  # noqa: E402
from test_web import app_state, client  # noqa: E402,F401

IID = "cd" * 32 + "i0"


class Walk:
    """A stand-in ord index: site records in order, each counted or not."""

    def __init__(self):
        self.rows, self.records, self.bodies = [], {}, {}

    def mode(self):
        return "walk"

    def add(self, name, pages, counts=True):
        rid = len(self.rows) + 1
        row = (b"t" * 32, 0, rid - 1, 100 + rid, rid)
        self.rows.append(row)
        self.records[rid] = {"id": f"{rid:064x}i0", "number": rid - 1, "height": 100 + rid, "name": name,
                             "pages": M.parse(M.make(name, pages))["pages"]} if counts else None

    def mesh_records_after(self, rid, limit=40):
        return [r for r in self.rows if r[4] > rid][:limit]

    def site_record(self, row):
        return self.records[row[4]]

    def read_inscription(self, iid):
        return self.bodies[iid]


def _sites(tmp_path, walk, mode="host"):
    return S.SitesIndex(tmp_path / "sites-main.sqlite", "main", lambda: walk, lambda: mode)


def test_a_hosted_index_finds_pages_by_their_words_and_ranks_the_name_first(tmp_path):
    walk = Walk()
    walk.add("pond.pepe", {"/": "# The pond\nA frog sits on a lily pad.\n=> /about About the frog",
                           "/about": "## About\nWritten by a frog."})
    walk.add("frog.pepe", {"/": {"html": "<title>Frogs</title><meta name='description' content='All about frogs.'>"
                                         "<h1>Frog facts</h1><p>Green.</p><script>var frog = 1</script>"}})
    walk.add("shop.pepe", {"/": {"id": IID}})
    walk.bodies[IID] = ("text/html;charset=utf-8", b"<title>Shop</title><p>Buy hats and boots here.</p>")
    sites = _sites(tmp_path, walk)
    sites.step()
    found = sites.search("frog")
    assert found["total"] == 3 and found["results"][0]["name"] == "frog.pepe", "the name counts most"
    assert found["results"][0]["title"] == "Frogs"
    assert S.HIT_OPEN in found["results"][0]["snippet"], "the description, its word marked, is the snippet"
    assert S.marked(found["results"][0]["snippet"]) == "All about <b>frogs</b>."
    assert {r["address"] for r in found["results"]} == {"frog.pepe", "pond.pepe", "pond.pepe/about"}
    assert [r["name"] for r in sites.search("boots")["results"]] == ["shop.pepe"], "an inscription page is read"
    assert sites.search("var")["total"] == 0, "a script's words are not the page's"
    assert sites.search("lily pad")["results"][0]["address"] == "pond.pepe", "every word must be there"
    assert sites.search("hats zebra")["results"][0]["name"] == "shop.pepe", "else any of them"
    assert sites.search("fro")["total"] == 3, "the last word may be the start of one"
    assert sites.search('" OR (')["total"] == 0, "what FTS5 cannot read finds nothing, it does not fail"
    st = sites.status()
    assert (st["sites"], st["pages"], st["read_to"]) == (3, 4, 2)


def test_a_newer_record_replaces_the_name_and_one_that_does_not_count_changes_nothing(tmp_path):
    walk = Walk()
    walk.add("pond.pepe", {"/": "# Old pond\nTadpoles."})
    sites = _sites(tmp_path, walk)
    sites.step()
    walk.add("pond.pepe", {"/": "# Pond\nNot the holder's: it does not count."}, counts=False)
    walk.add("pond.pepe", {"/": "# New pond\nNewts."})
    sites.step()
    assert sites.search("tadpoles")["total"] == 0 and sites.search("newts")["total"] == 1
    assert sites.search("holder")["total"] == 0
    assert sites.directory()["names"][0]["title"] == "New pond"


def test_stuffing_a_word_does_not_beat_the_name_and_the_title(tmp_path):
    walk = Walk()
    walk.add("hats.pepe", {"/": "# Hats\nWe sell hats."})
    walk.add("spam.pepe", {"/": "# Cheap stuff\n" + "hats " * 3000})
    for k in range(4):
        walk.add(f"other{k}.pepe", {"/": f"# Other {k}\nBoots and coats."})
    sites = _sites(tmp_path, walk)
    sites.step()
    assert [r["name"] for r in sites.search("hats")["results"]] == ["hats.pepe", "spam.pepe"]


def test_the_directory_and_the_results_come_a_page_at_a_time(tmp_path):
    walk = Walk()
    for k in range(25):
        walk.add(f"site{k}.pepe", {"/": f"# Site {k}\nA page about ponds."})
    sites = _sites(tmp_path, walk)
    while sites.step() < 30:
        pass
    first, last = sites.directory(0, 10), sites.directory(2, 10)
    assert (first["total"], first["pages"]) == (25, 3)
    assert first["names"][0]["name"] == "site24.pepe", "newest first"
    assert [n["name"] for n in last["names"]] == [f"site{k}.pepe" for k in range(4, -1, -1)]
    two = sites.search("ponds", page=2)
    assert (two["total"], two["pages"], len(two["results"])) == (25, 3, 5)


def test_only_a_hosting_arcade_with_the_full_index_builds_one(tmp_path):
    walk = Walk()
    walk.add("pond.pepe", {"/": "# Pond"})
    assert _sites(tmp_path, walk, mode="ask").step() == 60.0
    walk.mode = lambda: "help"
    sites = _sites(tmp_path, walk)
    sites.step()
    assert "full ord index" in sites.problem and sites.status()["sites"] == 0


def test_the_ord_walk_gives_the_records_in_order_and_says_which_count(tmp_path):
    chain, claim, page_a, page_b = _story()
    index = _index(tmp_path, chain)
    rows = index.mesh_records_after(0)
    assert [r[4] for r in rows] == sorted(r[4] for r in rows) and len(rows) == 3
    said = [index.site_record(r) for r in rows]
    assert said[0]["id"] == f"{page_a[1]}i0" and said[0]["pages"] == {"/": "# From A"}
    assert said[1] is None, "B holds nothing: its page does not count"
    assert said[2] is None, "other.mesh was never claimed"
    assert index.mesh_records_after(rows[-1][4]) == []
    with pytest.raises(ValueError, match="does not host"):
        index.answer({"what": "sites_search", "q": "a"})


def test_search_and_the_directory_on_the_routes_and_admin_chooses_who_hosts(client, tmp_path, monkeypatch):
    app, state = client
    chain = Chain()
    chain.add([_ins(0, _j({"p": "dns", "op": "reg", "name": "frog.pepe"}), A),
               _ins(1, M.make("frog.pepe", {"/": "# Frog facts\nFrogs are green.", "/eggs": "## Eggs\nFrogspawn."}), A)])
    index = _index(tmp_path, chain)
    index.sites_search = state._sites_hook("main")
    monkeypatch.setattr(state, "ord_indexes", lambda: {"main": index})
    asked = app.get("/r/search", params={"q": "frogs"})
    assert asked.status_code == 200 and asked.json()["total"] == 0 and asked.json()["trouble"], "ask: nobody hosts"
    assert asked.headers["access-control-allow-origin"] == "*"
    refused = app.post("/admin/api/sites", json={"net": "main", "mode": "host"}, headers={"X-Arcade-Admin": "1"})
    assert refused.status_code == 400 and "Keep the full index" in refused.json()["detail"]
    state.set_setting("ord_index:main", "walk")
    took = app.post("/admin/api/sites", json={"net": "main", "mode": "host"}, headers={"X-Arcade-Admin": "1"})
    assert took.status_code == 200, took.text
    state.sites_index("main").step()
    found = app.get("/r/search", params={"q": "frog"}).json()
    assert found["total"] == 2 and found["results"][0]["address"] == "frog.pepe" and found["results"][0]["chain"] == "main"
    assert index.answer({"what": "sites_search", "q": "frogspawn"})["sites_search"]["results"][0]["path"] == "/eggs", \
        "a linked arcade's question is answered from the hosted index"
    listed = app.get("/r/sites", params={"page": 0, "per": 1}).json()
    assert listed["total"] == 1 and listed["names"][0]["name"] == "frog.pepe" and listed["pages"] == 1
    assert app.get("/r/search").status_code == 400
    admin = app.get("/admin/api/sites").json()
    row = next(c for c in admin["chains"] if c["net"] == "main")
    assert row["mode"] == "host" and row["sites"] == 1 and row["pages"] == 2
