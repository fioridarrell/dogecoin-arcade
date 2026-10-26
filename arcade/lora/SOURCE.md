# arcade/lora: the LoRa (Meshtastic) gateway

A copy of `gateway/arcade_lora` from https://github.com/dogecoinarcade/arcade-lora (commit ab6f5a9, 2026-10-26: the .mesh web's PAGE_REQ/PAGE),
unchanged, so every arcade runs the same gateway (Admin -> Meshtastic, `arcade/web/lora.py`). Its protocol is that
repository's PROTOCOL.md; its tests are `tests/test_lora_{frames,gateway,keys,txcheck}.py` here (the C++ T-Deck client
tests stay in arcade-lora: they need its hosttest build).

To take a newer gateway: copy `gateway/arcade_lora/*.py` over these files and the four Python test files over
`tests/test_lora_*.py` (imports `arcade_lora` -> `arcade.lora`), run them, and say so in COLLAB-NOTES.

Needs the `[lora]` extra (meshtastic, coincurve); Admin -> Meshtastic installs it at the node machine.
