"""The .mesh web's routes (arcade/mesh_web.py; 2026-10-26: a simple "web" browser for the T-Deck, pages a
.mesh name's holder inscribes, "just like original web like in 1990").

* GET /r/mesh                       the directory: every .mesh name with a page that counts, newest first
* GET /r/mesh/<name>[/path]         one page: its MeshText and where it comes from. `?have=<page id>` answers
                                    {"unchanged": true} when the asker's copy is the newest (the T-Deck's cache over LoRa)
* GET /meshweb[/<name>[/path]]      the same pages drawn in a browser at the T-Deck's size, to look at without one

Answered by the most complete walk of Pepecoin mainnet (DoginalIndex.lookup): this arcade's, or a linked arcade's.
"""
from __future__ import annotations

import html
import time
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import mesh_web as M

#: How long the directory is kept before it is asked again (every page of it is read off the chain once, then kept).
DIRECTORY_KEEP = 60.0


def register(app, state) -> None:
    kept: dict[str, Any] = {}

    def index():
        idx = state.ord_indexes().get(M.NET)
        if idx is None:
            raise LookupError("this arcade does not read Pepecoin's ords")
        return idx

    def ask(question: dict) -> dict:
        return index().lookup(question)

    def directory() -> list[dict]:
        at, names = kept.get("dir", (0.0, None))
        if names is None or time.time() - at > DIRECTORY_KEEP:
            names = ask({"what": "mesh_directory"}).get("mesh_directory") or []
            kept["dir"] = (time.time(), names)
        return names

    def directory_page() -> dict:
        """The directory as a page of its own, so the T-Deck's browser opens it like any site (its homepage until one
        is set): MeshText, its id the sha256 of what it lists, so an unchanged directory is not sent again."""
        names = directory()
        lines = ["# The .mesh web", "Every .mesh site, newest first.", ""]
        lines += [f"=> {n['name']} {n['name']}" + (f" - {n['title']}" if n.get("title") else "") for n in names[:60]]
        if not names:
            lines.append("Nobody has published a page yet. Claim a .mesh name and publish one from the arcade's "
                         "Creator tab.")
        body = "\n".join(lines)
        return {"name": "directory", "path": "/", "page": "directory-" + M.digest(body.encode())[:16],
                "title": "The .mesh web", "body": body, "paths": ["/"], "size": len(body.encode()), "number": None,
                "holder": None, "claim": None}

    def site(address: str) -> tuple[int, dict]:
        if address.strip("/ ").lower() in ("", "directory", "mesh://directory"):
            return 200, directory_page()
        name, path = M.split_address(address)
        if not name:
            return 400, {"detail": "not a .mesh name", "address": address}
        said = ask({"what": "mesh", "name": name}).get("mesh") or {}
        out: dict[str, Any] = {"name": name, "path": path, "holder": said.get("holder"), "claim": said.get("claim")}
        page = said.get("page")
        if not said.get("claim"):
            return 404, {**out, "detail": f"Nobody has claimed {name}."}
        if not page:
            return 404, {**out, "detail": f"{name} has no page yet."}
        pages = page.get("pages") or {}
        out.update({"page": page["id"], "number": page["number"], "size": page["size"], "sha256": page["sha256"],
                    "paths": sorted(pages), "title": M.title_of(pages.get("/", ""))})
        if path not in pages:
            return 404, {**out, "detail": f"{name} has no page {path}."}
        out["body"] = pages[path]
        return 200, out

    @app.get("/r/mesh")
    def mesh_directory():
        try:
            return JSONResponse({"names": directory()}, headers={"Cache-Control": "no-store"})
        except (LookupError, ValueError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.get("/r/mesh/{address:path}")
    def mesh_page(address: str, have: str = ""):
        try:
            code, out = site(address)
        except (LookupError, ValueError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        if code == 200 and have and have == out.get("page"):
            out = {k: v for k, v in out.items() if k != "body"}
            out["unchanged"] = True
        return JSONResponse(out, status_code=code, headers={"Cache-Control": "no-store"})

    # --- in a browser ---------------------------------------------------------------------------------------------

    def draw(text: str, here: str) -> str:
        """MeshText as HTML: what the T-Deck shows, line by line."""
        out, pre = [], False
        for line in str(text).splitlines():
            if line.startswith("```"):
                out.append("</pre>" if pre else "<pre>")
                pre = not pre
                continue
            if pre:
                out.append(html.escape(line))
                continue
            s = line.rstrip()
            if s.startswith("=>"):
                parts = s[2:].strip().split(None, 1)
                if parts:
                    target = parts[0]
                    href = "/meshweb/" + (here + target if target.startswith("/") else target.removeprefix("mesh://"))
                    label = parts[1] if len(parts) > 1 else target
                    out.append(f'<p class="l"><a href="{html.escape(href)}">{html.escape(label)}</a></p>')
            elif s.startswith("### "):
                out.append(f"<h3>{html.escape(s[4:])}</h3>")
            elif s.startswith("## "):
                out.append(f"<h2>{html.escape(s[3:])}</h2>")
            elif s.startswith("# "):
                out.append(f"<h1>{html.escape(s[2:])}</h1>")
            elif s.startswith("* "):
                out.append(f"<p class=\"li\">&bull; {html.escape(s[2:])}</p>")
            elif s.startswith(">"):
                out.append(f"<blockquote>{html.escape(s[1:].strip())}</blockquote>")
            elif s:
                out.append(f"<p>{html.escape(s)}</p>")
        if pre:
            out.append("</pre>")
        return "\n".join(out)

    def frame(title: str, address: str, inner: str, status: str) -> HTMLResponse:
        doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · .mesh</title>
<style>
:root{{--bg:#141310;--fg:#ece8e0;--muted:#9b948a;--line:#2c2a25;--accent:#d9a520;--panel:#1c1b17}}
body{{margin:0;min-height:100vh;background:#0b0b0a;color:var(--fg);font:14px/1.45 Roboto,system-ui,sans-serif;
  display:flex;flex-direction:column;align-items:center;gap:10px;padding:16px;box-sizing:border-box}}
.tdeck{{width:320px;height:240px;background:var(--bg);border:1px solid var(--line);border-radius:10px;display:flex;
  flex-direction:column;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.5)}}
.bar{{display:flex;gap:6px;align-items:center;padding:4px 6px;background:var(--panel);border-bottom:1px solid var(--line);
  font-size:11px}}
.bar a{{color:var(--accent);text-decoration:none}}
.bar .addr{{flex:1;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:2px 7px;overflow:hidden;
  white-space:nowrap;text-overflow:ellipsis;color:var(--fg)}}
.page{{flex:1;overflow:auto;padding:6px 8px;font-size:12px}}
.page h1{{font-size:16px;margin:2px 0 4px}} .page h2{{font-size:14px;margin:6px 0 3px}} .page h3{{font-size:12px;margin:6px 0 2px}}
.page p{{margin:0 0 4px}} .page .li{{margin-left:4px}} .page a{{color:#6fb0ff}}
.page blockquote{{margin:0 0 4px;padding-left:6px;border-left:2px solid var(--line);color:var(--muted)}}
.page pre{{margin:2px 0 6px;font:11px/1.2 ui-monospace,monospace;white-space:pre;overflow:auto}}
.status{{padding:2px 6px;border-top:1px solid var(--line);font-size:10px;color:var(--muted)}}
.note{{max-width:320px;color:var(--muted);font-size:12px}}
.note a{{color:var(--accent)}}
</style></head><body>
<div class="tdeck">
  <div class="bar"><a href="/meshweb">Home</a><span class="addr">{html.escape(address)}</span></div>
  <div class="page">{inner}</div>
  <div class="status">{html.escape(status)}</div>
</div>
<p class="note">A .mesh page, drawn at the size of a DogecoinArcade T-Deck (320 &times; 240). On the T-Deck it opens in
the Mesh Browser from the App Store, over WiFi or LoRa.</p>
</body></html>"""
        return HTMLResponse(doc, headers={"Cache-Control": "no-store"})

    @app.get("/meshweb", response_class=HTMLResponse)
    def meshweb_home():
        try:
            names = directory()
        except (LookupError, ValueError) as exc:
            return frame("The .mesh web", "directory", f"<p>{html.escape(str(exc))}</p>", "Document: not found")
        lines = ["# The .mesh web", "Every .mesh name with a page, newest first.", ""]
        lines += [f"=> {n['name']} {n['name']}" + (f" - {n['title']}" if n.get("title") else "") for n in names]
        if not names:
            lines.append("Nobody has published a page yet.")
        return frame("The .mesh web", "directory", draw("\n".join(lines), ""), f"Document: Done ({len(names)} sites)")

    @app.get("/meshweb/{address:path}", response_class=HTMLResponse)
    def meshweb_page(address: str):
        try:
            code, out = site(address)
        except (LookupError, ValueError) as exc:
            return frame(".mesh", address, f"<p>{html.escape(str(exc))}</p>", "Document: not found")
        name, path = out.get("name") or address, out.get("path") or "/"
        shown = name + ("" if path == "/" else path)
        if code != 200:
            return frame(shown, shown, f"<h1>Not found</h1><p>{html.escape(out.get('detail', ''))}</p>",
                         "Document: not found")
        return frame(out.get("title") or shown, shown, draw(out["body"], name),
                     f"Document: Done ({out['size']:,} bytes, inscription #{out['number']:,})")
