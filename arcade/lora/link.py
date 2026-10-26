"""A Meshtastic radio as a frame pipe: USB serial, TCP or Bluetooth.

Frames travel as PRIVATE_APP direct messages; stock firmware encrypts those
end to end (PKC) and every Meshtastic node relays them.

One frame is in the air at a time: each send waits for the other radio's
ack (or a timeout) before the next goes. A 200-byte LongFast packet is about
1.5 s of airtime, and firing several back to back while the T-Deck answers
with its own acks made them collide (MAX_RETRANSMIT, 2026-10-23). Received
frames are handled on a worker thread, never the radio library's reader
thread, so a handler that sends can wait for acks the reader delivers.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from pubsub import pub
from meshtastic.protobuf import portnums_pb2

from . import frames as F

log = logging.getLogger("arcade_lora.link")
# seconds to wait for the radio's verdict on a frame: Meshtastic retries itself 3 times first, and on a weak LongFast
# link that took ~30 s -- with 25 s the gateway sent the frame again while the radio was still trying (and the T-Deck
# had it), doubling the airtime (2026-10-26: 2,301 "no ack" since 10-23, each followed ~5 s later by MAX_RETRANSMIT).
ACK_WAIT = 75.0
AFTER_ACK = 0.3         # a breath between frames so relays and the T-Deck can answer


class MeshLink:
    def __init__(self, *, port: str | None = None, ble: str | None = None, host: str | None = None):
        if ble:
            from meshtastic.ble_interface import BLEInterface
            self.iface = BLEInterface(ble)
        elif host:
            from meshtastic.tcp_interface import TCPInterface
            self.iface = TCPInterface(host)
        else:
            from meshtastic.serial_interface import SerialInterface
            self.iface = SerialInterface(port)
        self.my_node = self.iface.myInfo.my_node_num
        self.handlers = []
        self.heard_handlers = []
        self.send_lock = threading.Lock()
        self.inbox: queue.Queue = queue.Queue()
        self.stats = {"sent": 0, "acked": 0, "failed": 0}
        self.worker = threading.Thread(target=self._work, name="arcade-frames", daemon=True)
        self.worker.start()
        pub.subscribe(self._on_receive, "meshtastic.receive")

    def on_frame(self, handler):
        """handler(node_num, frame_bytes) for every arcade frame heard for us."""
        self.handlers.append(handler)

    def on_heard(self, handler):
        """handler(node_num) for every packet heard from a node, of any kind."""
        self.heard_handlers.append(handler)

    def _on_receive(self, packet, interface):
        # the radio library's reader thread: only hand over, never block here
        if interface is not self.iface:
            return
        node = packet.get("from")
        d = packet.get("decoded") or {}
        frame = None
        if d.get("portnum") in ("PRIVATE_APP", F.PORTNUM):
            payload = d.get("payload") or b""
            if payload and payload[0] == F.MAGIC:
                frame = bytes(payload)
                log.debug("rx %d B from %08x snr=%s", len(payload), node, packet.get("rxSnr"))
        self.inbox.put((node, frame))

    def _work(self):
        while True:
            node, frame = self.inbox.get()
            for h in self.heard_handlers:
                try:
                    h(node)
                except Exception:
                    log.exception("heard handler failed")
            if frame is None:
                continue
            for h in self.handlers:
                try:
                    h(node, frame)
                except Exception:
                    log.exception("frame handler failed")

    def send(self, node: int, frame: bytes) -> bool:
        """One frame to one node; True once that node's radio acked it (one more try if not)."""
        return self._send_once(node, frame) or self._send_once(node, frame)

    def _send_once(self, node: int, frame: bytes) -> bool:
        with self.send_lock:
            done = threading.Event()
            result = {"ok": False}

            def answered(packet):
                routing = (packet.get("decoded") or {}).get("routing") or {}
                result["ok"] = routing.get("errorReason", "NONE") == "NONE"
                if not result["ok"]:
                    log.info("frame to %08x: %s", node, routing.get("errorReason"))
                done.set()

            self.iface.sendData(frame, destinationId=node, portNum=portnums_pb2.PortNum.PRIVATE_APP,
                                wantAck=True, onResponse=answered, onResponseAckPermitted=True)
            self.stats["sent"] += 1
            if not done.wait(ACK_WAIT):
                log.info("frame to %08x: no ack in %.0f s", node, ACK_WAIT)
            self.stats["acked" if result["ok"] else "failed"] += 1
            time.sleep(AFTER_ACK)
            return result["ok"]

    def broadcast(self, frame: bytes):
        with self.send_lock:
            self.iface.sendData(frame, portNum=portnums_pb2.PortNum.PRIVATE_APP, wantAck=False)
            time.sleep(2.0)

    def close(self):
        pub.unsubscribe(self._on_receive, "meshtastic.receive")
        self.iface.close()
