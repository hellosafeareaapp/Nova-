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
| `GET /settings` | the resolved settings: look, theme, type, surface, wallpaper |
| `GET /tokens.css` | those settings as CSS custom properties, ready to link |
| `GET /assets/<name>` | a font or image dropped in `assets/` |
| `POST /switch` | `{"name": "kettle", "state": true}` — the rules run afterwards and may override the request. The reply carries `requested`, `actual` and `overridden`. |
| `POST /settings` | a partial settings object, e.g. `{"look": "retrowave"}` |

The simulation runs on a background thread at one simulated minute per real
second and loops the day continuously.

## Settings

`settings.json` sits next to `hub.py` and is sparse — it holds only what you
changed. Four layers resolve it, each overridable by the next: the defaults,
the named **look**, the **material**'s surface recipe, and the file itself.
So `{"look": "retrowave"}` is a complete theme in 26 bytes, and
`{"look": "retrowave", "surface": {"glow": 0.1}}` is that theme turned down.

- **looks** — `instrument`, `retrowave`, `amber`, `paper`, `frosted`
- **materials** — `card`, `chalk`, `neon`, `paper`, `frosted`: fill opacity,
  where the stroke takes its colour from, width, radius, lift and glow
- **mode** — `dark` or `light`; each carries its own shadow weights
- **type** — families, a scale, and `@font-face` for any font file in `assets/`
- **wallpaper** — an image in `assets/`, with a dim and a blur

A mangled or hand-broken `settings.json` never stops the server starting;
anything that does not make sense reverts to its default.

Those names came from a round of design work whose output was thrown away,
so treat the five looks as starting points, not as a finished set.
