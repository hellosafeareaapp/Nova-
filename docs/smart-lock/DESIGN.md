# A.D.A. Smart Access: smart lock system (future addition)

Status: **design, not built.** The dashboard has a working preview (Security
page, demo mode). Nothing here is wired to real hardware yet.

## Why

In a Nigerian home or estate the gate and doors are opened all day by
people the owner is not there to see: the gateman, drivers, cleaners,
artisans, guests, deliveries. Keys get copied and are never collected back,
and nobody knows who opened what. A.D.A. already runs the building's power
and water, has phone alerts, roles and a log, so access is the natural next
piece.

## Goals

1. Open the gate and doors with a **PIN, card, phone or one-time visitor code**.
2. **Keep working through NEPA outages** and when the internet or the hub is down.
3. **Never trap anyone**: a mechanical key and inside handle always work.
4. Every opening is **logged with who and how**, and the unusual ones raise a phone alert.
5. Use A.D.A.'s existing **roles, alerts, log and reports**. No second app.

## What gets installed

| Door | Lock | Why this one |
|---|---|---|
| Pedestrian gate | 12 V **fail-secure electric strike** or gate lock, outdoor keypad + card reader | Stays locked if power fails; mechanical key override |
| Vehicle gate | **Dry contact** into the existing gate motor controller + a gate position sensor | Most estates already have a motor; we only add control and status |
| Front door | **Motorised deadbolt** retrofit or a mortise lock with electric strike | Inside thumb-turn always works (fire safety) |
| Back / kitchen door | Same as front door | |
| Boys' quarters | Electric strike + keypad | Gateman and staff access without the house key |

Magnetic locks are **not** recommended on exit doors: they fail open on power
loss and need extra fire-safety hardware. Where one is used (glass doors), it
needs an exit button and a break-glass release.

## The lock node

One small node per door, the same family as the existing ESP32 controller:

- ESP32 (Wi-Fi, plus ESP-NOW or LoRa for gates far from the house, or RS485 cable)
- Keypad + 13.56 MHz card reader (MIFARE DESFire tags: hard to clone, unlike the old 125 kHz cards)
- Door contact (reed switch): knows if the door is actually closed, not just locked
- Exit button input, tamper switch on the cover
- MOSFET/relay output for the strike or motor, with a flyback diode
- 12 V supply with its **own 7 Ah sealed battery** and charger

### Power

Locks are on 12 V DC with their own battery, charged from the A.D.A. panel
(which runs from NEPA, generator or inverter). A strike draws almost nothing
while idle, so a 7 Ah battery holds a door for days with no power at all.
A.D.A.'s load-shedding rules **never** touch locks or their chargers. A low
lock battery raises an alert.

### When things fail

| Failure | What happens |
|---|---|
| NEPA off | Nothing visible. Locks run on their own battery |
| Hub or Wi-Fi down | Node keeps working from its **cached** PINs, cards and valid visitor codes; events are queued and sent when the hub is back |
| Lock battery flat | Fail-secure doors stay locked; open with the mechanical key. Exit side always opens |
| Node broken | Mechanical key |
| Fire | Inside handle / thumb-turn and exit buttons always open; nothing can lock people in |

## Ways to open

- **PIN** at the keypad (each person has their own, from A.D.A.'s People screen)
- **Card or key-tag** (enrolled at the dashboard, cancelled in one tap if lost)
- **Phone**: Unlock in the dashboard, which asks to confirm, and only works for roles allowed on that door
- **Visitor code**: a one-time or time-limited code for a driver, artisan or guest, e.g. *"Emeka (plumber) · gate only · today 10:00–16:00 · 2 uses"*, shared on WhatsApp straight from the dashboard
- **Gateman approval** (later): a visitor presses the gate bell, the gateman's phone shows a camera snapshot and **Allow / Deny**

## Who can open what (reuses A.D.A. roles)

| Role | Gate | Front / back door | Boys' quarters | Issue visitor codes |
|---|---|---|---|---|
| Owner | ✓ | ✓ | ✓ | ✓ |
| Manager | ✓ | ✓ | ✓ | ✓ |
| Caretaker / gateman | ✓ | — | ✓ | gate only |
| Resident | ✓ | own door | — | own guests |
| Visitor code | as issued | as issued | as issued | — |

## Alerts (phone, via the existing ntfy / Telegram path)

- **Door forced open**: the door contact opened with no valid unlock (high priority)
- **Door left open** for more than 2 minutes
- **5 wrong PINs** at a keypad: that keypad locks out for 5 minutes
- **Tamper**: keypad or node cover removed
- **Lock battery low** / **lock node offline**
- **Night rule**: the gate opened between 23:00 and 05:00

## Working with the rest of A.D.A.

- **Night check at 22:00**: lists any door not locked and offers *Lock all*; security lights on
- **Arrival at night**: a valid PIN at the gate turns on the compound lights for 3 minutes
- **Everyone out**: when the last resident locks up with *Away*, A.D.A. sheds non-essential loads (ACs, TV, iron) automatically
- The **weekly report** gets an access section: openings per door, visitor codes used, any forced or left-open events

## Security

- Unlock commands from the hub are signed per node (HMAC with a counter), so a recorded command cannot be replayed
- PINs are stored hashed; card UIDs only accepted from DESFire tags
- Nothing can be unlocked from the internet unless the hub allows it, and remote unlock is off by default
- Every unlock, failed attempt and code is in the log with who, how and when

## Hub API (planned)

| | |
|---|---|
| `GET /locks` | every door: locked, open/closed, battery, node online |
| `POST /locks/<id>` | `{"action": "unlock", "seconds": 5}` or `"lock"`; role checked |
| `POST /codes` | create a visitor code `{name, doors, from, to, uses}`; returns the code |
| `DELETE /codes/<id>` | cancel a code |
| `POST /lock-node` | the node's report: door state, battery, events (header `X-Device-Key`, same as the power node) |

## Parts per door (estimates, November 2026)

| Part | Estimate |
|---|---|
| ESP32 node + MOSFET board + enclosure | ₦15k – ₦30k |
| Keypad + DESFire card reader (outdoor rated for gates) | ₦25k – ₦70k |
| Fail-secure electric strike, or motorised deadbolt | ₦25k – ₦120k |
| Door contact, exit button, tamper switch | ₦5k – ₦12k |
| 12 V supply + charger + 7 Ah battery | ₦20k – ₦40k |
| Cards / key-tags (10) | ₦8k – ₦20k |
| **Per door** | **≈ ₦100k – ₦290k** |

Vehicle gate: about ₦20k – ₦50k extra if a gate motor is already installed.

## Build order

1. **Pedestrian gate**: keypad, strike, door contact, visitor codes. Biggest everyday win.
2. **Front door** with motorised deadbolt and phone unlock.
3. **Gateman approval** with a camera snapshot, and the vehicle gate.
4. Access section in the weekly report; night and away automations.
