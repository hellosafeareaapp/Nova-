# A.D.A. hub

`house_sim.py` running behind a local web server, with a dashboard that reads
its real state.

```
python hub.py            # then open the printed URL
python hub.py 8080       # if 8000 is taken
```

Python standard library only — no pip installs, no CDN, no external fonts or
assets. Runs in a-Shell on an iPhone and on a laptop, with no internet.

## Files

| file | |
|---|---|
| `hub.py` | the server, the sim thread and the dashboard. Single file. |
| `house_sim.py` | unchanged. `hub.py` imports the house and the rules from it and reimplements nothing. |

Both files must sit in the same folder. Nothing else in this repository is
involved — copy the `nova/` folder wherever it belongs.

## API

| | |
|---|---|
| `GET /` | the dashboard |
| `GET /state` | `source`, `voltage`, `tank`, `battery`, `draw`, every load with its state, and the last 30 events with timestamps |
| `POST /switch` | `{"name": "kettle", "state": true}` — the rules run afterwards and may override the request. The reply carries `requested`, `actual` and `overridden`. |

The simulation runs on a background thread at one simulated minute per real
second and loops the day continuously.

## Testing the staleness behaviour

The dashboard polls `/state` every second and tracks when the last successful
response actually arrived. Past five seconds it stops presenting the data as
live: the whole readout goes grey and dim, the top bar turns red and counts up
how old the data is, and a tap on a load is refused out loud instead of being
queued.

```
python hub.py            # dashboard goes live
Ctrl-C                   # ~5s later the display degrades and starts ageing
python hub.py            # it recovers on the next poll
```

The port is deliberately fixed and `SO_REUSEADDR` is set, so a stop/restart
comes back on the same address and the open dashboard reconnects on its own.
