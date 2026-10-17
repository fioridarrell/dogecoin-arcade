"""Collections described by an inscription (arcade/collection_json.py): 2026-10-17, Doginals collections that
appear on the arcade by themselves."""
import json
import pathlib

import pytest

from arcade import collection_json as C


def _items(n=7):
    return [C.Item(id=f"{k:064x}i0", number=k, traits={"Fur": ["Red", "Blue"][k % 2], "Hat": "Cap" if k % 3 else ""})
            for k in range(1, n + 1)]


def _roundtrip(items, traits, **kw):
    root, parts = C.build("Test Dogs", "doge", items, traits, per_part=3, **kw)
    root = C.with_parts(root, [f"{n:064x}" for n in range(len(parts))])
    parsed = C.parse_root(json.dumps(root))
    return C.assemble(parsed, [C.parse_part(json.dumps(p), parsed["traits"]) for p in parts])


def test_a_collection_goes_into_parts_and_comes_back_whole():
    coll = _roundtrip(_items(), ["Fur", "Hat"])
    assert [i.number for i in coll.items] == list(range(1, 8)) and coll.missing == []
    assert coll.items[1].traits == {"Fur": "Red", "Hat": "Cap"} and coll.items[2].traits == {"Fur": "Blue"}


def test_duplicate_ids_and_numbers_are_refused_and_gaps_reported():
    items = _items()
    with pytest.raises(C.CollectionError, match="numbered 3"):
        _roundtrip(items + [C.Item(id="ab" * 32 + "i0", number=3)], ["Fur"])
    with pytest.raises(C.CollectionError, match="listed twice"):
        _roundtrip(items + [C.Item(id=items[0].id, number=99)], ["Fur"])
    gappy = [i for i in items if i.number != 4]
    assert _roundtrip(gappy, ["Fur"]).missing == [4], "a gap is said, not refused"


def test_a_part_swapped_for_another_is_refused():
    root, parts = C.build("Test Dogs", "doge", _items(), ["Fur"], per_part=3)
    root = C.with_parts(root, [f"{n:064x}" for n in range(len(parts))])
    parts[0]["items"][0][1] = 50
    with pytest.raises(C.CollectionError, match="sha256"):
        C.assemble(root, parts)


def test_two_numbers_drawing_the_same_content_are_flagged():
    items = _items(4)
    hashes = {items[0].id: "a", items[1].id: "b", items[2].id: "a", items[3].id: None}
    said = C.chain_checks(items, hashes.get)
    assert said == {"missing": [items[3].id], "same_content": [[1, 3]]}


def test_the_two_layouts_people_already_have_convert():
    dm = [{"inscriptionId": "aa" * 32 + "i0", "name": "Dog #1", "attributes": {"Fur": "Red"}}]
    ow = [{"id": "aa" * 32 + "i0", "meta": {"name": "Dog #1", "attributes": [{"trait_type": "Fur", "value": "Red"}]}}]
    assert C.from_dm(dm) == C.from_ow(ow) == ([C.Item(id="aa" * 32 + "i0", number=1, traits={"Fur": "Red"})], ["Fur"])


@pytest.mark.skipif(not pathlib.Path("~/ADD-fixed/DM.json").expanduser().exists(),
                    reason="the Anti Doginal Dogs files are on a test machine only")
def test_ten_thousand_anti_doginal_dogs_fit_in_parts():
    items, traits = C.from_dm(json.loads(pathlib.Path("~/ADD-fixed/DM.json").expanduser().read_text()))
    root, parts = C.build("Anti Doginal Dogs", "doge", items, traits)
    sizes = [len(json.dumps(p, separators=(",", ":"))) for p in parts]
    assert len(parts) == 20 and max(sizes) < 60_000, sizes
    root = C.with_parts(root, ["00" * 32] * len(parts))
    coll = C.assemble(C.parse_root(json.dumps(root)), parts)
    assert len(coll.items) == 10_000 and coll.missing == []
