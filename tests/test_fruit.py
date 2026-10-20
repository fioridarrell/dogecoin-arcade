"""Fruit names for new arcades' operators, held by the node (arcade/fruit.py, 2026-10-20: "@bigchiefenergy
should reserve the fruit names. 150 well-known fruits since that is the node operator account")."""
import pytest

from arcade import fruit, payload as P, tags


class Index:
    def __init__(self):
        self.names = {}                 # tag -> address

    def address_of(self, tag):
        return self.names.get(tag)

    def tag_of(self, address):
        return next((t for t, a in self.names.items() if a == address), None)


class Node:
    def __init__(self):
        self.made = 0
        self.sent = []

    def call(self, method, *params):
        if method == "getnewaddress":
            self.made += 1
            return f"addr{self.made}"
        if method == "sendmany":
            self.sent.append(params[1])
            return "fundtx"
        if method == "listunspent":
            return [{"address": a} for a in params[2]]
        raise AssertionError(method)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Chain:
    params = None

    def __init__(self, node):
        self.node = node

    def rpc(self):
        return self.node


class State:
    def __init__(self):
        self.s = {}

    def setting(self, k, d=None):
        return self.s.get(k, d)

    def set_setting(self, k, v):
        self.s[k] = v


@pytest.fixture
def stand(monkeypatch):
    index, node = Index(), Node()
    broadcast = []

    class Sender:
        def __init__(self, rpc, params):
            pass

        def prepare(self, sender, payload, reference=None):
            data = P.decode(payload).data
            kind, name = data[5], data[7:].decode()
            class Prepared:
                pass
            p = Prepared()
            p.args = (sender, kind, name, reference)
            return p

        def broadcast(self, p):
            sender, kind, name, reference = p.args
            broadcast.append(p.args)
            if kind == tags.KIND_CLAIM:
                index.names[name] = sender
            else:
                index.names[name] = reference
            return f"tx{len(broadcast)}"
    monkeypatch.setattr(fruit.tokenlib, "TokenSender", Sender)
    s = fruit.FruitStand(State(), Chain(node), index)
    return s, index, node, broadcast


def test_the_list_is_150_valid_names_and_leaves_out_what_was_already_somebodys():
    assert len(fruit.FRUITS) == 150 == len(set(fruit.FRUITS))
    for name in fruit.FRUITS:
        assert tags.validate(name) == name
    for gone in ("apple", "banana", "orange", "strawberry", "kaffirlime"):
        assert gone not in fruit.FRUITS


def test_each_fruit_is_claimed_from_an_address_of_its_own(stand):
    s, index, node, broadcast = stand
    index.names["mango"] = "somebody"                 # claimed by another before the stand got to it
    s.reserve(wait=0)
    assert s.error == ""
    assert len(node.sent) == 1 and len(node.sent[0]) == 149, "one funding transaction, every unclaimed fruit"
    senders = [b[0] for b in broadcast]
    assert len(senders) == 149 == len(set(senders)), "one address a name: the chain's rule"
    counts = s.status()["counts"]
    assert counts == {"held": 149, "taken": 1}
    # Again: nothing more to do.
    s.reserve(wait=0)
    assert len(broadcast) == 149 and len(node.sent) == 1


def test_a_fruit_is_handed_on_once_and_only_to_an_address_without_a_name(stand):
    s, index, node, broadcast = stand
    s.reserve(wait=0)
    first = s.next_free()
    index.names["already"] = "addr-x"
    with pytest.raises(ValueError):
        s.give(first, "addr-x")
    txid = s.give(first, "new-operator")
    assert index.address_of(first) == "new-operator" and txid
    assert broadcast[-1][1] == tags.KIND_TRANSFER and broadcast[-1][3] == "new-operator"
    with pytest.raises(ValueError):
        s.give(first, "someone-else")
    assert s.next_free() != first
    assert s.status()["counts"]["given"] == 1
