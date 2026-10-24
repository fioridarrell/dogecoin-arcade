import pytest
from arcade.lora import frames as F

def test_roundtrip_single_and_split():
    for n in (0, 10, F.MAX_BODY, F.MAX_BODY + 1, 1000, F.MAX_PARTS * F.MAX_BODY):
        body = bytes(range(256)) * 14
        body = body[:n]
        parts = F.split(F.Message(F.MAIL_IN, 7, body))
        assert all(len(p) <= F.MAX_FRAME for p in parts)
        assert len(parts) == F.frames_needed(n)
        j = F.Joiner()
        got = [j.add("a", p) for p in reversed(parts)]
        assert got[-1].body == body and all(g is None for g in got[:-1])

def test_too_big():
    with pytest.raises(F.FrameError):
        F.split(F.Message(F.MAIL_IN, 1, b"x" * (F.MAX_PARTS * F.MAX_BODY + 1)))

def test_missing_bitmap_and_expiry():
    t = [0]
    j = F.Joiner(clock=lambda: t[0])
    parts = F.split(F.Message(F.COINS, 9, b"y" * 500))
    j.add("a", parts[0])
    assert j.missing("a", F.COINS, 9) == 0b110
    t[0] = 500
    assert j.expire() == [("a", F.COINS, 9)]

def test_fields():
    w = F.Writer().u8(1).str("@robin").amount(12345).bytes(b"abc").done()
    r = F.Reader(w)
    assert (r.u8(), r.str(), r.amount(), r.bytes(), r.end()) == (1, "@robin", 12345, b"abc", True)
