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

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>A.D.A. hub</title>
<style>
:root{
  --bg:#08090b; --panel:#0e1015; --rule:#1c1f26; --sunk:#181c23;
  --ink:#eef1f5; --dim:#828b98;
  --ok:#12b76a; --warn:#f79009; --bad:#f04438; --inv:#4a9eff;
  --src:#828b98;
}
body[data-source="grid"]{--src:var(--ok);}
body[data-source="gen"]{--src:var(--warn);}
body[data-source="inverter"]{--src:var(--inv);}
body[data-source="none"]{--src:var(--bad);}

*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html{background:var(--bg);}
body{
  margin:0 auto; max-width:560px;
  background:var(--bg); color:var(--ink);
  font:400 16px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-variant-numeric:tabular-nums;
  -webkit-font-smoothing:antialiased;
  padding-bottom:calc(30px + env(safe-area-inset-bottom));
}
.label{font-size:11px; letter-spacing:.14em; color:var(--dim); text-transform:uppercase;}
.unit{font-size:.40em; color:var(--dim); margin-left:.16em; letter-spacing:.05em; font-weight:600;}

/* --- the liveness bar is the first thing on screen and never hides ------ */
#statusbar{
  position:sticky; top:0; z-index:20;
  display:flex; align-items:center; gap:9px;
  padding:10px 14px; padding-top:calc(10px + env(safe-area-inset-top));
  background:#0b0d11; border-bottom:1px solid var(--rule);
  font-size:12px; letter-spacing:.09em; text-transform:uppercase; color:var(--dim);
}
#statusbar .dot{
  width:8px; height:8px; border-radius:50%; background:var(--ok); flex:none;
  box-shadow:0 0 0 3px rgba(18,183,106,.16);
}
body.stale #statusbar{
  background:var(--bad); color:#fff; font-weight:800; font-size:13px;
  letter-spacing:.05em; border-bottom-color:var(--bad);
}
body.stale #statusbar .dot{
  background:#fff; box-shadow:0 0 0 3px rgba(255,255,255,.3);
  animation:blink 1s steps(1,end) infinite;
}
@keyframes blink{50%{opacity:.2}}

/* --- stale: the whole readout visibly stops being live ------------------ */
#readouts{transition:filter .18s linear, opacity .18s linear;}
body.stale #readouts{filter:grayscale(1) brightness(.5) contrast(.92); opacity:.6;}

/* --- source: the one fact everything else depends on -------------------- */
.hero{
  padding:22px 16px 20px 18px;
  border-left:6px solid var(--src);
  background:var(--panel);
  border-bottom:1px solid var(--rule);
}
.source{
  margin-top:7px; font-size:clamp(40px,13vw,58px); line-height:1;
  font-weight:800; letter-spacing:-.025em; color:var(--src);
}
.sourcenote{margin-top:8px; color:var(--dim); font-size:13.5px;}

/* --- battery: the other three-second fact ------------------------------- */
.batt{padding:18px 16px 17px; border-bottom:1px solid var(--rule);}
.bigrow{display:flex; align-items:baseline; margin-top:3px;}
.big{font-size:clamp(44px,15vw,64px); line-height:1; font-weight:800; letter-spacing:-.03em;}
.bar{margin-top:13px; height:12px; background:var(--sunk); border-radius:2px; overflow:hidden;}
.bar>i{display:block; height:100%; width:0; background:var(--ok); border-radius:2px;
  transition:width .35s ease, background .25s linear;}

/* --- voltage / tank / draw --------------------------------------------- */
.strip{display:grid; grid-template-columns:repeat(3,1fr); border-bottom:1px solid var(--rule);}
.cell{padding:14px 12px 15px; border-right:1px solid var(--rule);}
.cell:last-child{border-right:0;}
.val{margin-top:6px; font-size:26px; font-weight:700; line-height:1.05;}
.note{margin-top:7px; min-height:15px; font-size:10.5px; letter-spacing:.08em;
  text-transform:uppercase; color:var(--dim);}
.cell.alarm .val, .cell.alarm .note{color:var(--bad);}
.cell.warn .val, .cell.warn .note{color:var(--warn);}
.minibar{height:5px; background:var(--sunk); border-radius:2px; overflow:hidden; margin-top:3px;}
.minibar>i{display:block; height:100%; width:0; background:var(--inv); transition:width .35s ease;}

.sec{margin:0; padding:17px 16px 9px; font-size:11px; letter-spacing:.16em;
  color:var(--dim); text-transform:uppercase; font-weight:600;}

/* --- loads -------------------------------------------------------------- */
.loads{
  display:grid; grid-template-columns:1fr 1fr; gap:1px;
  background:var(--rule); border-top:1px solid var(--rule); border-bottom:1px solid var(--rule);
}
.load{
  appearance:none; -webkit-appearance:none; border:0; margin:0; font:inherit;
  text-align:left; cursor:pointer; color:var(--ink); background:var(--panel);
  padding:13px 13px 12px; min-height:78px;
  display:grid; grid-template-columns:1fr auto;
  grid-template-areas:"n s" "w s"; align-items:center; gap:3px 8px;
  border-left:4px solid #262b33;
  transition:border-color .18s linear;
}
.load .lname{grid-area:n; font-size:15.5px; font-weight:700; letter-spacing:.03em; text-transform:uppercase;}
.load .lwatts{grid-area:w; font-size:11.5px; color:var(--dim); letter-spacing:.05em;}
.load .lstate{grid-area:s; font-size:11px; font-weight:800; letter-spacing:.12em; color:var(--dim);}
.load[aria-pressed="true"]{border-left-color:var(--ok);}
.load[aria-pressed="true"] .lstate{color:var(--ok);}
.load.pending{opacity:.5;}
.load:active{background:#151920;}
body.stale .load{cursor:not-allowed;}

/* --- event log ---------------------------------------------------------- */
.log{list-style:none; margin:0; padding:0;}
.log li{
  display:grid; grid-template-columns:auto 1fr; gap:11px;
  padding:9px 16px; border-bottom:1px solid #14171d; font-size:13px;
}
.log time{
  color:var(--dim); font-size:12.5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
.log .what{font-weight:600;}
.log .why{color:var(--dim); margin-left:.5em;}
.log li.alert .what{color:var(--warn);}
.log li.empty{color:var(--dim); grid-template-columns:1fr;}

/* --- refusals ----------------------------------------------------------- */
#toast{
  position:fixed; left:50%; bottom:calc(18px + env(safe-area-inset-bottom));
  transform:translate(-50%,14px); z-index:40;
  width:max-content; max-width:min(520px,calc(100% - 24px));
  padding:12px 16px; border-radius:3px; text-align:center;
  background:var(--bad); color:#fff; line-height:1.32;
  font-size:12.5px; font-weight:800; letter-spacing:.06em; text-transform:uppercase;
  box-shadow:0 10px 30px rgba(0,0,0,.55);
  opacity:0; pointer-events:none; transition:opacity .16s, transform .16s;
}
#toast.show{opacity:1; transform:translate(-50%,0);}
#toast.rules{background:var(--warn); color:#1b1200;}

@media (prefers-reduced-motion:reduce){
  *{transition:none !important; animation:none !important;}
}
</style>
</head>
<body class="stale">

<div id="statusbar" role="status" aria-live="polite">
  <span class="dot"></span><span id="statusmsg">Connecting&hellip;</span>
</div>

<main id="readouts">
  <section class="hero">
    <div class="label">Running on</div>
    <div class="source" id="source">&mdash;</div>
    <div class="sourcenote" id="sourcenote">waiting for the hub</div>
  </section>

  <section class="batt">
    <div class="label">Inverter battery</div>
    <div class="bigrow"><span class="big" id="battery">--</span><span class="unit">%</span></div>
    <div class="bar"><i id="battbar"></i></div>
  </section>

  <section class="strip">
    <div class="cell" id="voltcell">
      <div class="label">Voltage</div>
      <div class="val"><span id="voltage">--</span><span class="unit">V</span></div>
      <div class="note" id="voltnote"></div>
    </div>
    <div class="cell" id="tankcell">
      <div class="label">Tank</div>
      <div class="val"><span id="tank">--</span><span class="unit">%</span></div>
      <div class="note"><div class="minibar"><i id="tankbar"></i></div></div>
    </div>
    <div class="cell">
      <div class="label">Draw</div>
      <div class="val"><span id="draw">--</span><span class="unit">W</span></div>
      <div class="note" id="drawnote"></div>
    </div>
  </section>

  <h2 class="sec">Loads</h2>
  <div class="loads" id="loads"></div>

  <h2 class="sec">Event log</h2>
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

  var SRC_LABEL = {grid:'GRID', gen:'GENERATOR', inverter:'INVERTER', none:'NO POWER'};
  var SRC_NOTE = {
    grid:'NEPA is on \\u2014 battery charging',
    gen:'generator running \\u2014 burning fuel',
    inverter:'on battery \\u2014 heavy loads shed',
    none:'nothing is powering the house'
  };
  var ALERTY = /SPIKE|ALERT|FLAT|OVERFLOW|TOOK LIGHT|shed|restored/i;

  function el(id){ return document.getElementById(id); }

  function ago(ms){
    var s = Math.floor(ms / 1000);
    if (s < 60) return s + 's';
    return Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2,'0') + 's';
  }

  function isStale(){
    return lastOkAt === null || (Date.now() - lastOkAt) > STALE_MS;
  }

  /* The freshness clock runs independently of the poll, so the display goes
     stale on time even when no response is coming back at all. */
  function freshness(){
    var stale = isStale();
    document.body.classList.toggle('stale', stale);
    var msg;
    if (lastOkAt === null) {
      msg = triedOnce ? 'No connection \\u2014 never reached the hub' : 'Connecting\\u2026';
    } else if (stale) {
      msg = 'Not live \\u2014 data is ' + ago(Date.now() - lastOkAt) + ' old';
    } else {
      msg = 'Live \\u00b7 house clock ' + (last ? last.clock : '--:--');
    }
    el('statusmsg').textContent = msg;
  }

  function toast(text, kind){
    var t = el('toast');
    t.textContent = text;
    t.className = 'show' + (kind ? ' ' + kind : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function(){ t.className = ''; }, 3600);
  }

  function accept(data){
    lastOkAt = Date.now();
    last = data;
    render(data);
    freshness();
  }

  function render(d){
    document.body.dataset.source = d.source;
    el('source').textContent = SRC_LABEL[d.source] || d.source.toUpperCase();
    el('sourcenote').textContent = SRC_NOTE[d.source] || '';

    el('battery').textContent = Math.round(d.battery);
    var bb = el('battbar');
    bb.style.width = Math.max(0, Math.min(100, d.battery)) + '%';
    bb.style.background = d.battery <= 20 ? 'var(--bad)'
                        : d.battery <= 50 ? 'var(--warn)' : 'var(--ok)';

    el('voltage').textContent = Math.round(d.voltage);
    var vc = el('voltcell');
    vc.classList.toggle('alarm', !d.voltage_ok);
    el('voltnote').textContent =
        d.source === 'none' ? 'no supply'
      : d.voltage_ok        ? 'safe 200\\u2013260'
      : d.voltage > 260     ? 'spike \\u2014 over range'
      :                       'under range';

    el('tank').textContent = Math.round(d.tank);
    el('tankbar').style.width = Math.max(0, Math.min(100, d.tank)) + '%';
    var tc = el('tankcell');
    tc.classList.toggle('alarm', d.tank >= 115 || d.tank <= 20);
    tc.classList.toggle('warn', d.tank < 115 && d.tank >= 100);

    el('draw').textContent = d.draw;
    var on = 0;
    for (var i = 0; i < d.loads.length; i++) if (d.loads[i].on) on++;
    el('drawnote').textContent = on + ' of ' + d.loads.length + ' on';

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
        ['lname','lwatts','lstate'].forEach(function(cls){
          var s = document.createElement('span');
          s.className = cls;
          b.appendChild(s);
        });
        b.querySelector('.lname').textContent = l.name;
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
      b.querySelector('.lstate').textContent =
        pending[l.name] ? '\\u2026' : (l.on ? 'ON' : 'OFF');
      b.setAttribute('aria-label',
        l.name + ', ' + l.watts + ' watts, currently ' + (l.on ? 'on' : 'off') +
        ', tap to turn ' + (l.on ? 'off' : 'on'));
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
      empty.textContent = 'nothing has happened yet';
      ol.appendChild(empty);
      return;
    }
    events.forEach(function(e){
      var li = document.createElement('li');
      if (ALERTY.test(e.what + ' ' + e.why)) li.className = 'alert';
      var t = document.createElement('time');
      t.textContent = e.time;
      var body = document.createElement('span');
      var what = document.createElement('span');
      what.className = 'what';
      what.textContent = e.what;
      body.appendChild(what);
      if (e.why) {
        var why = document.createElement('span');
        why.className = 'why';
        why.textContent = e.why;
        body.appendChild(why);
      }
      li.appendChild(t);
      li.appendChild(body);
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
        /* Deliberately leaves lastOkAt alone. The old numbers stay on screen
           but the freshness clock keeps counting, so they get marked stale
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
    if (isStale()) {
      toast('Can\\u2019t reach the hub \\u2014 ' + name + ' not switched');
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
          toast('Rules overrode \\u2014 ' + name + ' is ' + (d.actual ? 'on' : 'off'), 'rules');
        }
      })
      .catch(function(){
        delete pending[name];
        /* Never queued, never applied optimistically: say so plainly. */
        toast('Not sent \\u2014 hub unreachable \\u00b7 ' + name + ' unchanged');
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
