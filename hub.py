"""
A.D.A. hub: house_sim running behind a local web server, with a dashboard
that reads its real state.

    python hub.py            # then open the printed URL
    python hub.py 8080       # if 8000 is already taken

Standard library only, single file, no network access needed. Runs in a-Shell
on an iPhone and on a laptop.

The house and the rules come from house_sim.py. Nothing in this file
reimplements them: the simulation is stepped exactly as house_sim.main does,
and every switch decision still goes through house_sim.run_rules.
"""

import contextlib
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import house_sim
except ImportError:
    sys.exit("hub.py needs house_sim.py in the same folder.")


# ---------------------------------------------------------------- config

PORT = 8000
TICK_SECONDS = 1.0        # one real second is one simulated minute
EVENT_LIMIT = 30          # /state carries this many events, newest first
LOG_LIMIT = 500           # house_sim.log is a display buffer; keep it bounded

# 260V is the threshold rule_protect_from_spike already acts on, so the
# dashboard marks the same line the rules do rather than inventing its own.
SAFE_MIN = 200.0
SAFE_MAX = 260.0

# scenario_night_outage triggers on absolute minutes, from 21:30 on the first
# day through to 06:15 on the second. Rewinding a whole day once we are past
# the last of them replays the entire scenario forever, and because the rewind
# is exactly 24h the clock the dashboard shows never jumps.
DAY = 24 * 60
WRAP_AT = 32 * 60         # 08:00 on day two, safely past the 06:15 trigger


# ---------------------------------------------------------------- the house

class HubHouse(house_sim.House):
    """house_sim.House plus a structured event feed.

    house_sim formats its log as fixed-width terminal text. The dashboard
    needs the pieces separately, so we record them as the events happen
    instead of parsing the text back apart.
    """

    def __init__(self):
        super().__init__()
        self.events = []

    def event(self, what, why=""):
        super().event(what, why)
        self.events.append({"time": self.clock, "what": what, "why": why})
        if len(self.events) > EVENT_LIMIT:
            del self.events[:-EVENT_LIMIT]
        if len(self.log) > LOG_LIMIT:
            del self.log[:-LOG_LIMIT]


class _Quiet:
    """Swallows house_sim's print()ing. The dashboard is the log now, and a
    flood of stdout would bury the URL this script prints on startup."""

    def write(self, _):
        return 0

    def flush(self):
        pass


@contextlib.contextmanager
def quiet():
    # redirect_stdout swaps a process-global, so every caller holds LOCK and
    # the redirect windows can never overlap.
    with contextlib.redirect_stdout(_Quiet()):
        yield


HOUSE = HubHouse()
LOCK = threading.Lock()


def simulate():
    """One real second is one simulated minute, looping the day forever."""
    next_tick = time.monotonic()
    while True:
        next_tick += TICK_SECONDS
        with LOCK:
            with quiet():
                HOUSE.tick()
                house_sim.scenario_night_outage(HOUSE)
                house_sim.run_rules(HOUSE)
            if HOUSE.minute >= WRAP_AT:
                HOUSE.minute -= DAY
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.monotonic()   # fell behind; stop trying to catch up


def snapshot():
    """The whole visible state of the house. Caller holds LOCK."""
    voltage = HOUSE.voltage
    return {
        "clock": HOUSE.clock,
        "source": HOUSE.source,
        "voltage": round(voltage, 1),
        "voltage_ok": SAFE_MIN <= voltage <= SAFE_MAX,
        "tank": round(HOUSE.tank, 1),
        "battery": round(HOUSE.battery, 1),
        "draw": HOUSE.draw,
        "loads": [
            {"name": name, "watts": load["watts"], "on": load["on"]}
            for name, load in HOUSE.loads.items()
        ],
        "events": list(reversed(HOUSE.events[-EVENT_LIMIT:])),
    }


# ---------------------------------------------------------------- server

class Hub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"      # keep-alive, so a 1Hz poll is cheap
    server_version = "ADAHub/1.0"
    timeout = 30

    def log_message(self, *_args):
        pass                            # one request a second would be noise

    def _send(self, code, body, ctype):
        raw = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass                        # the phone wandered off mid-response

    def _json(self, code, payload):
        self._send(code, json.dumps(payload), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/state":
            with LOCK:
                payload = snapshot()
            self._json(200, payload)
        elif path == "/favicon.ico":
            self._send(204, "", "text/plain")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/switch":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            name = body["name"]
            want = bool(body["state"])
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "expected JSON {name, state}"})
            return

        with LOCK:
            known = name in HOUSE.loads
            if known:
                with quiet():
                    HOUSE.switch(name, want, "from dashboard")
                    # The rules get the last word here exactly as they would on
                    # the real hub. When they disagree they switch it back and
                    # their reason lands in the event log.
                    house_sim.run_rules(HOUSE)
                actual = HOUSE.is_on(name)
                payload = snapshot()

        if not known:
            self._json(404, {"error": "no such load: " + str(name)})
            return
        payload["requested"] = want
        payload["actual"] = actual
        payload["overridden"] = actual != want
        self._json(200, payload)


# ---------------------------------------------------------------- dashboard

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>The house</title>
<style>
:root{
  --bg:#14110d;          /* a dim room, not a black screen */
  --panel:#1b1712;
  --rule:#2a231b;
  --sunk:#241e17;
  --ink:#f2e9dc;         /* warm paper, not clinical white */
  --dim:#9c8f7f;
  --ok:#7cb27c;
  --warn:#dd8b45;
  --bad:#c8544a;
  --gold:#d9b25c;
  --src:#9c8f7f;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --serif:"Iowan Old Style",Palatino,"Palatino Linotype",Georgia,"Times New Roman",serif;
}
body[data-source="grid"]{--src:var(--ok);}
body[data-source="gen"]{--src:var(--warn);}
body[data-source="inverter"]{--src:var(--gold);}
body[data-source="none"]{--src:var(--bad);}

*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html{background:var(--bg);}
body{
  margin:0 auto; max-width:540px;
  background:var(--bg); color:var(--ink);
  font:400 16px/1.4 var(--sans);
  font-variant-numeric:tabular-nums;
  -webkit-font-smoothing:antialiased;
  padding-bottom:calc(34px + env(safe-area-inset-bottom));
}

/* --- how old this is. never hidden, and it speaks in sentences ---------- */
#statusbar{
  position:sticky; top:0; z-index:20;
  display:flex; align-items:center; gap:10px;
  padding:12px 18px; padding-top:calc(12px + env(safe-area-inset-top));
  background:#100d0a; border-bottom:1px solid var(--rule);
  font:400 13.5px/1.35 var(--serif); color:var(--dim);
}
#statusbar .dot{width:7px; height:7px; border-radius:50%; background:var(--ok); flex:none;}
body.stale #statusbar{
  background:var(--bad); color:#fff5f0; font-size:14.5px;
  border-bottom-color:var(--bad);
  padding-top:calc(14px + env(safe-area-inset-top)); padding-bottom:14px;
}
body.stale #statusbar .dot{background:#fff5f0; animation:pulse 1.4s ease-in-out infinite;}
@keyframes pulse{50%{opacity:.25}}

/* --- stale: the readings stop looking like readings --------------------- */
#readouts{transition:filter .2s linear, opacity .2s linear;}
body.stale #readouts{filter:grayscale(.92) brightness(.52); opacity:.55;}

/* --- what the house is running on --------------------------------------- */
.hero{position:relative; overflow:hidden; padding:26px 18px 23px; border-bottom:1px solid var(--rule);}
.hero::before{
  content:""; position:absolute; inset:-50% -30% auto -30%; height:220%;
  background:radial-gradient(58% 48% at 24% 0%, var(--src), transparent 70%);
  opacity:.14; pointer-events:none;
}
.hero > *{position:relative; z-index:1;}
.eyebrow{font:400 14.5px var(--serif); color:var(--dim);}
.source{
  margin-top:5px; color:var(--src);
  font:600 clamp(42px,13.5vw,60px)/1.02 var(--sans); letter-spacing:-.015em;
}
.sourcenote{margin-top:11px; font:400 15.5px/1.45 var(--serif); color:var(--dim);}

/* --- the battery --------------------------------------------------------- */
.batt{padding:20px 18px 19px; border-bottom:1px solid var(--rule);}
.k{font:400 14.5px var(--serif); color:var(--dim);}
.bigrow{display:flex; align-items:baseline; margin-top:4px;}
.big{font:600 clamp(46px,15vw,62px)/1 var(--sans); letter-spacing:-.02em;}
.pct{margin-left:3px; font-size:22px; font-weight:500; color:var(--dim);}
.battwhy{margin-left:auto; font:400 15px var(--serif); color:var(--dim);}
.bar{margin-top:15px; height:9px; background:var(--sunk); border-radius:5px; overflow:hidden;}
.bar > i{
  display:block; height:100%; width:0; border-radius:5px; background:var(--ok);
  transition:width .4s ease, background .3s linear;
}

/* --- voltage, water, draw ------------------------------------------------ */
.strip{
  display:grid; grid-template-columns:repeat(3,1fr); gap:16px;
  padding:19px 18px 20px; border-bottom:1px solid var(--rule);
}
.cell .val{margin-top:5px; font:600 25px/1.05 var(--sans);}
.cell .u{margin-left:2px; font-size:14px; font-weight:500; color:var(--dim);}
.cell .note{margin-top:6px; min-height:17px; font:400 13px/1.3 var(--serif); color:var(--dim);}
.cell.alarm .val, .cell.alarm .note{color:var(--bad);}
.cell.warn .val, .cell.warn .note{color:var(--warn);}

.sec{margin:0; padding:23px 18px 9px; font:400 15.5px var(--serif); color:var(--dim);}

/* --- the switches, as a list rather than a wall of tiles ----------------- */
.loads{border-top:1px solid var(--rule);}
.load{
  display:flex; align-items:center; gap:14px; width:100%;
  appearance:none; -webkit-appearance:none; border:0; margin:0;
  background:transparent; color:var(--ink); font:inherit; text-align:left;
  padding:15px 18px; min-height:62px; cursor:pointer;
  border-bottom:1px solid var(--rule);
}
.load .lamp{
  width:10px; height:10px; border-radius:50%; flex:none; background:#3b3128;
  transition:background .2s linear, box-shadow .2s linear;
}
.load[aria-pressed="true"] .lamp{background:var(--ok); box-shadow:0 0 11px rgba(124,178,124,.55);}
.load .lname{flex:1; font-size:16.5px; font-weight:500;}
.load .lwatts{font:400 13.5px var(--serif); color:var(--dim);}
.load .lstate{width:36px; text-align:right; font-size:13.5px; font-weight:600; color:var(--dim);}
.load[aria-pressed="true"] .lstate{color:var(--ok);}
.load:active{background:var(--panel);}
.load.pending{opacity:.45;}
body.stale .load{cursor:not-allowed;}

/* --- what happened ------------------------------------------------------- */
.log{list-style:none; margin:0; padding:0;}
.log li{
  display:grid; grid-template-columns:auto 1fr; gap:13px;
  padding:11px 18px; border-bottom:1px solid var(--rule);
}
.log time{font:400 13.5px/1.5 var(--sans); color:var(--dim);}
.log .line{font-size:15px; line-height:1.45;}
.log .st{font-weight:600;}
.log .st.on{color:var(--ok);}
.log .why{display:block; margin-top:1px; font:400 14px/1.4 var(--serif); color:var(--dim);}
.log li.alert .subj{color:var(--warn);}
.log li.empty{grid-template-columns:1fr; font:400 15px var(--serif); color:var(--dim);}

/* --- when a tap cannot be delivered -------------------------------------- */
#toast{
  position:fixed; left:50%; bottom:calc(20px + env(safe-area-inset-bottom));
  transform:translate(-50%,12px); z-index:40;
  width:max-content; max-width:min(460px,calc(100% - 28px));
  padding:14px 19px; border-radius:11px; text-align:center;
  background:var(--bad); color:#fff6f3;
  font:400 15px/1.4 var(--serif);
  box-shadow:0 12px 34px rgba(0,0,0,.6);
  opacity:0; pointer-events:none; transition:opacity .18s, transform .18s;
}
#toast.show{opacity:1; transform:translate(-50%,0);}
#toast.rules{background:var(--gold); color:#241a08;}

@media (prefers-reduced-motion:reduce){*{transition:none !important; animation:none !important;}}
</style>
</head>
<body class="stale">

<div id="statusbar" role="status" aria-live="polite">
  <span class="dot"></span><span id="statusmsg">Trying to reach the hub…</span>
</div>

<main id="readouts">
  <section class="hero">
    <div class="eyebrow">The house is running on</div>
    <div class="source" id="source">…</div>
    <div class="sourcenote" id="sourcenote">Nothing has come back from the hub yet.</div>
  </section>

  <section class="batt">
    <div class="k">Battery in the inverter</div>
    <div class="bigrow">
      <span class="big" id="battery">--</span><span class="pct">%</span>
      <span class="battwhy" id="battwhy"></span>
    </div>
    <div class="bar"><i id="battbar"></i></div>
  </section>

  <section class="strip">
    <div class="cell" id="voltcell">
      <div class="k">Voltage</div>
      <div class="val"><span id="voltage">--</span><span class="u">V</span></div>
      <div class="note" id="voltnote"></div>
    </div>
    <div class="cell" id="tankcell">
      <div class="k">Water</div>
      <div class="val"><span id="tank">--</span><span class="u">%</span></div>
      <div class="note" id="tanknote"></div>
    </div>
    <div class="cell">
      <div class="k">Using</div>
      <div class="val"><span id="draw">--</span><span class="u">W</span></div>
      <div class="note" id="drawnote"></div>
    </div>
  </section>

  <h2 class="sec">Around the house</h2>
  <div class="loads" id="loads"></div>

  <h2 class="sec">What happened</h2>
  <ol class="log" id="log"></ol>
</main>

<div id="toast" role="status" aria-live="assertive"></div>

<script>
(function(){
  'use strict';

  var POLL_MS = 1000, STALE_MS = 5000, TIMEOUT_MS = 2500;

  var lastOkAt = null;      // when the last good /state actually arrived
  var triedOnce = false;
  var last = null;
  var pending = {};
  var loadOrder = '';
  var logSig = '';
  var inflight = false, timer = null, toastTimer = null;

  var SRC_LABEL = {grid:'Grid', gen:'Generator', inverter:'Inverter', none:'No power'};
  var SRC_NOTE = {
    grid:'NEPA is on, and the battery is charging.',
    gen:'The generator is running, and burning fuel.',
    inverter:'On battery. The heavy loads are already off.',
    none:'Nothing is powering the house right now.'
  };
  var NAMES = {tv:'TV'};
  var ALERTY = /SPIKE|ALERT|FLAT|OVERFLOW|TOOK LIGHT|shed|restored/i;

  function el(id){ return document.getElementById(id); }

  function nameOf(n){
    return NAMES[n] || n.charAt(0).toUpperCase() + n.slice(1);
  }

  /* house_sim shouts its events in caps for a terminal. Same words, read
     back the way a person would say them. */
  function spoken(s){
    var t = s.toLowerCase().replace(/\bnepa\b/g, 'NEPA').replace(/\btv\b/g, 'TV');
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function ago(ms){
    var s = Math.floor(ms / 1000);
    if (s < 60) return s + (s === 1 ? ' second' : ' seconds');
    var m = Math.floor(s / 60);
    if (m < 60) return m + (m === 1 ? ' minute' : ' minutes');
    var h = Math.floor(m / 60);
    return h + (h === 1 ? ' hour' : ' hours');
  }

  function isStale(){
    return lastOkAt === null || (Date.now() - lastOkAt) > STALE_MS;
  }

  /* Runs on its own clock, not the poll's, so the page still ages correctly
     when nothing is coming back at all. */
  function freshness(){
    var stale = isStale();
    document.body.classList.toggle('stale', stale);
    var msg;
    if (lastOkAt === null) {
      msg = triedOnce ? 'Can’t reach the hub. Nothing has loaded yet.'
                      : 'Trying to reach the hub…';
    } else if (stale) {
      msg = 'Not live. This is the house as it was ' + ago(Date.now() - lastOkAt) + ' ago.';
    } else {
      msg = 'Live · ' + (last ? last.clock : '--:--') + ' in the house';
    }
    el('statusmsg').textContent = msg;
  }

  function toast(text, kind){
    var t = el('toast');
    t.textContent = text;
    t.className = 'show' + (kind ? ' ' + kind : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function(){ t.className = ''; }, 4200);
  }

  function accept(data){
    lastOkAt = Date.now();
    last = data;
    render(data);
    freshness();
  }

  function render(d){
    document.body.dataset.source = d.source;
    el('source').textContent = SRC_LABEL[d.source] || d.source;
    el('sourcenote').textContent = SRC_NOTE[d.source] || '';

    el('battery').textContent = Math.round(d.battery);
    el('battwhy').textContent =
        d.source === 'inverter' ? 'draining'
      : d.source === 'none'     ? ''
      :                           'charging';
    var bb = el('battbar');
    bb.style.width = Math.max(0, Math.min(100, d.battery)) + '%';
    bb.style.background = d.battery <= 20 ? 'var(--bad)'
                        : d.battery <= 50 ? 'var(--warn)' : 'var(--ok)';

    el('voltage').textContent = Math.round(d.voltage);
    el('voltcell').classList.toggle('alarm', !d.voltage_ok);
    el('voltnote').textContent =
        d.source === 'none' ? 'nothing coming in'
      : d.voltage_ok        ? 'steady'
      : d.voltage > 260     ? 'too high, sensitive things cut'
      :                       'too low to be much use';

    el('tank').textContent = Math.round(d.tank);
    var tc = el('tankcell');
    tc.classList.toggle('alarm', d.tank >= 115 || d.tank <= 20);
    tc.classList.toggle('warn', d.tank < 115 && d.tank >= 100);
    el('tanknote').textContent =
        d.tank >= 115 ? 'overflowing'
      : d.tank >= 100 ? 'full'
      : d.tank <= 20  ? 'low, the pump should run'
      :                 '';

    el('draw').textContent = d.draw;
    var on = 0;
    for (var i = 0; i < d.loads.length; i++) if (d.loads[i].on) on++;
    el('drawnote').textContent = on + ' of ' + d.loads.length + ' switched on';

    renderLoads(d.loads);
    renderLog(d.events);
  }

  function renderLoads(loads){
    var wrap = el('loads');
    var order = loads.map(function(l){ return l.name; }).join(',');
    if (order !== loadOrder) {
      loadOrder = order;
      wrap.textContent = '';
      loads.forEach(function(l){
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'load';
        b.dataset.name = l.name;
        ['lamp','lname','lwatts','lstate'].forEach(function(cls){
          var s = document.createElement('span');
          s.className = cls;
          b.appendChild(s);
        });
        b.querySelector('.lname').textContent = nameOf(l.name);
        b.querySelector('.lwatts').textContent = l.watts + ' W';
        b.addEventListener('click', function(){ toggle(l.name); });
        wrap.appendChild(b);
      });
    }
    loads.forEach(function(l){
      var b = wrap.querySelector('[data-name="' + l.name + '"]');
      if (!b) return;
      b.setAttribute('aria-pressed', l.on ? 'true' : 'false');
      b.classList.toggle('pending', !!pending[l.name]);
      b.querySelector('.lstate').textContent = pending[l.name] ? '…' : (l.on ? 'on' : 'off');
      b.setAttribute('aria-label',
        nameOf(l.name) + ', ' + l.watts + ' watts, currently ' + (l.on ? 'on' : 'off') +
        '. Tap to switch it ' + (l.on ? 'off' : 'on') + '.');
    });
  }

  function renderLog(events){
    var sig = events.length
      ? events.length + '|' + events[0].time + '|' + events[0].what + '|' + events[0].why
      : '0';
    if (sig === logSig) return;
    logSig = sig;

    var ol = el('log');
    ol.textContent = '';
    if (!events.length) {
      var empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = 'Nothing yet.';
      ol.appendChild(empty);
      return;
    }
    events.forEach(function(e){
      var li = document.createElement('li');
      if (ALERTY.test(e.what + ' ' + e.why)) li.className = 'alert';

      var t = document.createElement('time');
      t.textContent = e.time;

      var line = document.createElement('div');
      line.className = 'line';
      var m = /^(ON|OFF)\s+(\S+)$/.exec(e.what);
      var subj = document.createElement('span');
      subj.className = 'subj';
      if (m) {
        subj.textContent = nameOf(m[2]) + ' ';
        line.appendChild(subj);
        var st = document.createElement('span');
        st.className = 'st' + (m[1] === 'ON' ? ' on' : '');
        st.textContent = m[1].toLowerCase();
        line.appendChild(st);
      } else {
        subj.textContent = spoken(e.what);
        line.appendChild(subj);
      }
      if (e.why) {
        var why = document.createElement('span');
        why.className = 'why';
        why.textContent = e.why;
        line.appendChild(why);
      }

      li.appendChild(t);
      li.appendChild(line);
      ol.appendChild(li);
    });
  }

  function schedule(ms){
    clearTimeout(timer);
    timer = setTimeout(poll, ms);
  }

  function poll(){
    if (inflight) return;
    inflight = true;
    var ctl = new AbortController();
    var killer = setTimeout(function(){ ctl.abort(); }, TIMEOUT_MS);
    fetch('/state', {signal: ctl.signal, cache: 'no-store'})
      .then(function(r){
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(accept)
      .catch(function(){
        /* Deliberately leaves lastOkAt alone. The old numbers stay on screen,
           but the clock above keeps counting, so they get marked as old
           rather than passed off as current. */
      })
      .then(function(){
        clearTimeout(killer);
        triedOnce = true;
        inflight = false;
        freshness();
        schedule(POLL_MS);
      });
  }

  function toggle(name){
    if (!last) return;
    var nm = nameOf(name);
    if (isStale()) {
      toast('Can’t reach the hub. The ' + nm.toLowerCase() + ' did not move.');
      return;
    }
    var current = null;
    last.loads.forEach(function(l){ if (l.name === name) current = l.on; });
    if (current === null || pending[name]) return;

    var want = !current;
    pending[name] = true;
    renderLoads(last.loads);

    var ctl = new AbortController();
    var killer = setTimeout(function(){ ctl.abort(); }, TIMEOUT_MS);
    fetch('/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, state: want}),
      signal: ctl.signal
    })
      .then(function(r){
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(function(d){
        delete pending[name];
        accept(d);
        if (d.overridden) {
          var why = d.events.length ? d.events[0].why : '';
          toast('The rules put the ' + nm.toLowerCase() + ' back ' +
                (d.actual ? 'on' : 'off') + (why ? ' (' + why + ').' : '.'), 'rules');
        }
      })
      .catch(function(){
        delete pending[name];
        /* Never queued, never guessed at: say plainly that it did not land. */
        toast('That did not get through. The ' + nm.toLowerCase() +
              ' is still ' + (current ? 'on' : 'off') + '.');
        if (last) renderLoads(last.loads);
        freshness();
      })
      .then(function(){ clearTimeout(killer); });
  }

  document.addEventListener('visibilitychange', function(){
    if (!document.hidden) { freshness(); schedule(0); }
  });

  setInterval(freshness, 250);
  freshness();
  poll();
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- main

def main():
    port = PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            sys.exit("usage: python hub.py [port]")

    threading.Thread(target=simulate, daemon=True).start()

    try:
        # SO_REUSEADDR keeps the port stable across a quick stop/restart, which
        # is what makes the stale-then-recover test work.
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("0.0.0.0", port), Hub)
    except OSError as err:
        sys.exit("cannot bind port {}: {}\ntry: python hub.py {}".format(
            port, err, port + 1))

    print("A.D.A. hub")
    print("  http://127.0.0.1:{}".format(port))
    try:
        lan = socket.gethostbyname(socket.gethostname())
        if not lan.startswith("127."):
            print("  http://{}:{}   (same wifi)".format(lan, port))
    except OSError:
        pass
    print("  one real second = one simulated minute")
    print("  Ctrl-C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
