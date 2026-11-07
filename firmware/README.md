# A.D.A. node (ESP32)

The ESP32 that replaces `house_sim`'s pretend hardware. It reports to
`hub.py` once a second, and the hub tells it which loads should be on.

## Step 1: talk to the hub, no mains

Nothing in this step touches 230 V. The relays click, but **nothing is
connected to their screw terminals.**

- **BOOT button = power source.** Each press goes NEPA → Gen → Inverter →
  No power. The hub logs the change and its rules react; on Inverter they
  shed the pump and kettle.
- **Relay 1 = borehole pump, relay 2 = kettle.** They follow the hub:
  switch the kettle on from the dashboard and relay 2 clicks; go to
  Inverter and it clicks off again because the rule sheds it.
- **Fail-safe.** If the hub is silent for 10 seconds, every relay goes to
  its safe state (pump and kettle off), so a crashed phone can't leave
  the pump running and overflow the tank.
- **Onboard LED.** Slow blink: talking to the hub. Fast blink: Wi-Fi is
  up but no hub. Solid: no Wi-Fi.

### Wiring

| Relay module | ESP32 DevKit |
| --- | --- |
| VCC | VIN (5 V from USB) |
| GND | GND |
| IN1 | GPIO 26 |
| IN2 | GPIO 27 |

Many cheap 5 V relay boards work from the ESP32's 3.3 V signal. If the
relay LED lights but it won't click, or it never switches, you need a
board that accepts 3.3 V inputs (or one with a separate JD-VCC jumper).

### Flash it

1. Install the [Arduino IDE](https://www.arduino.cc/en/software).
2. **Settings → Additional boards manager URLs**, add
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
   Then **Tools → Board → Boards Manager**, install **esp32 by Espressif**.
3. Start the hub on your phone or laptop: `python hub.py`. Note two lines
   it prints: the `(same wifi)` address and the `device key`.
4. Open `firmware/ada_node/ada_node.ino` and fill in the CONFIG block:
   Wi-Fi name and password, `HUB_HOST` (the address from step 3, without
   `http://` or the port), and `DEVICE_KEY`.
5. **Tools → Board → ESP32 Dev Module**, pick the port, and Upload. If the
   upload stalls at "Connecting…", hold BOOT until it starts.
6. **Tools → Serial Monitor** at 115200. You should see the Wi-Fi address,
   then `[hub] connected`.

### Check it works

- `GET /state` on the hub now has a `device` block with `"online": true`.
- Press BOOT: the hub's source changes and an event appears.
- Unplug the ESP32: after 5 seconds `device.online` turns false and the hub
  logs `DEVICE OFFLINE`.
- Stop the hub: after 10 seconds the relays fall back to their safe states.

The phone running the hub and the ESP32 must be on the same Wi-Fi. If the
hub runs on a phone hotspot, join the ESP32 to that hotspot.

## The protocol

`POST /device` with header `X-Device-Key: <key>`:

```json
{"id": "ada-node-01", "source": "grid", "relays": {"pump": false, "kettle": true}, "rssi": -58, "uptime": 312}
```

`source` is one of `grid`, `gen`, `inverter`, `none`. The reply:

```json
{"ok": true, "loads": {"pump": false, "kettle": false}, "cmd": "pump=0;kettle=0"}
```

`cmd` repeats `loads` in a form the firmware can read without a JSON
library. While the device is reporting it owns the power source and the
scripted outage pauses; the rules in `house_sim.py` still decide the loads.
The key is made on first run and kept in `device_key.txt` next to `hub.py`
(not committed). Delete that file to make a new one.

## Next steps

2. **Real sensors.** Tank level with an ultrasonic sensor (low voltage, safe
   to do yourself), then mains voltage and source detection with isolated
   modules such as a PZEM-004T.
3. **Real loads.** Relays or contactors on the pump and freezer. That is
   230 V wiring and should be done by an electrician.
