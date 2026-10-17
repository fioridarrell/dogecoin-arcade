"""Turn a Doginals trait file into a collection JSON (arcade/collection_json.py) and, if asked, inscribe it.

    python tools/collection_publish.py DM.json --name "Anti Doginal Dogs" --out build/add
        writes part-01.json ... and root.json (parts not yet named) to look at; sends nothing.

    python tools/collection_publish.py DM.json --name "Anti Doginal Dogs" --out build/add \
        --publish -testnet --key ~/.arcade/publish.key --rpc-url http://127.0.0.1:44555 --rpc-cookie .cookie
        inscribes every part, then the root naming them, through arcade/publish.py (journaled: a run that stops
        is picked up where it stopped and nothing is paid for twice).

The layout is guessed from the file: doggy.market's ("inscriptionId" + attributes object) or OW's ("id" + meta).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arcade import collection_json as C  # noqa: E402


def load_items(path: Path) -> tuple[list[C.Item], list[str]]:
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or not rows:
        raise C.CollectionError(f"{path} is not a list of items")
    return C.from_dm(rows) if "inscriptionId" in rows[0] else C.from_ow(rows)


def compact(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file", help="the trait file (DM or OW layout)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--chain", default="doge", help="where the items live (default doge: Doginals)")
    ap.add_argument("--description", default="")
    ap.add_argument("--cover", default="", help="an item's inscription id to show for the collection")
    ap.add_argument("--replaces", default="", help="the root this one corrects (same publisher only)")
    ap.add_argument("--out", required=True, help="folder for the root and parts")
    ap.add_argument("--publish", action="store_true", help="inscribe the parts and the root")
    args, rest = ap.parse_known_args(argv)

    items, traits = load_items(Path(args.file).expanduser())
    root, parts = C.build(args.name, args.chain, items, traits, description=args.description, cover=args.cover,
                          replaces=args.replaces)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for n, part in enumerate(parts, 1):
        (out / f"part-{n:02d}.json").write_bytes(compact(part))
    (out / "root.json").write_bytes(compact(root))
    print(f"{len(items)} items, {len(traits)} traits, {len(parts)} parts in {out}")
    if not args.publish:
        return 0

    from arcade import publish
    pargs = publish_args(rest)
    params = publish._params(pargs)
    with publish._rpc(pargs, params) as rpc:
        pub = publish.Publisher(rpc, params, publish.Key.from_file(pargs.key), pargs.journal,
                                say=lambda s: print("   " + s, flush=True), index=pargs.index)
        ids = []
        for n, part in enumerate(parts, 1):
            sent = pub.inscribe(compact(part), C.CONTENT_TYPE, name=f"part-{n:02d}.json")
            ids.append(sent["id"])
            print(f"part {n}/{len(parts)}: {sent['id']}", flush=True)
        whole = C.with_parts(root, ids)
        (out / "root.json").write_bytes(compact(whole))
        sent = pub.inscribe(compact(whole), C.CONTENT_TYPE, name="root.json")
        print(json.dumps({"root": sent["id"], "parts": ids}, indent=1))
    return 0


def publish_args(rest: list[str]):
    """The publish options (network, key, node) in arcade-publish's own words."""
    ap = argparse.ArgumentParser(prog="collection_publish --publish")
    net = ap.add_mutually_exclusive_group()
    net.add_argument("-main", action="store_true")
    net.add_argument("-testnet", action="store_true")
    net.add_argument("-regtest", action="store_true")
    ap.add_argument("--key", required=True)
    ap.add_argument("--datadir")
    ap.add_argument("--rpc-url")
    ap.add_argument("--rpc-user")
    ap.add_argument("--rpc-password")
    ap.add_argument("--rpc-cookie")
    ap.add_argument("--index")
    ap.add_argument("--journal", default=str(Path("~/.dogecoinarcade/publish").expanduser()))
    return ap.parse_args(rest)


if __name__ == "__main__":
    sys.exit(main())
