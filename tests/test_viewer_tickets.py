"""Viewer tickets kept on disk (web/viewer_tickets.py; 2026-10-26, a deploy lost every open page's ticket)."""
from arcade.web.viewer_tickets import Tickets


def test_a_ticket_survives_a_restart_expires_and_slides_while_used(tmp_path):
    now = [1000.0]
    clock = lambda: now[0]                                        # noqa: E731
    path = tmp_path / "viewer-tickets.sqlite"
    first = Tickets(path, clock=clock, life=100)
    t = first.mint("PAddr", "main", "ab" * 32)
    assert first.get(t)[:3] == ("PAddr", "main", "ab" * 32)
    again = Tickets(path, clock=clock, life=100)                  # the worker a reload starts
    assert again.get(t)[:3] == ("PAddr", "main", "ab" * 32), "a restart keeps it"
    now[0] += 60                                                  # past half its life, still in use
    assert again.get(t)[3] == 1160, "renewed for a whole life from now"
    now[0] += 90
    assert Tickets(path, clock=clock, life=100).get(t), "the renewal is on disk too"
    now[0] += 200
    assert again.get(t) is None and Tickets(path, clock=clock, life=100).get(t) is None, "unused, it ends"
    assert again.get("") is None and again.get("nonsense") is None
