# A.D.A. hub — Always-on Domestic Automation

`house_sim.py` running behind a local web server.

```
python hub.py              # then open the printed URL on any phone or laptop
python hub.py 8080         # if 8000 is taken
python hub.py --seed-week  # add a sample week of history for a demo report
```

Python standard library only — no pip installs. Runs in a-Shell on an iPhone
and on a laptop, with no internet (phone alerts need internet; nothing else
does).

## The dashboard

`GET /` serves the dashboard from `mockup/index.html`. Opened from the hub
it runs on **live data**: it polls `/state` every second, switches go to
`POST /switch` (and the rules may overrule them, with the reason shown), and
the header says *Live from hub* and whether the ESP32 controller is online.
Opened anywhere else (GitHub Pages, a file) it is the demo and says *Demo*.
You can also point a copy at a hub with `?hub=http://192.168.1.50:8000`.

Staleness is kept: past five seconds without a good answer the dashboard
says the data is not live and how old it is, and refuses to switch anything
instead of queueing it.

Live today: power source, voltage, battery, water tank, diesel, every load
(freezer, pump, TV, kettle, lights, fans, both ACs, iron), events, alerts and
the report. Still demo-only in live mode: prepaid meter units, solar,
cameras, weather and the estate view's other buildings.

## People, PINs and roles

Until anyone has a PIN the hub is open, as before, and the header says
**No PINs**. Tap it, create the owner with a 4–8 digit PIN, and from then on
every phone needs a PIN to see or switch anything (the ESP32 is unaffected,
it uses the device key).

| role | can |
|---|---|
| Resident | see everything, switch nothing |
| Caretaker | switch loads, log diesel deliveries, mark the generator serviced |
| Manager | all of that, plus building setup, phone alerts and adding people |
| Owner | everything; there is always at least one owner |

Nobody can create or remove someone above their own role. PINs are stored
hashed (PBKDF2) in `users.json` next to `hub.py` (not committed); five wrong
PINs lock that device out for a minute. In the demo, **People** lets you
switch between roles to show what each one can do.

## Phone alerts

Diesel dropping while the generator is off, diesel low or finished, battery
low or flat, water tank low with no power to pump, tank overflowing, voltage
spikes, and the controller going offline or coming back. Each is raised once
and then held for a simulated hour.

Set up in the dashboard (**Alerts → Phone alerts**):

1. Install the free **ntfy** app on the phone (Android or iPhone).
2. Subscribe to a long, hard-to-guess topic, e.g. `ada-x7k2p9q4`.
3. Put the same topic in the dashboard, save, press **Send test**.

Telegram works too (bot token and chat ID). Settings live in
`hub_config.json` next to `hub.py` (not committed); tokens are write-only
from the dashboard.

## Building setup, generator and deliveries

**Power → Building setup** sets the generator tank size, litres per running
hour, battery bank size, diesel price, generator service interval and the
alert levels. They are saved in `hub_config.json` and every estimate, alert
and report uses them.

The hub keeps an hour meter for the generator. The diesel panel shows the
hours left to the next service, **Mark serviced** restarts the countdown,
and an alert goes out when it is due.

**Log delivery** records what the vendor says (litres, price, vendor). The
hub matches it to the rise it measures at the tank (logged before or after,
within two simulated hours); a gap of 5% or more (2 L at least) raises a
"delivery came up short" alert. Deliveries and the shortfall are in the
report.

## Installer setup

**Power → Building setup → Installer setup** walks a new building through
six steps: what you need, the building's numbers, the owner PIN, pairing
the controller (it shows the device key and waits until the ESP32 reports
in, with its Wi-Fi strength), testing every relay (on for three seconds,
then mark it worked or not), and phone alerts. Owners and managers only.

## Without internet

Nothing needs the internet except phone alerts. The dashboard is one
self-contained file (no CDN, no web fonts), the hub is standard-library
Python, and phones reach it over the building's Wi-Fi even when the
internet is down. Alerts that can't be sent still show in the dashboard.

## Reports

**This week → Weekly report / Monthly** opens a printable page: diesel used
and its cost in naira, hours on NEPA, generator, inverter and no power,
unexplained diesel loss, refills, pump hours, lowest battery, voltage spikes,
controller downtime, a day-by-day chart and table, and a short "worth
looking at" list. Print it or save it as a PDF. History is kept per day in
`history.json` (not committed) and survives restarts. `--seed-week` fills
the days before today with sample figures, and the report says so.

Note the hub still runs one simulated minute per real second, so a "day"
passes in 24 minutes. Real-time clocks come with the real install.

## Files

| file | |
|---|---|
| `hub.py` | the server, the sim thread and the settings engine. Single file. |
| `house_sim.py` | the house and the rules. `hub.py` imports them and reimplements nothing. Now includes both ACs and the iron, shed on inverter like the kettle. |
| `mockup/index.html` | the dashboard, served at `/` |
| `firmware/` | the ESP32 controller; it finds the hub on its own (UDP broadcast on port 41234) |

Both files must sit in the same folder.

## API

| | |
|---|---|
| `GET /` | the dashboard (`mockup/index.html`) |
| `GET /api` | a plain page listing these endpoints |
| `GET /report.json?days=7` | per-day history and totals the report is built from |
| `GET /config` | site name, diesel price, whether phone alerts are set up (no secrets) |
| `POST /config` | `site_name`, `diesel_price`, `diesel_cap`, `gen_lph`, `battery_kwh`, `service_hours`, `alert_diesel_l`, `alert_battery_pct`, `alert_tank_pct`, `ntfy_topic`, `telegram_token`, `telegram_chat` |
| `POST /notify/test` | sends a test alert |
| `GET /setup` | managers and owners: device key, hub addresses and controller status for pairing |
| `GET /me` | who this request is signed in as, and whether PINs are on |
| `POST /login` | `{"pin": "2468"}` returns a token; send it as `X-ADA-Token` |
| `POST /logout` | ends this token's session |
| `GET /users`, `POST /users` | managers and owners: list, `{"action": "add", "name", "role", "pin"}` or `{"action": "remove", "name"}` |
| `POST /delivery` | `{"litres": 50, "price": 1300, "vendor": "Sunny Oil"}`; checked against the measured refill |
| `POST /service` | `{"note": "oil + filter"}`; restarts the generator service countdown |
| `POST /source` | demo: `{"source": "inverter"}`; refused while the ESP32 reports the source |
| `POST /fuel` | demo: `{"delta": -5}` refill or siphon; refused once a real fuel sensor reports |
| `GET /state` | `source`, `voltage`, `tank`, `battery`, `fuel` (litres), `draw`, every load with its state, the last 30 events, recent `alerts` and the `device` block |
| `GET /settings` | the resolved settings: vars, type, wallpaper |
| `GET /tokens.css` | those settings as CSS custom properties, ready to link |
| `GET /assets/<name>` | a font or image dropped in `assets/` |
| `POST /switch` | `{"name": "kettle", "state": true}` — the rules run afterwards and may override the request. The reply carries `requested`, `actual` and `overridden`. |
| `POST /settings` | a partial settings object, e.g. `{"vars": {"ink": "#111"}}` |
| `POST /device` | the ESP32's once-a-second report (header `X-Device-Key`). It may carry `readings` (`fuel_l`, `battery`, `tank`, `voltage`) from real sensors. The reply says which loads should be on. See [`firmware/README.md`](firmware/README.md). `GET /state` carries a `device` block with `online` and `age`. |

The simulation runs on a background thread at one simulated minute per real
second and loops the day continuously.

## Settings

`settings.json` sits next to `hub.py` and holds only what you set. There are
no palettes, no named looks and no materials — those were one person's taste
and they went out with the design. What is left is the mechanism:

```json
{ "vars": { "ink": "#111", "panel": "#fff", "radius": "14px" } }
```

```css
/* GET /tokens.css */
:root{--ink:#111;--panel:#fff;--radius:14px;--scale:1.0;}
```

You name the variables. The server has no opinion about what they are or
what you call them — link `/tokens.css` from whatever you build and every
value is yours. `POST /settings` with `{"vars": {"name": null}}` removes one.

- **vars** — any CSS custom property. A name must look like an identifier and
  a value cannot contain `; { } < > \ @`, so nothing written here can break
  out of the rule it sits in.
- **type** — `scale` (0.75–1.6, served as `--scale`) and `faces`, which
  `@font-face`s any font file you drop in `assets/`
- **wallpaper** — an image in `assets/`, served as a `body` background

A mangled or hand-broken `settings.json` never stops the server starting;
anything that does not make sense is simply left out.
