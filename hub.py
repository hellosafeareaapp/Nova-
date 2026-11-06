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
import os
import posixpath
import re
import shutil
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

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")
ASSETS_DIR = os.path.join(HERE, "assets")

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


# ------------------------------------------------------------- what is theirs
# The house belongs to whoever lives in it, so nothing below is a decision the
# dashboard gets to keep to itself. These are only the values it starts with:
# every one of them is overwritten by settings.json, which the owner edits.

DEFAULTS = {
    "theme": {
        "ink": "#efeae3", "dim": "#9a9188",
        "ok": "#7cb27c", "warn": "#dd8b45", "bad": "#c8544a",
        # one palette per thing the house can be running on
        "sources": {
            "grid":     {"tint": "#101214", "panel": "#171a1d", "rule": "#252a2e",
                         "sunk": "#1f2429", "src": "#a6b7c2"},
            "gen":      {"tint": "#151007", "panel": "#1e1710", "rule": "#2e2418",
                         "sunk": "#271e13", "src": "#e0913f"},
            "inverter": {"tint": "#0a0e16", "panel": "#111621", "rule": "#1d2534",
                         "sunk": "#18202d", "src": "#5b9bd8"},
            "none":     {"tint": "#170b0a", "panel": "#211110", "rule": "#331b19",
                         "sunk": "#2b1615", "src": "#d1554a"},
        },
    },
    "type": {
        "sans": '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif',
        "serif": '"Iowan Old Style",Palatino,"Palatino Linotype",Georgia,"Times New Roman",serif',
        "scale": 1.0,
        # a font file dropped in assets/ is served and @font-face'd, so this
        # stays offline: nothing is ever fetched from a network
        "faces": [],
    },
    "wallpaper": {"mode": "none", "image": "", "dim": 0.55, "blur": 0},
    "layout": {"preset": "cards", "density": "comfortable"},
    "voice": {"wake": "Hey A.D.A.", "pidgin": True, "style": "plain"},
    "nova": {"particles": 2200, "fps": 30},
}

HEX = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
FONT_EXT = (".woff2", ".woff", ".ttf", ".otf")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
ASSET_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
               ".otf": "font/otf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
               ".avif": "image/avif"}

SETTINGS_LOCK = threading.Lock()


def _merge(base, over):
    """Overlay the owner's settings on the defaults, key by key, so a partial
    or older settings.json still produces a complete set."""
    out = dict(base)
    for k, v in (over or {}).items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v)
        elif k in base:
            out[k] = v
    return out


def _clean(st):
    """Keep a hand-edited file from bricking the dashboard. Anything that does
    not make sense quietly reverts to the default rather than raising: this is
    read during a blackout, so it has to boot no matter what is in the file."""
    d = DEFAULTS
    th = st["theme"]
    for k in ("ink", "dim", "ok", "warn", "bad"):
        if not HEX.match(str(th.get(k, ""))):
            th[k] = d["theme"][k]
    for name, pal in th["sources"].items():
        base = d["theme"]["sources"].get(name, d["theme"]["sources"]["grid"])
        for k in ("tint", "panel", "rule", "sunk", "src"):
            if not HEX.match(str(pal.get(k, ""))):
                pal[k] = base[k]
    ty = st["type"]
    try:
        ty["scale"] = min(1.6, max(0.75, float(ty.get("scale", 1))))
    except (TypeError, ValueError):
        ty["scale"] = 1.0
    ty["faces"] = [f for f in (ty.get("faces") or [])
                   if isinstance(f, dict) and safe_asset(f.get("file", ""))
                   and str(f.get("file", "")).lower().endswith(FONT_EXT)][:8]
    wp = st["wallpaper"]
    if wp.get("mode") not in ("none", "image"):
        wp["mode"] = "none"
    if not safe_asset(wp.get("image", "")) or not str(wp.get("image", "")).lower().endswith(IMAGE_EXT):
        wp["image"] = ""
        wp["mode"] = "none"
    for k, lo, hi in (("dim", 0.0, 1.0), ("blur", 0, 40)):
        try:
            wp[k] = min(hi, max(lo, float(wp.get(k, 0))))
        except (TypeError, ValueError):
            wp[k] = d["wallpaper"][k]
    nv = st["nova"]
    for k, lo, hi in (("particles", 200, 4000), ("fps", 10, 60)):
        try:
            nv[k] = int(min(hi, max(lo, float(nv.get(k, d["nova"][k])))))
        except (TypeError, ValueError):
            nv[k] = d["nova"][k]
    return st


def safe_asset(name):
    """A single plain filename inside assets/. No paths, no traversal."""
    name = str(name or "")
    return bool(name) and name == posixpath.basename(name) and ".." not in name \
        and "/" not in name and "\\" not in name and not name.startswith(".")


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    return _clean(_merge(DEFAULTS, raw if isinstance(raw, dict) else {}))


def save_settings(st):
    """Written to a temporary file and moved into place, so a crash mid-write
    cannot leave the owner with half a settings file."""
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, SETTINGS_FILE)


def tokens_css(st):
    """The :root block the page used to hard-code, generated from settings."""
    th, ty, wp = st["theme"], st["type"], st["wallpaper"]
    out = []
    for f in ty["faces"]:
        out.append("@font-face{font-family:%s;src:url('/assets/%s');font-display:swap;}"
                   % (json.dumps(str(f.get("name", "Owner"))), f["file"]))
    base = ["--ink:%s" % th["ink"], "--dim:%s" % th["dim"], "--ok:%s" % th["ok"],
            "--warn:%s" % th["warn"], "--bad:%s" % th["bad"],
            "--sans:%s" % ty["sans"], "--serif:%s" % ty["serif"],
            "--scale:%s" % ty["scale"]]
    base += ["--%s:%s" % (k, v) for k, v in th["sources"]["grid"].items()]
    out.append(":root{%s;}" % ";".join(base))
    for name, pal in th["sources"].items():
        out.append(':root[data-source="%s"]{%s;}'
                   % (name, ";".join("--%s:%s" % (k, v) for k, v in pal.items())))
    if wp["mode"] == "image" and wp["image"]:
        out.append("body{background-image:url('/assets/%s');background-size:cover;"
                   "background-position:center;background-attachment:fixed;}" % wp["image"])
        out.append(".wallpaper-dim{position:fixed;inset:0;z-index:0;pointer-events:none;"
                   "background:var(--tint);opacity:%s;%s}"
                   % (wp["dim"],
                      ("backdrop-filter:blur(%spx);" % wp["blur"]) if wp["blur"] else ""))
    return "\n".join(out)


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
            st = load_settings()
            self._send(200, PAGE.replace("/*TOKENS*/", tokens_css(st)),
                       "text/html; charset=utf-8")
            return
        if path == "/settings":
            self._json(200, load_settings())
            return
        if path.startswith("/assets/"):
            self._asset(path[len("/assets/"):])
            return
        if path == "/__unused__":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/state":
            with LOCK:
                payload = snapshot()
            self._json(200, payload)
        elif path == "/favicon.ico":
            self._send(204, "", "text/plain")
        else:
            self._json(404, {"error": "not found"})

    def _asset(self, name):
        """Serves one file out of assets/, by plain name only."""
        if not safe_asset(name):
            self._json(404, {"error": "not found"}); return
        ext = os.path.splitext(name)[1].lower()
        if ext not in ASSET_TYPES:
            self._json(404, {"error": "not found"}); return
        full = os.path.join(ASSETS_DIR, name)
        # resolve and re-check, so a symlink cannot escape assets/ either
        if os.path.realpath(os.path.dirname(full)) != os.path.realpath(ASSETS_DIR):
            self._json(404, {"error": "not found"}); return
        try:
            with open(full, "rb") as fh:
                body = fh.read()
        except OSError:
            self._json(404, {"error": "not found"}); return
        try:
            self.send_response(200)
            self.send_header("Content-Type", ASSET_TYPES[ext])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route == "/settings":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                patch = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(patch, dict):
                    raise ValueError
            except (ValueError, TypeError):
                self._json(400, {"error": "expected a JSON object"}); return
            with SETTINGS_LOCK:
                st = _clean(_merge(load_settings(), patch))
                try:
                    save_settings(st)
                except OSError as err:
                    self._json(500, {"error": "could not save: %s" % err}); return
            self._json(200, st)
            return
        if route != "/switch":
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
<html lang="en" data-source="none">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>The house</title>
<style id="usertokens">/*TOKENS*/</style>
<style>

*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html{background:var(--tint); transition:background-color 600ms linear;}
body{
  margin:0 auto; max-width:540px;
  background:var(--tint); color:var(--ink);
  font:400 calc(16px * var(--scale,1))/1.4 var(--sans);
  font-variant-numeric:tabular-nums;
  -webkit-font-smoothing:antialiased;
  padding-bottom:calc(34px + env(safe-area-inset-bottom));
  transition:background-color 600ms linear;
}
section, .load, #statusbar, .log li{transition:background-color 600ms linear, border-color 600ms linear;}

/* --- how old this is. never hidden, and it speaks in sentences ---------- */
#statusbar{
  position:sticky; top:0; z-index:20;
  display:flex; align-items:center; gap:10px;
  padding:12px 18px; padding-top:calc(12px + env(safe-area-inset-top));
  background:var(--panel); border-bottom:1px solid var(--rule);
  font:400 13.5px/1.35 var(--serif); color:var(--dim);
}
#statusbar .dot{width:7px; height:7px; border-radius:50%; background:var(--ok); flex:none;}
body.stale #statusbar{
  background:var(--bad); color:#fff5f0; font-size:14.5px;
  border-bottom-color:var(--bad);
  padding-top:calc(14px + env(safe-area-inset-top)); padding-bottom:14px;
}
body.stale #statusbar .dot{background:#fff5f0;}

/* --- stale: the readings stop looking like readings --------------------- */
#readouts{transition:filter .2s linear, opacity .2s linear;}
body.stale #readouts{filter:grayscale(.94) brightness(.5); opacity:.55;}

/* Motion is the liveness signal, so when the data stops, every moving thing
   on the page freezes exactly where it is. Not reset, not hidden: stopped. */
body.stale *, body.stale *::before, body.stale *::after{animation-play-state:paused !important;}

.rail, .head{display:none;}

/* --- what the house is running on --------------------------------------- */
.hero{padding:26px 18px 23px; border-bottom:1px solid var(--rule);}
.eyebrow{font:400 14.5px var(--serif); color:var(--dim);}
.source{
  margin-top:5px; color:var(--src);
  font:600 clamp(42px,13.5vw,60px)/1.02 var(--sans); letter-spacing:-.01em;
  transition:color 600ms linear;
}
.sourcenote{margin-top:11px; font:400 15.5px/1.45 var(--serif); color:var(--dim);}

/* --- the presence, and the battery -------------------------------------- */
.batt{padding:20px 18px 19px; border-bottom:1px solid var(--rule);}
.k{font:400 14.5px var(--serif); color:var(--dim);}
.battmid{margin-top:10px;}
.adacard{padding:14px 18px; display:flex; align-items:center; justify-content:center;}
.figure{width:196px; height:196px;}

/* Nova is a sphere of points on a canvas. Every property is a reading: spin
   from load, radius from battery, colour from source, and it stops dead when
   the data does. Drawn by hand below -- no library, nothing external. */
.figure{display:block; position:relative;}
.novacv{display:block; width:100%; height:100%;}
.figure.worried{--src:var(--bad);}
}

.battrow{display:flex; align-items:flex-end; gap:0;}
.big{font:600 clamp(44px,14vw,58px)/1 var(--sans); letter-spacing:-.01em;}
.pct{margin-left:3px; padding-bottom:.14em; font-size:21px; font-weight:500; color:var(--dim);}
.battwhy{margin-left:auto; padding-bottom:.35em; font:400 15px var(--serif); color:var(--dim);}
.bar{margin-top:14px; height:9px; background:var(--sunk); border-radius:5px; overflow:hidden;}
.bar > i{display:block; height:100%; width:0; border-radius:5px; background:var(--ok);
  transition:width .4s ease, background .3s linear;}
.ring, .ringwhy{display:none;}

/* rolling digits, so a battery draining on inverter is visible as movement */
.digits{display:inline-flex;}
.sr{position:absolute; width:1px; height:1px; margin:-1px; padding:0;
  overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0;}
.digit{display:inline-block; height:1em; overflow:hidden; vertical-align:top;}
.reel{display:block; transition:transform .6s cubic-bezier(.22,.61,.36,1);}
.reel b{display:block; height:1em; line-height:1; font-weight:inherit;}

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

/* --- the switches, heaviest first, sized by what they actually draw ------ */
.loads{border-top:1px solid var(--rule);}
.load{
  display:flex; align-items:center; gap:14px; width:100%;
  appearance:none; -webkit-appearance:none; border:0; margin:0;
  background:transparent; color:var(--ink); font:inherit; text-align:left;
  padding:15px 18px; cursor:pointer; border-bottom:1px solid var(--rule);
}
.load .lamp{width:10px; height:10px; border-radius:50%; flex:none; background:var(--sunk);
  transition:background .2s linear;}
.load[aria-pressed="true"] .lamp{background:var(--ok);}
.load .lname{flex:1; font-weight:500;}
.load .lwatts{font:400 13.5px var(--serif); color:var(--dim);}
.load .lstate{width:36px; text-align:right; font-size:13.5px; font-weight:600; color:var(--dim);}
.load[aria-pressed="true"] .lstate{color:var(--ok);}
.load:active{background:var(--panel);}
.load.pending{opacity:.45;}
body.stale .load{cursor:not-allowed;}

.load.heavy{min-height:74px;}
.load.heavy .lname{font-size:19px; font-weight:600;}
.load.heavy .lwatts{font-size:15.5px; color:var(--ink); opacity:.75;}
.load.mid{min-height:62px;}
.load.mid .lname{font-size:16px;}
.load.light{min-height:54px;}
.load.light .lname{font-size:14.5px; color:var(--dim);}
.load.light .lwatts{font-size:12.5px;}

/* --- what happened, in the house's own words ---------------------------- */
.log{list-style:none; margin:0; padding:0;}
.log li{
  display:grid; grid-template-columns:auto 1fr; gap:13px;
  padding:11px 18px; border-bottom:1px solid var(--rule);
}
.log time{font:400 13.5px/1.5 var(--sans); color:var(--dim);}
.log .line{font:400 15px/1.45 var(--serif);}
.log li.alert .line{color:var(--warn);}
.log li.empty{grid-template-columns:1fr; color:var(--dim); font:400 15px var(--serif);}
.log li.fresh{animation:freshin 1.1s ease-out;}
@keyframes freshin{
  0%{transform:translateY(-11px); opacity:0; background-color:rgba(255,255,255,.09);}
  45%{opacity:1;}
  100%{transform:translateY(0); opacity:1; background-color:transparent;}
}

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
#toast.rules{background:var(--warn); color:#201404;}

/* ======================================================================== */
/* Desktop                                                                  */
/* ======================================================================== */
@media (min-width:1024px){
  body{max-width:none; padding-bottom:0;}

  .wrap{
    display:grid; grid-template-columns:78px minmax(0,1fr);
    max-width:1560px; margin:0 auto; min-height:100vh;
  }
  .rail{
    display:flex; flex-direction:column; align-items:center; gap:20px;
    padding:24px 0 26px; border-right:1px solid var(--rule); background:var(--panel);
  }
  .rail .mark{width:25px; height:25px; color:var(--dim); flex:none;}
  .rail .spine{flex:1; width:3px; border-radius:2px; background:var(--src); opacity:.9;
    transition:background-color 600ms linear;}
  .rail .raillamp{width:9px; height:9px; border-radius:50%; background:var(--ok); flex:none;
    transition:background .3s linear;}
  body.stale .rail .spine{background:#4a4640;}
  body.stale .rail .raillamp{background:var(--bad);}

  .head{display:flex; align-items:baseline; justify-content:space-between; padding:26px 30px 18px;}
  .head .title{font:400 21px var(--serif); color:var(--ink);}
  .head .clock{font:500 21px var(--sans); color:var(--dim);}
  body.stale .head{opacity:.5; filter:grayscale(.9);}

  #statusbar{padding:13px 30px; font-size:14px;}
  body.stale #statusbar{padding:15px 30px; font-size:15px;}

  #readouts{
    display:grid; gap:16px; padding:0 30px 30px;
    grid-template-columns:1.02fr .96fr .86fr;
    grid-template-rows:1fr auto auto auto;
    grid-template-areas:
      "source ada battery"
      "reads  ada battery"
      "log    log log"
      "loads  loads loads";
  }
  #readouts > section{background:var(--panel); border:1px solid var(--rule); border-radius:16px;}

  .hero{
    grid-area:source; padding:34px 30px 34px;
    display:flex; flex-direction:column; justify-content:center; min-height:268px;
  }
  .hero .eyebrow{font-size:16px;}
  .hero .source{font-size:clamp(48px,4.6vw,70px); margin-top:8px;}
  .hero .sourcenote{margin-top:14px; font-size:17px;}

  .batt{grid-area:battery; padding:20px 22px 24px; display:flex; flex-direction:column;}
  .battmid{display:flex; flex-direction:column; justify-content:center; align-items:center; gap:0;}
  /* no panel of its own: it occupies the layout directly and stands on it */
  #readouts > .adacard{background:transparent; border:0; border-radius:0;}
  .adacard{grid-area:ada; padding:0 6px; align-items:center;}
  .figure{width:292px; height:292px;}
  .battrow, .bar{display:none;}
  .ring{display:block; position:relative; width:172px; height:172px;}
  .ring svg{width:100%; height:100%; transform:rotate(-90deg); display:block;}
  .ring .track{fill:none; stroke:var(--sunk); stroke-width:11; transition:stroke 600ms linear;}
  .ring .fill{fill:none; stroke:var(--ok); stroke-width:11; stroke-linecap:round;
    stroke-dasharray:339.292; stroke-dashoffset:339.292;
    transition:stroke-dashoffset .6s ease, stroke .3s linear;}
  .ringtext{position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
    font:600 42px var(--sans); letter-spacing:-.01em;}
  .ringtext i{font:500 18px var(--sans); font-style:normal; color:var(--dim); margin-left:2px;
    align-self:center; padding-top:.5em;}
  .ringwhy{display:block; margin-top:14px; font:400 15px var(--serif); color:var(--dim); min-height:21px;}

  .strip{grid-area:reads; padding:20px 22px 22px; gap:18px;}

  .logcard{grid-area:log; display:flex; flex-direction:column; overflow:hidden; max-height:520px;}
  .logcard .sec{padding:19px 22px 11px;}
  .logcard .log{flex:1; overflow-y:auto;}
  .log li{padding:11px 22px;}
  .log li:last-child{border-bottom:0;}

  .loadscard{grid-area:loads; overflow:hidden;}
  .loadscard .sec{padding:19px 22px 4px;}
  .loads{display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
    gap:12px; padding:8px 22px 20px; border-top:0;}
  .load{
    display:grid; grid-template-columns:auto 1fr; align-content:start;
    grid-template-areas:"lamp state" "name name" "watts watts";
    gap:9px 8px; padding:15px 15px 14px;
    border:1px solid var(--rule); border-radius:13px; background:var(--tint);
    transition:border-color .18s linear, background-color 600ms linear;
  }
  .load:hover{border-color:var(--src);}
  .load .lamp{grid-area:lamp;}
  .load .lname{grid-area:name; flex:none;}
  .load .lwatts{grid-area:watts;}
  .load .lstate{grid-area:state; width:auto; justify-self:end;}
  /* a 2000W kettle is not the same object as a 120W television */
  .load.heavy{grid-column:span 2; min-height:118px;}
  .load.heavy .lname{font-size:22px;}
  .load.heavy .lwatts{font-size:17px;}
  .load.mid{min-height:106px;}
  .load.light{min-height:96px;}
}
@media (min-width:1320px){
  #readouts{
    grid-template-columns:1.02fr .94fr .78fr 1.0fr;
    grid-template-rows:1fr auto auto;
    grid-template-areas:
      "source ada battery log"
      "reads  ada battery log"
      "loads  loads loads  loads";
  }
}
@media (min-width:1366px){
  .loads{grid-template-columns:repeat(8,minmax(0,1fr));}
}

@media (prefers-reduced-motion:reduce){
  *{transition:none !important; animation:none !important;}
}
</style>
</head>
<body class="stale">
<div class="wallpaper-dim"></div>

<div id="statusbar" role="status" aria-live="polite">
  <span class="dot"></span><span id="statusmsg">Trying to reach the hub…</span>
</div>

<div class="wrap">
  <aside class="rail" aria-hidden="true">
    <svg class="mark" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 10.5 12 3l9 7.5"></path><path d="M5.5 9.6V20h13V9.6"></path>
    </svg>
    <span class="spine"></span>
    <span class="raillamp"></span>
  </aside>

  <div class="col">
    <header class="head">
      <div class="title">The house</div>
      <div class="clock" id="headclock">--:--</div>
    </header>

    <main id="readouts">
      <section class="hero">
        <div class="eyebrow">The house is running on</div>
        <div class="source" id="source">…</div>
        <div class="sourcenote" id="sourcenote">Nothing has come back from the hub yet.</div>
      </section>

      <section class="adacard">
        <div class="figure" id="figure" aria-hidden="true">
            <canvas class="novacv" id="novacv"></canvas>
        </div>
      </section>

      <section class="batt">
        <div class="k">Battery in the inverter</div>
        <div class="battmid">
          <div class="battrow">
            <span class="big digits" id="battdigits"></span><span class="pct" aria-hidden="true">%</span>
            <span class="battwhy" id="battwhy"></span>
          </div>
          <div class="bar"><i id="battbar"></i></div>
          <div class="ring">
            <svg viewBox="0 0 128 128">
              <circle class="track" cx="64" cy="64" r="54"></circle>
              <circle class="fill" id="ringfill" cx="64" cy="64" r="54"></circle>
            </svg>
            <div class="ringtext" aria-hidden="true"><span class="digits" id="ringdigits"></span><i>%</i></div>
          </div>
          <div class="ringwhy" id="ringwhy"></div>
          <p class="sr" id="battsr"></p>
        </div>
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

      <section class="loadscard">
        <h2 class="sec">Around the house</h2>
        <div class="loads" id="loads"></div>
      </section>

      <section class="logcard">
        <h2 class="sec">What happened</h2>
        <ol class="log" id="log"></ol>
      </section>
    </main>
  </div>
</div>

<div id="toast" role="status" aria-live="assertive"></div>

<script>
(function(){
  'use strict';

  var POLL_MS = 1000, STALE_MS = 5000, TIMEOUT_MS = 2500;
  var RING = 339.292;       // 2 * pi * r, with r = 54 in the svg above

  var lastOkAt = null;
  var triedOnce = false;
  var last = null;
  var pending = {};
  var loadOrder = '';
  var seenKeys = null;      // null until the first render, so nothing flashes on load
  var inflight = false, timer = null, toastTimer = null;
  var fig = {mood:''};
  var lastPulse = '', lastBatt = null;

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
  function nameOf(n){ return NAMES[n] || n.charAt(0).toUpperCase() + n.slice(1); }
  function spoken(s){
    var t = s.toLowerCase().replace(/\bnepa\b/g, 'NEPA').replace(/\btv\b/g, 'TV');
    return t.charAt(0).toUpperCase() + t.slice(1);
  }
  function keyOf(e){ return e.time + '|' + e.what + '|' + e.why; }

  /* ---- Nova: a sphere of points, written here, no library -------------- */
  var Nova = (function(){
    var PARTICLES = 2200;  // ceiling. The governor below only ever goes down from here.
    var FLOOR     = 700;   // it will not thin the lattice past this
    var MAX       = 4000;  // hard cap, whatever anything else asks for.
    var FPS       = 30;    // deliberately not 60. This runs on a Pi and a phone.
    var FOV = 3.2, SPRING = 12, DAMP = 4.5, TILT = 0.10, SPR = 32;

    var cv, ctx, box, spr, sctx, W = 0, H = 0, pts = [], n = 0;
    var rot = 0, raf = 0, spinning = false, isLive = false, last = 0, acc = 0;
    var reduce = false, ptr = null, col = [166,183,194];
    var cost = 0, slow = 0, fast = 0, want = 0;   // frame-time governor
    var st = {source:'grid', battery:100, draw:0};
    var bx = [], by = [], bs = [];        // reused every frame; no per-frame garbage
    for (var q = 0; q < 8; q++) { bx.push([]); by.push([]); bs.push([]); }

    /* A latitude/longitude lattice. Every longitude converges at the poles, so
       they ring up bright and the rows and columns stay legible as it turns --
       that structure is the point. (An earlier build used a Fibonacci spiral,
       which spreads evenly and deliberately kills exactly that.) */
    function build(target){
      target = Math.max(200, Math.min(MAX, target));
      var rings = Math.max(8, Math.round(Math.sqrt(target / 2)));
      var seg = Math.max(8, Math.round(target / rings));
      n = rings * seg;
      if (cv) cv.dataset.n = n;      // readable when tuning on the target device
      pts = [];
      for (var i = 0; i < rings; i++) {
        var phi = Math.PI * (i + 0.5) / rings;
        var y = Math.cos(phi), r = Math.sin(phi);
        for (var j = 0; j < seg; j++) {
          var th = 2 * Math.PI * j / seg;
          pts.push({x:Math.cos(th)*r, y:y, z:Math.sin(th)*r,
                    rx:0, ry:0, rz:0, ox:0, oy:0, oz:0, vx:0, vy:0, vz:0});
        }
      }
    }

    /* One sprite, redrawn only when the colour changes: a white-hot core
       falling off into the state colour. Additive blending does the bloom. */
    function sprite(){
      if (!spr) { spr = document.createElement('canvas'); spr.width = spr.height = SPR;
                  sctx = spr.getContext('2d'); }
      var c = col[0] + ',' + col[1] + ',' + col[2];
      sctx.clearRect(0, 0, SPR, SPR);
      var g = sctx.createRadialGradient(SPR/2, SPR/2, 0, SPR/2, SPR/2, SPR/2);
      g.addColorStop(0,    'rgba(255,255,255,0.90)');
      g.addColorStop(0.07, 'rgba(255,255,255,0.42)');
      g.addColorStop(0.17, 'rgba(' + c + ',0.62)');
      g.addColorStop(0.42, 'rgba(' + c + ',0.15)');
      g.addColorStop(1,    'rgba(' + c + ',0)');
      sctx.fillStyle = g; sctx.fillRect(0, 0, SPR, SPR);
    }

    function fit(){
      if (!cv) return;
      var r = cv.getBoundingClientRect();
      if (!r.width) return;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = Math.round(r.width); H = Math.round(r.height);
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      want = W < 240 ? Math.round(PARTICLES * 0.55) : PARTICLES;
      if (Math.abs(want - n) > want * 0.5) build(want);
    }

    function colour(){
      var v = getComputedStyle(box).getPropertyValue('--src').trim();
      var m = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec(v);
      if (!m) return;
      var h = m[1];
      if (h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
      col = [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
      sprite();
    }

    /* Every number here is a reading, not a preference. */
    function params(){
      var s = st.source, low = st.battery <= 20 || s === 'none';
      var p = {spin:0.16, jitter:0, dim:1, squeeze:1, pulse:0};
      if (s === 'gen')           { p.spin = 0.40; p.jitter = 0.011; }
      else if (s === 'inverter') { p.spin = 0.05; p.dim = 0.5; p.squeeze = 0.78; }
      else if (s === 'none')     { p.spin = 0.12; p.jitter = 0.006; }
      if (low) { p.pulse = 1; p.spin = Math.max(p.spin, 0.26);
                 p.jitter = Math.max(p.jitter, 0.008); }
      p.spin += Math.min(1, st.draw / 3400) * 0.55;   // heavier load, faster spin
      return p;
    }

    function step(dt){
      if (!W) return;
      var p = params();
      rot += p.spin * dt;
      var t = Date.now() / 1000;
      var beat = p.pulse ? 1 + Math.sin(t * 5.2) * 0.07 : 1;
      var batt = Math.max(0, Math.min(100, st.battery));
      /* radius carries the battery: as it falls, the sphere contracts */
      var R = Math.min(W, H) * 0.29 * p.squeeze * (0.66 + 0.34 * batt / 100) * beat;
      var cx = W / 2, cy = H / 2;
      var cs = Math.cos(rot), sn = Math.sin(rot);
      var ct = Math.cos(TILT), stt = Math.sin(TILT);

      bx[0].length = 0; by[0].length = 0; bs[0].length = 0;
      ctx.clearRect(0, 0, W, H);

      for (var i = 0; i < n; i++) {
        var o = pts[i];
        o.vx += (-SPRING * o.ox - DAMP * o.vx) * dt;
        o.vy += (-SPRING * o.oy - DAMP * o.vy) * dt;
        o.vz += (-SPRING * o.oz - DAMP * o.vz) * dt;
        o.ox += o.vx * dt; o.oy += o.vy * dt; o.oz += o.vz * dt;

        var x = o.x * cs - o.z * sn;
        var z0 = o.x * sn + o.z * cs;
        var y = o.y * ct - z0 * stt;
        var z = o.y * stt + z0 * ct;
        o.rx = x; o.ry = y; o.rz = z;      // remembered so a spike blows outward

        if (p.jitter) { x += (Math.random()-0.5)*p.jitter; y += (Math.random()-0.5)*p.jitter; }
        x += o.ox; y += o.oy; z += o.oz;

        var sc = FOV / (FOV + z);
        var sx = cx + x * R * sc, sy = cy + y * R * sc;

        if (ptr) {                          // the pointer pushes; the spring returns
          var dx = sx - ptr.x, dy = sy - ptr.y, d2 = dx*dx + dy*dy;
          if (d2 < 4900 && d2 > 0.5) {
            var d = Math.sqrt(d2), f = (1 - d/70) * 58 * dt;
            o.vx += dx/d * f; o.vy += dy/d * f;
          }
        }

        var depth = (z + 1) / 2;            // 0 far, 1 near
        bx[0].push(sx); by[0].push(sy);
        bs[0].push(depth); bs[0].push(sc);
      }

      /* Additive, so where the lattice crowds -- the poles, the limb -- the
         glow piles up on itself instead of flattening. One sprite, one
         drawImage each; no per-particle gradient or fillStyle string. */
      var xs = bx[0], ys = by[0], ds = bs[0];
      ctx.globalCompositeOperation = 'lighter';
      for (var k = 0, m2 = 0; k < xs.length; k++, m2 += 2) {
        var depth2 = ds[m2], sc2 = ds[m2 + 1];
        ctx.globalAlpha = (0.05 + 0.52 * depth2) * p.dim;
        var rad = (0.95 + 1.9 * depth2) * sc2;
        ctx.drawImage(spr, xs[k] - rad, ys[k] - rad, rad * 2, rad * 2);
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
    }

    function frame(ts){
      if (!spinning) return;
      raf = requestAnimationFrame(frame);
      var dt = (ts - last) / 1000; last = ts;
      if (dt > 0.25) dt = 0.25;
      acc += dt;
      if (acc < 1 / FPS) return;
      govern(acc);           // how long the frame actually took, not just our part
      step(acc); acc = 0;
    }

    /* A Pi and a phone are not this laptop. Rather than guess a particle count
       that is safe everywhere, measure how long a frame actually takes here and
       thin the lattice until it fits the budget. Only ever downward from the
       configured ceiling, never past FLOOR. */
    function govern(interval){
      var target = 1 / FPS;
      cost = cost * 0.85 + interval * 0.15;
      if (cost > target * 1.5) {                  // frames arriving late: thin it
        fast = 0;
        if (++slow > 20 && n > FLOOR) {
          build(Math.max(FLOOR, Math.round(n * 0.7))); slow = 0; cost = target;
        }
      } else if (cost < target * 1.12) {          // comfortable: creep back up
        slow = 0;
        if (++fast > 400 && n < want) {
          build(Math.min(want, Math.round(n * 1.3))); fast = 0; cost = target;
        }
      } else { slow = 0; fast = 0; }
    }
    function start(){
      if (spinning || reduce) return;
      spinning = true; last = performance.now(); acc = 0;
      raf = requestAnimationFrame(frame);
    }
    function stop(){ spinning = false; cancelAnimationFrame(raf); }

    return {
      init: function(){
        cv = el('novacv'); box = el('figure');
        if (!cv || !cv.getContext) return;
        ctx = cv.getContext('2d');
        reduce = window.matchMedia &&
                 window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        build(PARTICLES); fit(); colour(); step(0);
        window.addEventListener('resize', function(){ fit(); if (!spinning) step(0); });
        document.addEventListener('visibilitychange', function(){
          if (document.hidden) stop(); else if (isLive) start();
        });
        cv.addEventListener('pointermove', function(e){
          var r = cv.getBoundingClientRect();
          ptr = {x:e.clientX - r.left, y:e.clientY - r.top};
        });
        cv.addEventListener('pointerleave', function(){ ptr = null; });
        cv.addEventListener('pointercancel', function(){ ptr = null; });
      },
      state: function(d){
        st.source = d.source; st.battery = d.battery; st.draw = d.draw;
        colour();
        if (!spinning) step(0);          // keeps the static fallback current
      },
      /* The whole point: when the data stops, the sphere stops. Not slower --
         stopped, on whatever frame it was on. */
      live: function(v){
        if (v === isLive) return;
        isLive = v;
        if (v && !document.hidden) start(); else stop();
      },
      count: function(){ return n; },
      spike: function(){
        if (reduce) return;
        for (var i = 0; i < n; i++) {
          var o = pts[i], m = 2.6 + Math.random() * 1.6;
          o.vx += o.rx * m; o.vy += o.ry * m; o.vz += o.rz * m;
        }
      }
    };
  })();


  /* ---- the house talking about itself --------------------------------- */
  function narrate(e){
    var what = e.what, why = e.why || '';
    var m = /^(ON|OFF)\s+(\S+)$/.exec(what);
    if (m) {
      var on = m[1] === 'ON', nm = nameOf(m[2]), low = nm === 'TV' ? 'TV' : nm.toLowerCase();
      var spike = /spike\s+(\d+)V/.exec(why);
      if (spike) return 'Cut the ' + low + '. That was ' + spike[1] + ' volts.';
      if (why === 'shed: on inverter') return nm + ' off. We are on battery.';
      if (why === 'shed: night, on inverter') return nm + ' off for the night. Saving the battery.';
      if (why === 'restored: mains back') return nm + ' back on. Mains is steady.';
      if (/tank at/.test(why)) return on ? 'Tank was low. Pump running.' : 'Tank is full. Pump off.';
      if (why === 'from dashboard') return 'You switched the ' + low + (on ? ' on.' : ' off.');
      if (why === 'someone in the house') return 'Somebody put ' + (on ? 'on the ' : 'off the ') + low + '.';
      return nm + (on ? ' on.' : ' off.') + (why ? ' ' + why + '.' : '');
    }
    switch (what) {
      /* nothing playful in an alert about something that costs money or breaks */
      case 'VOLTAGE SPIKE':    return 'Spike on the line. ' + why.replace(/V$/, ' volts') + '.';
      case 'BATTERY FLAT':     return 'Battery is finished. Everything is off.';
      case 'TANK FULL':        return 'Tank is full and the pump is still running.';
      case 'TANK OVERFLOWING': return 'Tank is overflowing. Water is wasting.';
      case 'ALERT':            return why ? spoken(why) + '.' : 'Something needs looking at.';
      case 'GEN STARTED':      return 'Generator is on.';
      case 'GEN OFF':          return 'Generator off. On battery now.';
      case 'NEPA TOOK LIGHT':  return 'NEPA don take light.';
      case 'LIGHT IS BACK':    return 'NEPA don bring light.';
    }
    return spoken(what) + (why ? '. ' + why : '') + '.';
  }

  /* ---- rolling digits -------------------------------------------------- */
  function setDigits(node, value){
    var str = String(value);
    node.setAttribute('aria-hidden', 'true');
    if (node.childElementCount !== str.length) {
      node.textContent = '';
      for (var i = 0; i < str.length; i++) {
        var d = document.createElement('span');
        d.className = 'digit';
        var reel = document.createElement('span');
        reel.className = 'reel';
        for (var n = 0; n <= 9; n++) {
          var b = document.createElement('b');
          b.textContent = n;
          reel.appendChild(b);
        }
        d.appendChild(reel);
        node.appendChild(d);
      }
    }
    for (var j = 0; j < str.length; j++) {
      node.children[j].firstChild.style.transform = 'translateY(-' + str.charAt(j) + 'em)';
    }
  }

  function ago(ms){
    var s = Math.floor(ms / 1000);
    if (s < 60) return s + (s === 1 ? ' second' : ' seconds');
    var m = Math.floor(s / 60);
    if (m < 60) return m + (m === 1 ? ' minute' : ' minutes');
    var h = Math.floor(m / 60);
    return h + (h === 1 ? ' hour' : ' hours');
  }

  function isStale(){ return lastOkAt === null || (Date.now() - lastOkAt) > STALE_MS; }

  function freshness(){
    var stale = isStale();
    document.body.classList.toggle('stale', stale);
    Nova.live(!stale);
    var msg;
    if (lastOkAt === null) {
      msg = triedOnce ? 'Can’t reach the hub. Nothing has loaded yet.' : 'Trying to reach the hub…';
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
    document.documentElement.dataset.source = d.source;
    el('source').textContent = SRC_LABEL[d.source] || d.source;
    el('sourcenote').textContent = SRC_NOTE[d.source] || '';
    el('headclock').textContent = d.clock;

    var pct = Math.max(0, Math.min(100, d.battery));
    var col = d.battery <= 20 ? 'var(--bad)' : d.battery <= 50 ? 'var(--warn)' : 'var(--ok)';
    var why = d.source === 'inverter' ? 'draining' : d.source === 'none' ? '' : 'charging';
    var whole = Math.round(d.battery);

    if (whole !== lastBatt) {
      setDigits(el('battdigits'), whole);
      setDigits(el('ringdigits'), whole);
      lastBatt = whole;
    }
    el('battsr').textContent = 'Battery ' + whole + ' percent' + (why ? ', ' + why : '') + '.';
    el('battwhy').textContent = why;
    el('ringwhy').textContent = why;
    el('battbar').style.width = pct + '%';
    el('battbar').style.background = col;
    el('ringfill').style.strokeDashoffset = RING * (1 - pct / 100);
    el('ringfill').style.stroke = col;

    /* the pulse behind the figure carries the load: calm at 300W, urgent at 2000W */
    var clamped = Math.min(2000, Math.max(300, d.draw));
    var pulse = (3.8 - (clamped - 300) / 1700 * 2.9).toFixed(1) + 's';
    if (pulse !== lastPulse) {
      el('figure').style.setProperty('--pulse', pulse);
      lastPulse = pulse;
    }

    fig.mood = (d.battery <= 20 || d.source === 'none') ? 'worried'
             : d.source === 'inverter' ? 'still'
             : d.source === 'gen'      ? 'alert' : '';
    paintFigure();
    Nova.state({source:d.source, battery:d.battery, draw:d.draw});

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
        d.tank >= 115 ? 'overflowing' : d.tank >= 100 ? 'full'
      : d.tank <= 20  ? 'low, the pump should run' : '';

    el('draw').textContent = d.draw;
    var on = 0;
    for (var i = 0; i < d.loads.length; i++) if (d.loads[i].on) on++;
    el('drawnote').textContent = on + ' of ' + d.loads.length + ' switched on';

    renderLoads(d.loads.slice().sort(function(a, b){ return b.watts - a.watts; }));
    renderLog(d.events);
  }

  function tier(w){ return w >= 700 ? 'heavy' : w >= 180 ? 'mid' : 'light'; }

  function renderLoads(loads){
    var wrap = el('loads');
    var order = loads.map(function(l){ return l.name; }).join(',');
    if (order !== loadOrder) {
      loadOrder = order;
      wrap.textContent = '';
      loads.forEach(function(l){
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'load ' + tier(l.watts);
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

  function paintFigure(){
    el('figure').className = 'figure' + (fig.mood ? ' ' + fig.mood : '');
  }

  function renderLog(events){
    var keys = events.map(keyOf);
    var known = seenKeys;
    var fresh = {};
    if (known) {
      keys.forEach(function(k){ if (known.indexOf(k) === -1) fresh[k] = true; });
    }
    seenKeys = keys;

    var ol = el('log');
    ol.textContent = '';
    if (!events.length) {
      var empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = 'Nothing yet.';
      ol.appendChild(empty);
      return;
    }
    var spiked = false;
    events.forEach(function(e){
      var k = keyOf(e);
      var li = document.createElement('li');
      li.className = (ALERTY.test(e.what + ' ' + e.why) ? 'alert' : '') + (fresh[k] ? ' fresh' : '');
      var t = document.createElement('time');
      t.textContent = e.time;
      var line = document.createElement('div');
      line.className = 'line';
      line.textContent = narrate(e);
      li.appendChild(t);
      li.appendChild(line);
      ol.appendChild(li);
      if (fresh[k] && /SPIKE/.test(e.what)) spiked = true;
    });
    if (spiked) Nova.spike();
  }

  function schedule(ms){ clearTimeout(timer); timer = setTimeout(poll, ms); }

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
    var nm = nameOf(name), low = nm === 'TV' ? 'TV' : nm.toLowerCase();
    if (isStale()) {
      toast('Can’t reach the hub. The ' + low + ' did not move.');
      return;
    }
    var current = null;
    last.loads.forEach(function(l){ if (l.name === name) current = l.on; });
    if (current === null || pending[name]) return;

    var want = !current;
    pending[name] = true;
    renderLoads(last.loads.slice().sort(function(a, b){ return b.watts - a.watts; }));

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
          toast('The rules put the ' + low + ' back ' + (d.actual ? 'on' : 'off') +
                (why ? ' (' + why + ').' : '.'), 'rules');
        }
      })
      .catch(function(){
        delete pending[name];
        toast('That did not get through. The ' + low + ' is still ' + (current ? 'on' : 'off') + '.');
        if (last) renderLoads(last.loads.slice().sort(function(a, b){ return b.watts - a.watts; }));
        freshness();
      })
      .then(function(){ clearTimeout(killer); });
  }

  document.addEventListener('visibilitychange', function(){
    if (!document.hidden) { freshness(); schedule(0); }
  });

  Nova.init();
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
