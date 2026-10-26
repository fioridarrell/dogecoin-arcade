"""The .mesh web (2026-10-26: "I want to use .mesh DNS protocol. For the Meshtastic ... a simple 'web' browser.
Whichever wallet holds the name should be able to inscribe a companion to the name that displays a webpage designed
specifically for the Meshtastic network and the T deck in mind. It should work just like original web like in 1990").

A .mesh NAME is an ordinary "p":"dns" name (ord_rules.NAME_RULES, the spec reading) on Pepecoin mainnet. Its PAGE is a
JSON inscription of its own:

    {"p": "mesh", "name": "me-seeks.mesh", "pages": {"/": "# Hello\\n...", "/about": "..."}}

A page counts when the address it was inscribed to (its reveal's first output) holds the name's inscription now; of
those, the newest wins (the operator chose "Latest by the holder"). Sell the name and the old holder's pages stop counting
until the new holder inscribes one.

Each page is MESHTEXT: plain UTF-8 lines in the spirit of 1990s hypertext (Gemini's text format):

    # Heading / ## Sub heading / ### Small heading
    * a list item
    > a quote
    => name.mesh/path A link       (one per line; "=> /path Label" is a page of the same site)
    ```  ... preformatted (ASCII art) ...  ```
    anything else: a paragraph line

A whole site is at most SITE_MAX bytes: one 16-part LoRa message, so every page also loads by radio.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import ord_rules

#: The chain .mesh names and pages are read on.
NET = "main"
ENDING = ".mesh"
#: A site's whole inscription: one 16-part LoRa message (PROTOCOL.md: parts of 195 bytes) less the answer's own fields.
SITE_MAX = 3000
#: Paths: "/" and "/word/word", short, lowercase letters, digits, - _ .
PATH = re.compile(r"^/(?:[a-z0-9._-]{1,32}(?:/[a-z0-9._-]{1,32}){0,3})?$")
PATHS_MAX = 24
#: Pages looked at, newest first, when a name is resolved or the directory drawn.
SCAN = 400


class MeshError(ValueError):
    pass


def name_of(raw: str) -> str | None:
    """A .mesh name as the spec reading makes it, or None when it is not one."""
    made = ord_rules.normalise("spec", "json", str(raw or "").strip())
    return made if made and made.endswith(ENDING) else None


def split_address(address: str) -> tuple[str | None, str]:
    """ "me-seeks.mesh/about" -> ("me-seeks.mesh", "/about"); a bare name is its "/"."""
    s = str(address or "").strip()
    if s.lower().startswith("mesh://"):
        s = s[7:]
    name, slash, path = s.partition("/")
    path = "/" + path.strip("/") if slash else "/"
    return name_of(name), path.lower()


def parse(body: bytes) -> dict | None:
    """A page inscription's {"name", "pages"}, or None when the body is not one that counts."""
    if not body or len(body) > SITE_MAX:
        return None
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("p") != "mesh" or not isinstance(value.get("pages"), dict):
        return None
    name = name_of(value.get("name"))
    if not name:
        return None
    pages = {}
    for path, text in list(value["pages"].items())[:PATHS_MAX]:
        if isinstance(path, str) and isinstance(text, str) and PATH.match(path.lower()):
            pages[path.lower()] = text
    if not pages:
        return None
    return {"name": name, "pages": pages}


def make(name: str, pages: dict[str, str]) -> bytes:
    """The bytes of a page inscription (Creator -> Names -> Publish a page), compact, refused when it would not count."""
    made = name_of(name)
    if not made:
        raise MeshError("not a .mesh name")
    clean = {}
    for path, text in pages.items():
        p = "/" + str(path or "").strip().strip("/").lower() if str(path or "").strip("/") else "/"
        if not PATH.match(p):
            raise MeshError(f"{path!r} is not a path: / and short words of a-z 0-9 . _ - between slashes")
        clean[p] = str(text).replace("\r\n", "\n")
    if "/" not in clean:
        raise MeshError("a site needs its front page, /")
    body = json.dumps({"p": "mesh", "name": made, "pages": clean}, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > SITE_MAX:
        raise MeshError(f"{len(body):,} bytes: a site is at most {SITE_MAX:,}, so it loads over LoRa too")
    if parse(body) is None:
        raise MeshError("that would not read back as a page")
    return body


def title_of(text: str) -> str:
    """A page's first heading, else its first line: what the directory and the browser's title show."""
    first = ""
    for line in str(text).splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:80]
        if s and not first and not s.startswith(("=>", "```")):
            first = s
    return first[:80]


def links_of(text: str) -> list[tuple[str, str]]:
    """(target, label) of each "=>" line, in order."""
    out, pre = [], False
    for line in str(text).splitlines():
        if line.startswith("```"):
            pre = not pre
            continue
        if not pre and line.startswith("=>"):
            parts = line[2:].strip().split(None, 1)
            if parts:
                out.append((parts[0], parts[1].strip() if len(parts) > 1 else parts[0]))
    return out


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
