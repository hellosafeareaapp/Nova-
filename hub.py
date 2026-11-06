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
    # how a surface is made, as opposed to what colour it is. "material" is a
    # named recipe for the knobs under it; any knob set by hand wins over it.
    "surface": {
        "material": "card", "radius": 16, "stroke": 1, "lift": "soft",
        "glow": 0.0, "blur": 0, "fill": 1.0, "strokefrom": "rule",
    },
    "mode": "dark",
    # a whole look in one word. everything below it is still overridable.
    "look": "instrument",
    "layout": {"preset": "cards", "density": "comfortable"},
    "voice": {"wake": "Hey A.D.A.", "pidgin": True, "style": "plain"},
    "nova": {"particles": 2200, "fps": 30},
}

# Five ways of making a surface. The dashboard used to have exactly one of
# these welded into the stylesheet, which is why every recolouring of it still
# looked like the same dashboard.
MATERIALS = {
    # a panel with a hairline and a crisp shadow. what was here before.
    "card":    {"radius": 16, "stroke": 1, "lift": "soft", "glow": 0.0,
                "blur": 0, "fill": 1.0, "strokefrom": "rule"},
    # matte. no border, no shadow: a block of colour separates itself.
    "chalk":   {"radius": 18, "stroke": 0, "lift": "none", "glow": 0.0,
                "blur": 0, "fill": 1.0, "strokefrom": "rule"},
    # no fill to speak of, a lit edge in the source colour. reads as emitted.
    "neon":    {"radius": 14, "stroke": 1, "lift": "none", "glow": 0.75,
                "blur": 0, "fill": 0.34, "strokefrom": "src"},
    # printed. tight corners, one hairline, nothing floating.
    "paper":   {"radius": 6, "stroke": 1, "lift": "none", "glow": 0.0,
                "blur": 0, "fill": 1.0, "strokefrom": "rule"},
    # translucent over whatever is behind it. wants a wallpaper to be worth it.
    "frosted": {"radius": 22, "stroke": 1, "lift": "soft", "glow": 0.0,
                "blur": 18, "fill": 0.40, "strokefrom": "ink"},
}

# A look is a complete set of settings under one name, so a theme can be sent
# to someone as {"look": "retrowave"} and arrive whole. These sit between the
# defaults and settings.json: the owner overrides any part of one they like.
LOOKS = {
    # the night instrument panel. the dashboard as built.
    "instrument": {},

    # neon over violet. surfaces stop being filled and start being outlined.
    "retrowave": {
        "mode": "dark",
        "surface": {"material": "neon"},
        "theme": {
            "ink": "#f6e9ff", "dim": "#a081c6",
            "ok": "#2ff2c4", "warn": "#ffb200", "bad": "#ff3d7f",
            "sources": {
                "grid":     {"tint": "#140b26", "panel": "#1d1038", "rule": "#3a1f6b",
                             "sunk": "#241243", "src": "#49e0ff"},
                "gen":      {"tint": "#1e0d1e", "panel": "#2a1029", "rule": "#5a1f4e",
                             "sunk": "#331434", "src": "#ffb200"},
                "inverter": {"tint": "#0e0c2c", "panel": "#17123f", "rule": "#2e2378",
                             "sunk": "#1c1650", "src": "#b06bff"},
                "none":     {"tint": "#210a1b", "panel": "#2e0d25", "rule": "#5c1540",
                             "sunk": "#38112c", "src": "#ff3d7f"},
            },
        },
    },

    # warm, flat, close-packed. amber on near-black, no borders anywhere.
    "amber": {
        "mode": "dark",
        "surface": {"material": "chalk"},
        "theme": {
            "ink": "#f2e4ca", "dim": "#9c8a6b",
            "ok": "#86b06a", "warn": "#e8a33a", "bad": "#d9544a",
            "sources": {
                "grid":     {"tint": "#14110c", "panel": "#1e1913", "rule": "#2d2519",
                             "sunk": "#241d14", "src": "#e8a33a"},
                "gen":      {"tint": "#17120a", "panel": "#221b0f", "rule": "#352814",
                             "sunk": "#2a2012", "src": "#f0b24a"},
                "inverter": {"tint": "#10120f", "panel": "#1a1d17", "rule": "#272c22",
                             "sunk": "#1f231c", "src": "#9fc27a"},
                "none":     {"tint": "#1a0f0c", "panel": "#251611", "rule": "#3a1f18",
                             "sunk": "#2d1813", "src": "#d9544a"},
            },
        },
    },

    # daylight. ink on cream, hairlines, nothing glowing or floating.
    "paper": {
        "mode": "light",
        "surface": {"material": "paper"},
        "theme": {
            "ink": "#1c1a17", "dim": "#6f6a62",
            "ok": "#2f7d4f", "warn": "#9a5a12", "bad": "#a8322a",
            "sources": {
                "grid":     {"tint": "#f4f1ea", "panel": "#fffdf8", "rule": "#ddd7cb",
                             "sunk": "#e7e2d7", "src": "#3a5d8f"},
                "gen":      {"tint": "#f6f0e4", "panel": "#fffcf4", "rule": "#e2d8c2",
                             "sunk": "#ebe3d0", "src": "#9a5a12"},
                "inverter": {"tint": "#eef2f4", "panel": "#fbfdfe", "rule": "#d5dde2",
                             "sunk": "#e1e8ec", "src": "#2b6f8c"},
                "none":     {"tint": "#f8eeec", "panel": "#fffbfa", "rule": "#e7cfcb",
                             "sunk": "#f0dcd9", "src": "#a8322a"},
            },
        },
    },

    # glass. put an image in assets/ and set wallpaper for this to mean much.
    "frosted": {
        "mode": "dark",
        "surface": {"material": "frosted"},
        "wallpaper": {"dim": 0.34, "blur": 0},
        "theme": {
            "ink": "#f2f4f7", "dim": "#a8b0ba",
            "ok": "#5ec98f", "warn": "#f0a93c", "bad": "#f0655a",
            "sources": {
                "grid":     {"tint": "#121519", "panel": "#2b323b", "rule": "#3c4650",
                             "sunk": "#1c2127", "src": "#bcd0de"},
                "gen":      {"tint": "#19150f", "panel": "#352d21", "rule": "#473c2c",
                             "sunk": "#241e16", "src": "#f0a93c"},
                "inverter": {"tint": "#0f141c", "panel": "#242f3e", "rule": "#34435a",
                             "sunk": "#18202b", "src": "#79b4e8"},
                "none":     {"tint": "#1a1211", "panel": "#382423", "rule": "#4b2f2d",
                             "sunk": "#251817", "src": "#f0655a"},
            },
        },
    },
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
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if k not in base:
            continue
        if isinstance(base[k], dict):
            # a non-dict here would blow away a whole branch and take the
            # boot with it, so a mangled value just loses to the default
            out[k] = _merge(base[k], v) if isinstance(v, dict) else base[k]
        else:
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
    sf = st["surface"]
    if sf.get("material") not in MATERIALS:
        sf["material"] = "card"
    mat = MATERIALS[sf["material"]]
    if sf.get("strokefrom") not in ("rule", "src", "ink"):
        sf["strokefrom"] = mat["strokefrom"]
    if sf.get("lift") not in ("none", "soft", "deep"):
        sf["lift"] = mat["lift"]
    for k, lo, hi in (("radius", 0, 32), ("stroke", 0, 3), ("glow", 0.0, 1.0),
                      ("blur", 0, 30), ("fill", 0.0, 1.0)):
        try:
            sf[k] = min(hi, max(lo, float(sf.get(k))))
        except (TypeError, ValueError):
            sf[k] = mat[k]
    if st.get("mode") not in ("dark", "light"):
        st["mode"] = "dark"
    return st


def safe_asset(name):
    """A single plain filename inside assets/. No paths, no traversal."""
    name = str(name or "")
    return bool(name) and name == posixpath.basename(name) and ".." not in name \
        and "/" not in name and "\\" not in name and not name.startswith(".")


def _read_raw():
    """settings.json as written: sparse, only what the owner departed from."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _patch_raw(raw, patch, shape=None):
    """Deep-update the stored intent, dropping keys the schema does not know.

    This edits the sparse file rather than the composed result. Saving the
    composed result instead would bake every value a look supplied into the
    file as though the owner had picked it by hand, and the look could then
    never change anything again.
    """
    shape = DEFAULTS if shape is None else shape
    out = dict(raw)
    if not isinstance(patch, dict):
        return out
    for k, v in patch.items():
        if k not in shape:
            continue
        if isinstance(shape[k], dict) and isinstance(v, dict):
            prev = out.get(k)
            out[k] = _patch_raw(prev if isinstance(prev, dict) else {}, v, shape[k])
        else:
            out[k] = v
    return out


def load_settings():
    """Four layers, each one overridable by the next: the defaults, the named
    look, the material's surface recipe, and finally settings.json itself.

    The material only fills in surface knobs that neither the look nor the
    file mentioned, so {"look": "retrowave"} is a complete working theme and
    {"look": "retrowave", "surface": {"glow": 0.2}} is that theme turned down.
    """
    raw = _read_raw()

    look = raw.get("look")
    if look not in LOOKS:
        look = DEFAULTS["look"]
    st = _merge(_merge(DEFAULTS, LOOKS[look]), raw)
    st["look"] = look

    looksurf = LOOKS[look].get("surface") or {}
    rawsurf = raw.get("surface") if isinstance(raw.get("surface"), dict) else {}
    spoken = set(looksurf) | set(rawsurf)
    # a material nobody has heard of is a typo, not a decision, so the look
    # keeps the one it chose rather than being demoted to the global default
    if st["surface"].get("material") not in MATERIALS:
        st["surface"]["material"] = looksurf.get(
            "material", DEFAULTS["surface"]["material"])
    mat = MATERIALS[st["surface"]["material"]]
    for k, v in mat.items():
        if k not in spoken:
            st["surface"][k] = v
    return _clean(st)


def save_settings(st):
    """Written to a temporary file and moved into place, so a crash mid-write
    cannot leave the owner with half a settings file."""
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, SETTINGS_FILE)


def _rgba(hexcol, alpha):
    """A hex colour at an opacity, composed here rather than with color-mix()
    so it works on whatever browser the phone happens to have."""
    h = str(hexcol).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r = g = b = 128
    if alpha >= 1:
        return "#%02x%02x%02x" % (r, g, b)
    return "rgba(%d,%d,%d,%.3f)" % (r, g, b, max(0.0, alpha))


# how hard a shadow falls, per mode. a light surface cannot take a black one.
LIFTS = {
    "dark":  {"none": None, "soft": "0 10px 28px rgba(0,0,0,.34)",
              "deep": "0 18px 48px rgba(0,0,0,.55)"},
    "light": {"none": None, "soft": "0 6px 18px rgba(42,36,26,.09)",
              "deep": "0 14px 34px rgba(42,36,26,.17)"},
}

# what "no longer live" looks like. it has to read as wrong in either mode,
# because this is the one thing on the page that must never be missable.
STALEFX = {"dark":  "grayscale(.94) brightness(.5) opacity(.62)",
           "light": "grayscale(.94) brightness(1.04) opacity(.4)"}

STROKE_ALPHA = {"rule": 1.0, "src": 0.55, "ink": 0.20}


def _surface_vars(pal, sf, th, mode):
    """The surface half of the palette: not what colour a card is, but what
    kind of thing it is. Per source, because a lit edge is the source colour."""
    fill = sf["fill"]
    edge = {"rule": pal["rule"], "src": pal["src"], "ink": th["ink"]}[sf["strokefrom"]]
    big, small = [], []
    if sf["glow"] > 0:
        g = sf["glow"]
        big.append("0 0 %dpx %s" % (int(10 + 26 * g), _rgba(pal["src"], .14 + .40 * g)))
        big.append("inset 0 0 %dpx %s" % (int(8 + 20 * g), _rgba(pal["src"], .04 + .11 * g)))
        small.append("0 0 %dpx %s" % (int(6 + 14 * g), _rgba(pal["src"], .10 + .26 * g)))
    shade = LIFTS[mode][sf["lift"]]
    if shade:
        big.append(shade)
        small.append(shade)
    glass = ("blur(%dpx) saturate(1.35)" % int(sf["blur"])) if sf["blur"] else "none"
    return [
        "--fill:%s" % _rgba(pal["panel"], fill),
        "--loadfill:%s" % _rgba(pal["tint"], min(1.0, fill + .12)),
        "--stroke:%s" % _rgba(edge, STROKE_ALPHA[sf["strokefrom"]]),
        # the separators that hold the phone layout together. always drawn, at
        # 1px, whatever the material does -- without them the columns merge.
        "--bandrule:%s" % (_rgba(pal["src"], .42) if sf["strokefrom"] == "src"
                           else pal["rule"]),
        "--sw:%gpx" % sf["stroke"],
        "--r:%dpx" % int(sf["radius"]),
        "--rs:%dpx" % max(0, int(sf["radius"]) - 3),
        "--shadow:%s" % (", ".join(big) if big else "none"),
        "--shadow-sm:%s" % (", ".join(small) if small else "none"),
        "--glass:%s" % glass,
        # hover has to land on something: a lit edge where there is an edge,
        # a shift in the fill where there is not
        "--hoverfill:%s" % (_rgba(pal["sunk"], min(1.0, fill + .12))
                            if sf["stroke"] <= 0 else _rgba(pal["tint"], min(1.0, fill + .12))),
    ]


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
    sf, mode = st["surface"], st["mode"]
    base.append("color-scheme:%s" % mode)
    base.append("--stalefx:%s" % STALEFX[mode])
    base += ["--%s:%s" % (k, v) for k, v in th["sources"]["grid"].items()]
    base += _surface_vars(th["sources"]["grid"], sf, th, mode)
    out.append(":root{%s;}" % ";".join(base))
    for name, pal in th["sources"].items():
        out.append(':root[data-source="%s"]{%s;}'
                   % (name, ";".join(["--%s:%s" % (k, v) for k, v in pal.items()]
                                     + _surface_vars(pal, sf, th, mode))))
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
<li><a href="/settings">/settings</a> &mdash; the resolved look, type, surface and wallpaper</li>
<li><a href="/tokens.css">/tokens.css</a> &mdash; those settings as CSS custom properties</li>
<li>POST /switch &mdash; {"name": "kettle", "state": true}; the rules run after and may overrule it</li>
<li>POST /settings &mdash; a partial settings object, e.g. {"look": "retrowave"}</li>
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
