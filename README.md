# A.D.A. hub

`house_sim.py` running behind a local web server.

```
python hub.py            # then open the printed URL
python hub.py 8080       # if 8000 is taken
```

Python standard library only — no pip installs, no CDN, no external fonts or
assets. Runs in a-Shell on an iPhone and on a laptop, with no internet.

## There is no front end right now

The dashboard was deleted on purpose. What is left is the part that was
worth keeping: the simulation, the rules, the event feed, and the settings
engine the next client will be built on. `GET /` serves an unstyled page
that lists the endpoints and nothing else.

**The staleness behaviour went with it and has to be rebuilt.** It is the
one requirement that matters most, so whatever comes next has to poll
`/state` every second, track when the last *successful* response arrived,
and past five seconds visibly stop presenting the data as live, say how old
it is, and refuse to queue a switch instead of silently holding it.

## Files

| file | |
|---|---|
| `hub.py` | the server, the sim thread and the settings engine. Single file. |
| `house_sim.py` | unchanged. `hub.py` imports the house and the rules from it and reimplements nothing. |

Both files must sit in the same folder.

## API

| | |
|---|---|
| `GET /` | a placeholder listing these endpoints |
| `GET /state` | `source`, `voltage`, `tank`, `battery`, `draw`, every load with its state, and the last 30 events with timestamps |
| `GET /settings` | the resolved settings: vars, type, wallpaper |
| `GET /tokens.css` | those settings as CSS custom properties, ready to link |
| `GET /assets/<name>` | a font or image dropped in `assets/` |
| `POST /switch` | `{"name": "kettle", "state": true}` — the rules run afterwards and may override the request. The reply carries `requested`, `actual` and `overridden`. |
| `POST /settings` | a partial settings object, e.g. `{"vars": {"ink": "#111"}}` |
| `POST /device` | the ESP32's once-a-second report (header `X-Device-Key`). The reply says which loads should be on. See [`firmware/README.md`](firmware/README.md). `GET /state` carries a `device` block with `online` and `age`. |

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
