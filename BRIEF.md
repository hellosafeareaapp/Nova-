# The hub brief

> **This is a reconstruction, not the original.** `hubbuildbrief.pdf` was
> attached to the conversation that produced this code and is no longer
> available to the session. The lines in quotation marks below are the
> brief's own words, preserved verbatim; everything else is a summary of
> its requirements and may be less precise than the original. **If you
> still have the PDF, trust it over this file.**

Build `hub.py`: the existing `house_sim.py` running behind a local web
server, with a dashboard that reads its real state.

## Constraints

- "Python standard library only. No pip installs. It has to run in a-Shell
  on an iPhone as well as on a laptop."
- "Single file. `python hub.py`, then open the printed localhost URL."
- "Import the house and rules from house_sim.py. Do not duplicate that
  logic."
- The simulation runs on a background thread. One real second is one
  simulated minute, and the day loops continuously.

## The API

| | |
|---|---|
| `GET /` | the dashboard |
| `GET /state` | source, voltage, tank, battery, total draw, every load with its on/off state, and the last 30 events with timestamps |
| `POST /switch` | a load name and a desired state |

On `POST /switch` the rules run afterwards and may override the request.
"That override is correct behaviour and should be visible in the event log."

## Staleness

> "This matters more than the rest and is the reason the dashboard exists."

Poll `/state` every second and track when the last **successful** response
actually arrived. If that is more than five seconds ago:

- "the whole display must visibly change to show it is no longer live, and
  say how long ago the data is from"
- while stale, "toggling a load must not silently queue"

## On how it should look

> "Do not build a generic dashboard of identical rounded cards. This is an
> instrument panel for a house under unreliable power, read at night."

## Do not

- "Add features not listed here. No door locks, no scheduling, no accounts."
- "Use any external font, CDN, or asset. It must work with no internet."
