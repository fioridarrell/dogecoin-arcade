"""The sites search (2026-10-27: "how we could make a simple search engine that searches all of the published
domains"; "The directory at some point will be millions of pages long"; "It should be an option for the operator to
host The sites index. Just like ords are an option.").

A hosting arcade keeps, per chain, a full-text index of every name's site: one row per page (MeshText, HTML, or an
inscription the record names), its words, title, description and headings, in sites-<net>.sqlite (SQLite FTS5). It is
fed by this arcade's own walk of the chain (DoginalIndex, mode "walk"): each "p":"mesh" site record in the order it was
inscribed, counted only when the name's holder made it (the rule the Browser reads by, `DoginalIndex.site_record`), so
the newest record of a name replaces the pages of the one before. Every hosting arcade builds it from the chain alone,
by the same rules: any two say the same.

Modes (setting `sites_index:<net>`, Admin -> Ords -> Sites search):

* `host` -- keep the index (needs this chain's full ord index: "Keep the full index"), answer searches, here and for
  linked arcades (the ord index's mesh question "sites_search").
* `ask` (the default) -- keep nothing; a search is asked of a linked arcade that hosts it.
* `off` -- neither.

Ranking: SQLite's BM25 over the columns, the name weighted most, then the title, the description, the headings and
the words: a word repeated a thousand times counts little more than a few times (BM25 saturates), so stuffing does not
win; a good name, title and <meta name="description"> do. The description is the snippet when it matches.
"""
from __future__ import annotations

import html.parser
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import mesh_web

log = logging.getLogger("arcade.sites_index")

MODES = ("host", "ask", "off")
DEFAULT_MODE = "ask"
PER_PAGE = 10
#: Results one search may page through (a search engine's last page is never needed past this).
RESULTS_MAX = 1000
#: What one site may put in the index: pages, and words a page.
PAGES_MAX = 50
BODY_MAX = 20_000
FIELD_MAX = 600
#: Records read a pass, and the bytes of one page read to index it.
BATCH = 40
READ_MAX = 400_000
#: The operator's cap on the file, in MB (setting `sites_max_mb`): past it no new site is added.
DEFAULT_MAX_MB = 4096
#: A site's rows are rowid site_id * ROWS .. + ROWS - 1, so a site's pages go in one range delete.
ROWS = 100
#: The weights of the indexed columns, in the table's order (the unindexed ones count 0).
WEIGHTS = (0.0, 0.0, 0.0, 10.0, 6.0, 3.0, 2.0, 1.0)
#: Taken off BM25's score (lower is better) when the words are in the page's name or title, and for a site's front
#: page. BM25 alone ranks little while there are few sites (a word on every page is worth nearly nothing), so a name
#: or title that says it comes first, whatever the corpus.
NAMED_BOOST, FRONT_BOOST = 5.0, 0.5
HIT_OPEN, HIT_CLOSE = "\x02", "\x03"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sites (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, record TEXT NOT NULL,
    number INTEGER, height INTEGER, title TEXT, pages INTEGER, at REAL);
CREATE INDEX IF NOT EXISTS sites_newest ON sites (number DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5 (site UNINDEXED, path UNINDEXED, height UNINDEXED,
    name, title, descr, heads, body, tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3');
"""


# --------------------------------------------------------------------------- a page's words

class _Text(html.parser.HTMLParser):
    """An HTML page's title, description, headings and words; scripts, styles and pictures left out."""
    SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "head"}
    HEADS = {"h1", "h2", "h3"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.descr, self.heads, self.body = [], "", [], []
        self._skip, self._in_title, self._in_head = 0, False, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.descr = self.descr or (a.get("content") or "")
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.HEADS:
            self._in_head += 1
        elif tag == "img" and a.get("alt"):
            self.body.append(a["alt"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.HEADS:
            self._in_head = max(0, self._in_head - 1)

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        elif not self._skip:
            (self.heads if self._in_head else self.body).append(data)


#: How many times one word of a page counts (2026-10-27, the operator: "Will they be able to search engine optimization to
#: their web3site"): the words past this are dropped before indexing, so repeating a word a thousand times is
#: the same as saying it eight times.
REPEATS_MAX = 8


def _squash(text: str, most: int) -> str:
    seen: dict[str, int] = {}
    kept = []
    for word in re.sub(r"\s+", " ", text or "").strip().split(" "):
        key = word.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= REPEATS_MAX:
            kept.append(word)
    return " ".join(kept)[:most]


def html_text(page: str) -> dict:
    p = _Text()
    try:
        p.feed(page)
        p.close()
    except Exception:                                    # noqa: BLE001 -- broken HTML: what was read so far
        pass
    return {"title": _squash(" ".join(p.title), FIELD_MAX), "descr": _squash(p.descr, FIELD_MAX),
            "heads": _squash(" ".join(p.heads), FIELD_MAX * 2), "body": _squash(" ".join(p.body), BODY_MAX)}


def meshtext_text(text: str) -> dict:
    heads, body = [], []
    for line in str(text).split("\n"):
        s = line.strip()
        if not s or s.startswith("```") or s.startswith("!["):
            continue
        if s.startswith("#"):
            heads.append(s.lstrip("#").strip())
        elif s.startswith("=>"):
            bits = s[2:].split(None, 1)
            body.append(bits[1] if len(bits) > 1 else bits[0] if bits else "")
        else:
            body.append(s.lstrip("*> ").strip())
    return {"title": _squash(mesh_web.title_of(text), FIELD_MAX), "descr": "",
            "heads": _squash(" ".join(heads), FIELD_MAX * 2), "body": _squash(" ".join(body), BODY_MAX)}


def name_words(name: str) -> str:
    """A name as words to find it by: me-seeks.pep -> "me-seeks.pep me seeks meseeks pep"."""
    label, _, ending = name.rpartition(".")
    return " ".join(dict.fromkeys([name, label.replace("-", " "), label.replace("-", ""), ending]))


def query_of(said: str) -> str | None:
    """A visitor's words as an FTS5 query: every word must be there (the last may be the start of one). None: no
    words."""
    words = re.findall(r"\w+", str(said or "").lower())[:8]
    if not words:
        return None
    return " ".join(f'"{w}"' for w in words[:-1]) + (" " if len(words) > 1 else "") + f'"{words[-1]}"*'


def any_query_of(said: str) -> str | None:
    words = re.findall(r"\w+", str(said or "").lower())[:8]
    return " OR ".join(f'"{w}"' for w in words) or None


# --------------------------------------------------------------------------- the index

class SitesIndex:
    """One chain's sites search. `ord_index()`: this chain's DoginalIndex (or None); `mode()`: host | ask | off;
    `max_mb()`: the operator's cap."""

    def __init__(self, path: Path, net: str, ord_index: Callable[[], Any], mode: Callable[[], str],
                 max_mb: Callable[[], int] = lambda: DEFAULT_MAX_MB):
        self.path, self.net = Path(path), net
        self.ord_index, self.mode, self.max_mb = ord_index, mode, max_mb
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self.problem = ""

    # ---- the file

    def db(self) -> sqlite3.Connection:
        with self._lock:
            if self._db is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(SCHEMA)
                self._db = db
            return self._db

    def _meta(self, key: str, default: str = "") -> str:
        row = self.db().execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key: str, value) -> None:
        self.db().execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, str(value)))

    def size_mb(self) -> float:
        total = 0
        for p in (self.path, self.path.with_name(self.path.name + "-wal")):
            try:
                total += p.stat().st_size
            except OSError:
                pass
        return total / 1e6

    # ---- feeding it

    def step(self, budget: float = 5.0) -> float:
        """Read the next site records into the index; how long to rest before the next pass."""
        if self.mode() != "host":
            return 60.0
        index = self.ord_index()
        if index is None or index.mode() != "walk":
            self.problem = "Hosting the sites search needs this chain's full ord index (Keep the full index)."
            return 60.0
        if self.size_mb() > self.max_mb():
            self.problem = f"The index is at its cap of {self.max_mb():,} MB: no new site is added."
            return 300.0
        self.problem = ""
        until = time.monotonic() + budget
        after = int(self._meta("after_rid", "0") or 0)
        rows = index.mesh_records_after(after, BATCH)
        for row in rows:
            try:
                self._take(index, row)
            except Exception:                            # noqa: BLE001 -- one record that cannot be read: the next
                log.debug("site record %s", row, exc_info=True)
            after = int(row[4])
            with self._lock:
                self._set_meta("after_rid", after)
                self.db().commit()
            if time.monotonic() > until:
                return 1.0
        return 1.0 if len(rows) == BATCH else 30.0

    def _take(self, index, row) -> None:
        """One site record: when it is the name's holder's, its pages replace the name's rows."""
        record = index.site_record(row)
        if record is None:
            return
        name, pages = record["name"], record["pages"]
        found = []
        for path in sorted(pages)[:PAGES_MAX]:
            text = self._page_text(index, pages[path])
            if text is not None:
                found.append((path, text))
        title = next((t["title"] for p, t in found if p == "/" and t["title"]), "") or mesh_web.title_of(pages.get("/", ""))
        with self._lock:
            db = self.db()
            old = db.execute("SELECT id FROM sites WHERE name = ?", (name,)).fetchone()
            if old:
                db.execute("DELETE FROM pages WHERE rowid >= ? AND rowid < ?", (old[0] * ROWS, old[0] * ROWS + ROWS))
                db.execute("UPDATE sites SET record = ?, number = ?, height = ?, title = ?, pages = ?, at = ? WHERE id = ?",
                           (record["id"], record["number"], record["height"], title, len(found), time.time(), old[0]))
                sid = old[0]
            else:
                sid = db.execute("INSERT INTO sites (name, record, number, height, title, pages, at) VALUES "
                                 "(?, ?, ?, ?, ?, ?, ?)", (name, record["id"], record["number"], record["height"], title,
                                                           len(found), time.time())).lastrowid
            words = name_words(name)
            for k, (path, t) in enumerate(found):
                db.execute("INSERT INTO pages (rowid, site, path, height, name, title, descr, heads, body) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (sid * ROWS + k, name, path, record["height"], words, t["title"], t["descr"], t["heads"],
                            t["body"]))

    @staticmethod
    def _page_text(index, value) -> dict | None:
        kind, got = mesh_web.page_kind(value)
        if kind == "text":
            return meshtext_text(got)
        if kind == "html":
            return html_text(got)
        if kind == "id":
            try:
                ctype, body = index.read_inscription(got)
            except Exception:                            # noqa: BLE001 -- not readable now: found by name and path
                return {"title": "", "descr": "", "heads": "", "body": ""}
            body = bytes(body)[:READ_MAX]
            ctype = str(ctype or "").lower()
            if ctype.startswith("text/html"):
                return html_text(body.decode("utf-8", "replace"))
            if ctype.startswith("text/"):
                return {"title": "", "descr": "", "heads": "", "body": _squash(body.decode("utf-8", "replace"), BODY_MAX)}
            return {"title": "", "descr": "", "heads": "", "body": ""}
        return None

    # ---- answering

    def search(self, said: str, page: int = 0, per: int = PER_PAGE) -> dict:
        """{"results": [{name, path, address, title, snippet, height, score}], "total", "page", "pages"} -- the
        snippet's matching words between HIT_OPEN and HIT_CLOSE (the page draws them; the text is never markup)."""
        page = max(0, int(page or 0))
        per = max(1, min(int(per or PER_PAGE), 100))
        out: dict[str, Any] = {"q": str(said or "")[:200], "results": [], "total": 0, "page": page, "pages": 0}
        for query in (query_of(said), any_query_of(said)):
            if not query:
                break
            try:
                with self._lock:
                    db = self.db()
                    total = db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM pages WHERE pages MATCH ? LIMIT ?)",
                                       (query, RESULTS_MAX)).fetchone()[0]
                    if not total:
                        continue
                    weights = ", ".join(str(w) for w in WEIGHTS)
                    rows = db.execute(
                        f"SELECT site, path, height, title, descr, "
                        f"snippet(pages, 5, '{HIT_OPEN}', '{HIT_CLOSE}', '…', 24), "
                        f"snippet(pages, 7, '{HIT_OPEN}', '{HIT_CLOSE}', '…', 28), "
                        f"bm25(pages, {weights}) "
                        f"- CASE WHEN rowid IN (SELECT rowid FROM pages WHERE pages MATCH ?) THEN {NAMED_BOOST} ELSE 0 END "
                        f"- CASE WHEN path = '/' THEN {FRONT_BOOST} ELSE 0 END AS score "
                        f"FROM pages WHERE pages MATCH ? ORDER BY score LIMIT ? OFFSET ?",
                        ("{name title} : (" + query + ")", query, per, page * per)).fetchall()
            except sqlite3.OperationalError as exc:      # a query FTS5 cannot read: nothing found
                log.debug("sites search %r: %s", query, exc)
                continue
            out.update(total=total, pages=(total + per - 1) // per, any=query.startswith('"') and " OR " in query)
            for site, path, height, title, descr, d_snip, b_snip, score in rows:
                snip = d_snip if HIT_OPEN in (d_snip or "") else b_snip if HIT_OPEN in (b_snip or "") else (descr or b_snip)
                out["results"].append({"name": site, "path": path, "chain": self.net, "title": title or "",
                                       "address": site + ("" if path == "/" else path), "snippet": snip or "",
                                       "height": height, "score": round(float(score), 4)})
            break
        return out

    def directory(self, page: int = 0, per: int = 50) -> dict:
        """Every name with a site, newest record first, a page at a time."""
        page, per = max(0, int(page or 0)), max(1, min(int(per or 50), 200))
        with self._lock:
            db = self.db()
            total = db.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            rows = db.execute("SELECT name, record, number, height, title FROM sites ORDER BY number DESC LIMIT ? OFFSET ?",
                              (per, page * per)).fetchall()
        return {"names": [{"name": n, "page": r, "number": num, "height": h, "title": t or "", "chain": self.net}
                          for n, r, num, h, t in rows],
                "total": total, "page": page, "pages": (total + per - 1) // per}

    def status(self) -> dict:
        out: dict[str, Any] = {"net": self.net, "mode": self.mode(), "problem": self.problem}
        if self.mode() == "host" or self.path.exists():
            try:
                with self._lock:
                    db = self.db()
                    out["sites"] = db.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
                    out["pages"] = db.execute("SELECT COALESCE(SUM(pages), 0) FROM sites").fetchone()[0]
                    out["read_to"] = int(self._meta("after_rid", "0") or 0) - 1
                out["mb"] = round(self.size_mb(), 1)
                out["max_mb"] = self.max_mb()
            except sqlite3.Error as exc:
                out["problem"] = str(exc)[:200]
        return out


class Runner:
    """The daemon thread feeding every hosted chain's index (`indexes()`: {net: SitesIndex})."""

    def __init__(self, indexes: Callable[[], dict]):
        self.indexes = indexes
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="arcade-sites-index", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self._stop.wait(30)                              # the ord indexes open first
        while not self._stop.is_set():
            rests = []
            try:
                for index in list(self.indexes().values()):
                    rests.append(index.step())
            except Exception:                            # noqa: BLE001 -- never dies
                log.debug("sites index step", exc_info=True)
                rests.append(60.0)
            self._stop.wait(min(rests) if rests else 60.0)


def marked(snippet: str) -> str:
    """A snippet as HTML: escaped, the matching words in <b>."""
    import html as htmllib
    return htmllib.escape(snippet or "").replace(HIT_OPEN, "<b>").replace(HIT_CLOSE, "</b>")


def merge(answers: list[dict], page: int, per: int = PER_PAGE) -> dict:
    """Several chains' answers (each asked for its first (page + 1) * per) as one page, best first."""
    rows = sorted((r for a in answers for r in a.get("results", [])), key=lambda r: r.get("score", 0))
    total = sum(int(a.get("total") or 0) for a in answers)
    return {"results": rows[page * per:(page + 1) * per], "total": total, "page": page,
            "pages": (total + per - 1) // per}


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))
