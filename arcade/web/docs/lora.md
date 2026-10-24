# LoRa: reaching an arcade by radio (T-Decks and Meshtastic)

Any arcade can run a **LoRa gateway**: a Meshtastic radio plugged into the arcade's machine, through which arcade
**T-Decks** (LilyGO T-Deck and T-Deck Plus running the arcade firmware) reach the arcade with no internet of their
own. A T-Deck signs in with its own key, sees its coins, sends, and reads and writes sealed messages; the gateway
carries each request over LoRa and talks to the arcade the way a browser does.

## For operators: running a gateway

Everything is on **Admin → Meshtastic**. The gateway is off until you turn it on.

1. Plug a Meshtastic radio (a Heltec V3, or any radio on stock Meshtastic firmware) into the arcade's machine by USB.
2. If the page says the radio support is missing, press **Install the radio support** at the node machine. It adds
   two Python packages, `meshtastic` and `coincurve`, to the arcade's own Python.
3. Pick the radio's **serial port** (`/dev/ttyUSB0`, `/dev/ttyACM0`, ...), give the gateway a **name** T-Decks will
   show, and tick the **coins the T-Decks see**.
4. Press **Run the gateway**.

The page then shows:

- **The radio:** its node id, region and preset, how many nodes it hears, and frames sent, acknowledged and failed.
- **The T-Decks** that have said hello: who is signed in, who is online, and mail waiting to go out to each.

The **beacon** is off by default. T-Decks find gateways by asking, and a beacon only adds airtime on a busy channel.

If the radio is unplugged or resets, the gateway opens it again by itself and keeps every T-Deck's session.

Linked arcades see a **LoRa** badge beside an arcade that runs a gateway, on Admin's overview.

## Flashing a T-Deck

**Admin → Meshtastic → Flash a T-Deck** writes the arcade firmware from the page over USB. It needs Chrome or Edge
on a computer, which have Web Serial. The arcade fetches the firmware release and checks its sha256 before your
browser writes it.

For the first flash, on a T-Deck still running stock Meshtastic:

1. Switch the T-Deck on.
2. Hold the trackball down, plug in the USB cable, and let go after 2 seconds.
3. Press **Install the firmware** and pick the T-Deck's port.
4. When it says done, unplug the T-Deck and plug it in again.

After the first flash, the T-Deck goes into flash mode by itself. **Update only** writes just the app part, which is
faster.

The firmware is free software (GPL-3.0), built from Meshtastic's firmware and device-ui. Its source is at
[dogecoinarcade/firmware](https://github.com/dogecoinarcade/firmware/tree/arcade) and
[dogecoinarcade/device-ui](https://github.com/dogecoinarcade/device-ui/tree/arcade), branch `arcade`.
The gateway is [dogecoinarcade/arcade-lora](https://github.com/dogecoinarcade/arcade-lora).
