"""Run a service for your own game on your own node: a bank, a referee, a dealer.

2026-10-12 (the operator): "If anybody else wants to make a bank for their own game,
they should be able to do it on their own node." A game's service is a program
that sits in that game's realtime rooms and answers its players -- ASHVALE's
@ashvale Bank is one. Any node's owner can run one for their own game, on their
own machine, with nothing but Python and this module: no browser, no website,
nobody else's node.

**Who it speaks as.** The node's own account: the realtime routes treat a request
from this machine that carries the node's service key as the node's operator, so
the service joins as the node's wallet address, signed by its own Core, exactly
as the operator's own viewer does. Players see that address's @tag, and every
node checks the certificate with its own Core, so nobody can run a service in
your name.

**The key.** `<home>/realtime-service.key`, 32 random bytes made by the node the
first time, readable only by the user the node runs as. It never leaves the
machine: the routes ignore it on any request that came in through the door, so
the internet cannot use it even if it leaked.

**What it can do.** What any player can: join rooms of a game, send messages of
at most 512 bytes at about five a second, hear everyone in the room. Anything
that must last -- payments, items, saves that must survive -- goes on the chain
or into the service's own storage, as with any game.

    from arcade.service import Room
    with Room("mygame", "bank") as room:
        for event in room.events():
            if event["type"] == "message":
                room.send({"t": "ack", "to": event["member"]["id"]})
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Iterator

KEY_FILE = "realtime-service.key"
HEADER = "X-Arcade-Service"


def default_home() -> Path:
    return Path(os.environ.get("ARCADE_HOME", Path.home() / ".dogecoinarcade"))


def service_key(home: Path | str, make: bool = True) -> str | None:
    """The node's service key, made on first use (0600)."""
    path = Path(home) / KEY_FILE
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        if not make:
            return None
    key = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:                    # made meanwhile by the node or another service
        return path.read_text().strip() or None
    with os.fdopen(fd, "w") as fh:
        fh.write(key + "\n")
    return key


class ServiceError(Exception):
    pass


class Room:
    """One room of one game, from a program on the node's own machine.

    `events()` yields what happens in the room (join, leave, message) and comes
    back by itself if the node restarts or the stream drops: it joins again and
    carries on. `send()` takes a dict (sent as JSON), a str or bytes."""

    def __init__(self, game: str, room: str, *, node: str = "http://127.0.0.1:8420",
                 home: Path | str | None = None, timeout: float = 15.0):
        import requests
        self.game, self.room, self.node = game, room, node.rstrip("/")
        key = service_key(home or default_home(), make=False)
        if not key:
            raise ServiceError(f"no {KEY_FILE} in the node's home: start the arcade once first")
        self.http = requests.Session()
        self.http.headers[HEADER] = key
        self.timeout = timeout
        self.token: str | None = None
        self.me: dict | None = None
        self.members: list[dict] = []

    def __enter__(self) -> "Room":
        self.join()
        return self

    def __exit__(self, *exc) -> None:
        self.leave()

    def _post(self, path: str, body: dict) -> dict:
        r = self.http.post(self.node + path, json=body, timeout=self.timeout)
        try:
            out = r.json()
        except ValueError:
            raise ServiceError(f"{path}: HTTP {r.status_code}") from None
        if r.status_code != 200 or not out.get("ok"):
            raise ServiceError(f"{path}: {out.get('error') or out.get('detail') or r.status_code}")
        return out

    def join(self) -> dict:
        out = self._post("/realtime/join", {"game": self.game, "room": self.room})
        self.token, self.me, self.members = out["token"], out["me"], out.get("members", [])
        return out

    def send(self, data: Any) -> None:
        if self.token is None:
            self.join()
        if isinstance(data, (dict, list)):
            data = json.dumps(data, separators=(",", ":"))
        body = {"token": self.token}
        if isinstance(data, bytes):
            import base64
            body["b64"] = base64.b64encode(data).decode()
        else:
            body["data"] = str(data)
        self._post("/realtime/send", body)

    def leave(self) -> None:
        if self.token:
            try:
                self._post("/realtime/leave", {"token": self.token})
            except Exception:                  # noqa: BLE001 -- leaving is best effort
                pass
        self.token = None

    def events(self, reconnect: bool = True) -> Iterator[dict]:
        import requests
        wait = 1.0
        while True:
            try:
                if self.token is None:
                    self.join()
                with self.http.get(f"{self.node}/realtime/stream", params={"token": self.token},
                                   stream=True, timeout=(self.timeout, None)) as r:
                    if r.status_code != 200:
                        raise ServiceError(f"stream: HTTP {r.status_code}")
                    wait = 1.0
                    for line in r.iter_lines(decode_unicode=True):
                        if line and line.startswith("data: "):
                            yield json.loads(line[6:])
            except (requests.RequestException, ServiceError, ValueError):
                if not reconnect:
                    raise
            self.token = None                  # the node forgot us, or restarted: join again
            time.sleep(wait)
            wait = min(30.0, wait * 2)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m arcade.service",
                                 description="Sit in one room of your game and print what happens (a starting point for a service).")
    ap.add_argument("game")
    ap.add_argument("room")
    ap.add_argument("--node", default="http://127.0.0.1:8420")
    ap.add_argument("--echo", action="store_true", help="answer every message with {'t':'echo', ...}")
    a = ap.parse_args(argv)
    with Room(a.game, a.room, node=a.node) as room:
        print(f"in {a.game}/{a.room} as {room.me}", flush=True)
        for ev in room.events():
            print(json.dumps(ev), flush=True)
            if a.echo and ev.get("type") == "message" and ev["member"]["id"] != room.me["id"]:
                room.send({"t": "echo", "to": ev["member"]["id"], "data": ev.get("data")})
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
