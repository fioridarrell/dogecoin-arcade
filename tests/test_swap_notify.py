"""A lock-script swap that finished tells both sides in Notifications (2026-10-17:
"If it is sold while you're playing your game, it should just happen and the notification
should go into your notifications")."""
from arcade import notify
from arcade.swap_history import SwapHistory


def test_both_sides_are_told(tmp_path):
    h = SwapHistory(tmp_path / "s.sqlite")
    h.record("t1", "test-token-59", "doge-coin", 1000, 114, seller="nSELL", buyer="nBUY", at=100)
    h.record("t2", "test-token-59", "doge-coin", 5, 6, seller="nX", buyer="nY", at=101)
    describe = lambda r: (f"{r['base_amount']:,} GOLD", f"{r['quote_amount']} DOGE")  # noqa: E731
    sold = notify.lockswap_events(h.of({"nSELL"}), {"nSELL"}, describe)
    assert [(e.kind, e.actor, e.target, e.text) for e in sold] == [("sold", "nBUY", "t1", "1,000 GOLD for 114 DOGE")]
    bought = notify.lockswap_events(h.of({"nBUY"}), {"nBUY"}, describe)
    assert bought[0].kind == "bought" and bought[0].actor == "nSELL"
    merged = notify.merge(sold, {})
    assert merged[0].unread and notify.merge(sold, notify.seen_now(sold, {}))[0].unread is False
