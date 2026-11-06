"""
A.D.A. hub: house_sim running behind a local web server.

The dashboard that used to live here was deleted; what remains is the API
and the settings engine it will be rebuilt on. GET / says as much.

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
# There are no palettes here, no named looks and no materials. Those were one
# person's taste and they went out with the design they belonged to.
#
# What is left is only the mechanism. You name whatever CSS variables your
# design needs in settings.json and they come back from /tokens.css. The
# server has no opinion about what they are or what you call them.
#
#     {"vars": {"ink": "#111", "panel": "#fff", "radius": "14px"}}
#       ->  :root{--ink:#111;--panel:#fff;--radius:14px}

DEFAULTS = {
    "vars": {},
    "type": {"scale": 1.0, "faces": []},
    "wallpaper": {"image": "", "dim": 0.0, "blur": 0},
}

FONT_EXT = (".woff2", ".woff", ".ttf", ".otf")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
ASSET_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
               ".otf": "font/otf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
               ".avif": "image/avif"}

# a custom property name, and a value that cannot close the rule it sits in
VAR_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
VAR_BAD = re.compile(r"[;{}<>\\@]")

SETTINGS_LOCK = threading.Lock()


def safe_asset(name):
    """A single plain filename inside assets/. No paths, no traversal."""
    name = str(name or "")
    return bool(name) and name == posixpath.basename(name) and ".." not in name \
        and "/" not in name and "\\" not in name and not name.startswith(".")


def _merge(base, over):
    """Overlay settings.json on the defaults, key by key."""
    out = dict(base)
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if k not in base:
            continue
        if isinstance(base[k], dict):
            # vars is the owner's own namespace, so it is taken whole; a
            # non-dict anywhere else loses to the default rather than
            # replacing a branch and taking the boot down with it
            out[k] = (dict(v) if k == "vars" else _merge(base[k], v)) \
                if isinstance(v, dict) else base[k]
        else:
            out[k] = v
    return out


def _clean(st):
    """Keep a hand-edited file from bricking the dashboard. Anything that does
    not make sense quietly reverts rather than raising: this is read during a
    blackout, so it has to boot no matter what is in the file."""
    good = {}
    for k, v in (st.get("vars") or {}).items():
        k, v = str(k).lstrip("-"), str(v)
        if VAR_NAME.match(k) and v and not VAR_BAD.search(v):
            good[k] = v
    st["vars"] = good

    ty = st["type"]
    try:
        ty["scale"] = min(1.6, max(0.75, float(ty.get("scale", 1))))
    except (TypeError, ValueError):
        ty["scale"] = 1.0
    ty["faces"] = [f for f in (ty.get("faces") or [])
                   if isinstance(f, dict) and safe_asset(f.get("file", ""))
                   and str(f.get("file", "")).lower().endswith(FONT_EXT)][:8]

    wp = st["wallpaper"]
    if not safe_asset(wp.get("image", "")) or \
            not str(wp.get("image", "")).lower().endswith(IMAGE_EXT):
        wp["image"] = ""
    for k, lo, hi in (("dim", 0.0, 1.0), ("blur", 0, 40)):
        try:
            wp[k] = min(hi, max(lo, float(wp.get(k, 0))))
        except (TypeError, ValueError):
            wp[k] = DEFAULTS["wallpaper"][k]
    return st


def _read_raw():
    """settings.json as written: sparse, only what the owner set."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _patch_raw(raw, patch):
    """Deep-update the stored file. Edits intent, not the composed result."""
    out = dict(raw)
    if not isinstance(patch, dict):
        return out
    for k, v in patch.items():
        if k not in DEFAULTS:
            continue
        if k == "vars" and isinstance(v, dict):
            merged = dict(out.get("vars") or {})
            for name, val in v.items():
                if val is None:
                    merged.pop(str(name), None)
                else:
                    merged[str(name)] = val
            out["vars"] = merged
        elif isinstance(DEFAULTS[k], dict) and isinstance(v, dict):
            prev = out.get(k)
            sub = dict(prev) if isinstance(prev, dict) else {}
            sub.update(v)
            out[k] = sub
        else:
            out[k] = v
    return out


def load_settings():
    return _clean(_merge(DEFAULTS, _read_raw()))


def save_settings(st):
    """Written to a temporary file and moved into place, so a crash mid-write
    cannot leave the owner with half a settings file."""
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, SETTINGS_FILE)


def tokens_css(st):
    """settings.json rendered as a stylesheet, and nothing more."""
    ty, wp = st["type"], st["wallpaper"]
    out = []
    for f in ty["faces"]:
        out.append("@font-face{font-family:%s;src:url('/assets/%s');"
                   "font-display:swap;}"
                   % (json.dumps(str(f.get("name", "Owner"))), f["file"]))
    decls = ["--%s:%s" % (k, v) for k, v in sorted(st["vars"].items())]
    decls.append("--scale:%s" % ty["scale"])
    out.append(":root{%s;}" % ";".join(decls))
    if wp["image"]:
        out.append("body{background-image:url('/assets/%s');background-size:cover;"
                   "background-position:center;background-attachment:fixed;}"
                   % wp["image"])
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
            self._send(200, STUB, "text/html; charset=utf-8")
            return
        if path == "/settings":
            self._json(200, load_settings())
            return
        # the settings engine, rendered. whatever client comes next links
        # this one file and gets the owner's colours, type and surfaces.
        if path == "/tokens.css":
            self._send(200, tokens_css(load_settings()), "text/css; charset=utf-8")
            return
        if path.startswith("/assets/"):
            self._asset(path[len("/assets/"):])
            return
        if path == "/state":
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
                raw = _patch_raw(_read_raw(), patch)
                try:
                    save_settings(raw)
                except OSError as err:
                    self._json(500, {"error": "could not save: %s" % err}); return
                st = load_settings()
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


# ------------------------------------------------------------- the client

# The front end was deleted on purpose. Everything below the API line still
# works -- the simulator, the rules, the event feed, the settings engine --
# and this page exists only so that `python hub.py` still starts and tells
# you what is there. It has no stylesheet and no script, by design: the
# next client is written from nothing, staleness included.

STUB = """<!doctype html>
<meta charset="utf-8">
<title>The house</title>
<h1>The house</h1>
<p>No front end. The server is running.</p>
<ul>
<li><a href="/state">/state</a> &mdash; what the house is doing right now</li>
<li><a href="/settings">/settings</a> &mdash; whatever variables, type and wallpaper you have set</li>
<li><a href="/tokens.css">/tokens.css</a> &mdash; those settings as CSS custom properties</li>
<li>POST /switch &mdash; {"name": "kettle", "state": true}; the rules run after and may overrule it</li>
<li>POST /settings &mdash; e.g. {"vars": {"ink": "#111", "panel": "#fff"}}</li>
</ul>
<p>One real second is one simulated minute, and the day repeats.</p>
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
