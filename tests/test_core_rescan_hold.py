"""A Core rescanning for wallet imports borrows the chain's reads, across a restart (2026-10-21).

A 1.14 Core holds its lock for the whole rescan and answers nothing; the import says so (core_catchup.hold), the
hold is kept in the arcade's settings, and it is let go of only once no import runs here and the Core answers again.
"""
from types import SimpleNamespace

import pytest

from arcade import core_catchup as cc
from arcade.rpc import RpcTransportError


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    cc.forget()
    cc.HOLDS.clear()
    cc._running.clear()
    kept = {}
    monkeypatch.setattr(cc, "store", (lambda: dict(kept), lambda d: (kept.clear(), kept.update(d))))
    monkeypatch.setattr(cc, "_loaded", False)
    monkeypatch.setattr(cc, "_sharers", lambda share, net: (["peerpeerpeer01"], 1000))
    yield kept
    cc.HOLDS.clear()
    cc._running.clear()
    cc.forget()


CHAIN = SimpleNamespace(network="doge", borrowed=False, params=SimpleNamespace(label="DOGE"))


class _Core:
    def __init__(self, answers):
        self.answers = answers

    def call(self, method, *args):
        if not self.answers:
            raise RpcTransportError("timed out")
        if method == "getblockchaininfo":
            return {"blocks": 1000, "headers": 1000, "verificationprogress": 1.0, "initialblockdownload": False}
        return {}

    def close(self):
        pass


def test_a_hold_borrows_at_once_and_is_kept(fresh):
    assert cc.hold(CHAIN, "rescanning for wallet imports")
    assert cc._state("doge").borrowing and fresh == {"doge": "rescanning for wallet imports"}
    st = cc.check(CHAIN, share=object(), client=lambda chain: _Core(answers=True))
    assert st.borrowing and st.reading.behind, "while the import runs, the Core is not even asked"


def test_after_a_restart_a_busy_core_still_borrows_and_one_that_answers_lets_go(fresh, monkeypatch):
    cc.hold(CHAIN, "rescanning for wallet imports")
    # a restart: memory gone, the settings kept
    cc.HOLDS.clear()
    cc._running.clear()
    cc.forget()
    monkeypatch.setattr(cc, "_loaded", False)
    st = cc.check(CHAIN, share=object(), client=lambda chain: _Core(answers=False))
    assert cc.held("doge") and st.reading.behind and st.borrowing, "the Core does not answer: still rescanning"
    st = cc.check(CHAIN, share=object(), client=lambda chain: _Core(answers=True))
    assert cc.held("doge") is None and fresh == {}, "it answers again: the rescan is over"
    assert st.reading.behind is False


def test_finished_lets_the_next_check_ask_the_core(fresh):
    cc.hold(CHAIN, "rescanning for wallet imports")
    cc.finished("doge")
    cc.check(CHAIN, share=object(), client=lambda chain: _Core(answers=True))
    assert cc.held("doge") is None
