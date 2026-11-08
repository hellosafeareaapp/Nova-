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
import hashlib
import json
import os
import posixpath
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.request
from collections import deque
from datetime import date, timedelta
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

# A real controller (the ESP32 in firmware/) reports here once a second. While
# it is reporting it owns the power source and mirrors the loads onto its
# relays; when it goes quiet the hub says so instead of pretending.
DEVICE_KEY_FILE = os.path.join(HERE, "device_key.txt")
DEVICE_TIMEOUT = 5.0      # seconds without a report before the device is offline
SOURCES = ("grid", "gen", "inverter", "none")
SOURCE_EVENTS = {
    "grid": ("LIGHT IS BACK", ""),
    "gen": ("GEN STARTED", ""),
    "inverter": ("ON INVERTER", "on battery now"),
    "none": ("NO POWER", ""),
}

# The dashboard is served from here, so a phone on the same Wi-Fi opens
# http://<hub>:8000 and gets live data with no CORS or mixed-content trouble.
DASHBOARD_FILE = os.path.join(HERE, "mockup", "index.html")

# Phone alerts, site name and diesel price. Kept out of settings.json because
# /settings is readable by anyone on the network and this holds bot tokens.
CONFIG_FILE = os.path.join(HERE, "hub_config.json")
HISTORY_FILE = os.path.join(HERE, "history.json")
CONFIG_DEFAULTS = {
    "site_name": "My building",
    "diesel_price": 1300,        # naira per litre; set your own, prices move
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",            # pick something long and unguessable
    "telegram_token": "",
    "telegram_chat": "",
    # the building, so one hub fits more than the demo house
    "diesel_cap": 50.0,          # generator tank, litres
    "gen_lph": 2.6,              # litres per running hour, until a sensor measures it
    "battery_kwh": 5.0,          # inverter bank
    "alert_diesel_l": 10.0,
    "alert_battery_pct": 20.0,
    "alert_tank_pct": 15.0,
    "service_hours": 250.0,      # generator service interval
}
CONFIG_LIMITS = {"diesel_cap": (5, 5000), "gen_lph": (0.2, 100), "battery_kwh": (0.5, 500),
                 "alert_diesel_l": (0, 1000), "alert_battery_pct": (5, 90),
                 "alert_tank_pct": (0, 90), "service_hours": (10, 5000), "diesel_price": (0, 100000)}

# Generator tank. The ESP32 can report a measured level ("fuel_l"); until one
# does, the hub burns diesel at gen_lph (hub_config.json) while the gen runs.
GEN_LPH = 2.6                    # only for the sample week; live uses cfg("gen_lph")
DROP_ALERT_L = 2.0               # unexplained loss while the gen is off
REFILL_MIN_L = 3.0

# The ESP32 listens for this broadcast to find the hub by itself, so a laptop
# or phone hotspot handing out a new IP no longer strands it.
DISCOVERY_PORT = 41234

ALERT_COOLDOWN_MIN = 60          # same alert at most once per simulated hour
ALERT_LIMIT = 50

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
        self.fuel = 36.0                 # litres in the generator tank
        self.fuel_measured = False       # True once a real sensor reports
        self.battery_measured = False
        self.fuel_base = self.fuel       # level when the gen last stopped
        self.simday = 0

    def event(self, what, why=""):
        super().event(what, why)
        self.events.append({"time": self.clock, "what": what, "why": why})
        if len(self.events) > EVENT_LIMIT:
            del self.events[:-EVENT_LIMIT]
        if len(self.log) > LOG_LIMIT:
            del self.log[:-LOG_LIMIT]
        if what == "VOLTAGE SPIKE":
            STATS.add("spikes", 1)
        alert_from_event(self, what, why)

    def tick(self):
        was_inv = self.source == "inverter"
        super().tick()
        if was_inv and self.source in ("inverter", "none") and not self.battery_measured:
            # house_sim drains as if the bank were 10 kWh; rescale to this building's
            self.battery += self.draw / 6000.0
            self.battery = max(0.0, self.battery - self.draw * 100.0 / (60.0 * cfg("battery_kwh") * 1000))
        if self.minute % DAY == 0:
            self.simday += 1
        if self.source == "gen" and not self.fuel_measured:
            self.fuel = max(0.0, self.fuel - cfg("gen_lph") / 60.0)
            STATS.add("gen_l", cfg("gen_lph") / 60.0)
            if self.fuel == 0:
                self.event("DIESEL FINISHED", "generator stopped")
                self.source = "inverter" if self.battery > 0 else "none"

    def delivery_checked(self, d):
        if d["short_l"] >= max(2.0, d["claimed_l"] * 0.05):
            self.event("DELIVERY SHORT", "{} said {:.0f} L, tank rose {:.1f} L".format(
                d["vendor"] or "vendor", d["claimed_l"], d["measured_l"]))
        else:
            self.event("DELIVERY OK", "{:.0f} L confirmed at the tank".format(d["claimed_l"]))

    def set_fuel(self, litres, measured=False):
        """A new tank level, from the sensor or the demo buttons. Drops while
        the generator is off and refills are noticed here, nowhere else."""
        litres = max(0.0, min(cfg("diesel_cap"), float(litres)))
        if measured:
            self.fuel_measured = True
        prev, self.fuel = self.fuel, litres
        if self.source == "gen":
            if measured and prev > litres:
                STATS.add("gen_l", prev - litres)
            self.fuel_base = litres
            return
        if litres >= self.fuel_base + REFILL_MIN_L:
            added = litres - self.fuel_base
            STATS.add("refill_l", added)
            self.event("DIESEL REFILL", "+{:.1f} L".format(added))
            self.fuel_base = litres
            d = STATS.refill_seen(added, self.simday * DAY + self.minute % DAY)
            if d:
                self.delivery_checked(d)
        elif litres <= self.fuel_base - DROP_ALERT_L:
            lost = self.fuel_base - litres
            STATS.add("drop_l", lost)
            STATS.add("drops", 1)
            self.event("DIESEL DROP", "{:.1f} L while gen off".format(lost))
            self.fuel_base = litres


# ---------------------------------------------------------------- people & PINs
# Nobody has a PIN until the owner sets one; until then the hub is open, as it
# always was. Once anyone has a PIN, every reading and switch needs a login.

USERS_FILE = os.path.join(HERE, "users.json")
ROLES = {"resident": 1, "caretaker": 2, "manager": 3, "owner": 4}
ROLE_LABEL = {"resident": "Resident", "caretaker": "Caretaker", "manager": "Manager", "owner": "Owner"}
NEEDS = {  # minimum role level per route
    ("GET", "/state"): 1, ("GET", "/report.json"): 1, ("GET", "/alerts"): 1,
    ("GET", "/config"): 1, ("GET", "/settings"): 1, ("GET", "/users"): 3,
    ("GET", "/setup"): 3,
    ("POST", "/switch"): 2, ("POST", "/source"): 2, ("POST", "/fuel"): 2,
    ("POST", "/service"): 2, ("POST", "/delivery"): 2,
    ("POST", "/config"): 3, ("POST", "/notify/test"): 3, ("POST", "/settings"): 3,
    ("POST", "/users"): 3, ("POST", "/logout"): 1,
}
USERS_LOCK = threading.Lock()
LOGIN_FAILS = {}                 # ip -> [count, locked_until]


def load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    users = [u for u in raw.get("users", []) if isinstance(u, dict) and u.get("role") in ROLES]
    sessions = raw.get("sessions") if isinstance(raw.get("sessions"), dict) else {}
    return {"users": users, "sessions": sessions}


def save_users(db):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(db, fh, indent=1)
    os.replace(tmp, USERS_FILE)


def pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 120000).hex()


def public_users(db):
    return [{"name": u["name"], "role": u["role"], "label": ROLE_LABEL[u["role"]]} for u in db["users"]]


# ---------------------------------------------------------------- config file

CONFIG_LOCK = threading.Lock()


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    out = dict(CONFIG_DEFAULTS)
    if isinstance(raw, dict):
        for k in CONFIG_DEFAULTS:
            if k in raw:
                v = clean_config_value(k, raw[k])
                if v is not None:
                    out[k] = v
    return out


def clean_config_value(k, v):
    """One setting, checked: right type, inside sane limits. None if not."""
    d = CONFIG_DEFAULTS[k]
    if isinstance(d, str):
        return v.strip()[:200] if isinstance(v, str) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    lo, hi = CONFIG_LIMITS.get(k, (float("-inf"), float("inf")))
    v = min(hi, max(lo, float(v)))
    return int(v) if isinstance(d, int) else v


CFG = {}                         # cached copy for the sim thread; refreshed on save


def cfg(k):
    return CFG.get(k, CONFIG_DEFAULTS[k])


def save_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)
    os.replace(tmp, CONFIG_FILE)


def public_config(cfg):
    """What the dashboard may see: whether alerts are set up, not the secrets."""
    topic = cfg["ntfy_topic"]
    building = {k: cfg[k] for k in CONFIG_LIMITS}
    return dict(building, **{
        "site_name": cfg["site_name"],
        "diesel_price": cfg["diesel_price"],
        "ntfy_server": cfg["ntfy_server"],
        "ntfy_topic_set": bool(topic),
        "ntfy_topic_hint": (topic[:3] + "..." + topic[-2:]) if len(topic) > 6 else ("set" if topic else ""),
        "telegram_set": bool(cfg["telegram_token"] and cfg["telegram_chat"]),
        "last_error": NOTIFY_STATE["last_error"],
        "last_sent": NOTIFY_STATE["last_sent"],
    })


# ---------------------------------------------------------------- alerts
# Every alert starts life as a house event, so the rules stay the only thing
# deciding what happened; this table only decides who gets woken up for it.

ALERT_RULES = {
    # event what        level    title
    "DIESEL DROP":      ("bad",  "Diesel dropped while the generator was off"),
    "DIESEL LOW":       ("warn", "Diesel is running low"),
    "DIESEL FINISHED":  ("bad",  "Generator ran out of diesel"),
    "BATTERY FLAT":     ("bad",  "Inverter battery is flat"),
    "VOLTAGE SPIKE":    ("warn", "Voltage spike"),
    "TANK OVERFLOWING": ("warn", "Water tank is overflowing"),
    "TANK LOW":         ("warn", "Water tank is low and there is no power to pump"),
    "DEVICE OFFLINE":   ("bad",  "Controller went offline"),
    "DEVICE ONLINE":    ("ok",   "Controller is back online"),
    "BATTERY LOW":      ("warn", "Inverter battery is low"),
    "SERVICE DUE":      ("warn", "Generator service is due"),
    "DELIVERY SHORT":   ("bad",  "Diesel delivery came up short"),
}
ALERTS = deque(maxlen=ALERT_LIMIT)
ALERT_LAST = {}                  # key -> absolute sim minute last raised
NOTIFY_QUEUE = deque(maxlen=100)
NOTIFY_EVENT = threading.Event()
NOTIFY_STATE = {"last_error": "", "last_sent": ""}
_alert_seq = [0]


def alert_from_event(h, what, why):
    rule = ALERT_RULES.get(what)
    if not rule:
        return
    key = what
    last = ALERT_LAST.get(key)
    now = h.simday * DAY + (h.minute % DAY)
    if last is not None and now - last < ALERT_COOLDOWN_MIN and what != "DEVICE ONLINE":
        return
    ALERT_LAST[key] = now
    level, title = rule
    _alert_seq[0] += 1
    a = {"id": _alert_seq[0], "time": h.clock, "level": level, "title": title,
         "body": why or "", "key": key}
    ALERTS.appendleft(a)
    STATS.add("alerts", 1)
    NOTIFY_QUEUE.append(a)
    NOTIFY_EVENT.set()


def check_conditions(h):
    """Alerts that are states rather than events. Each fires once on the way
    in and re-arms on the way out, so a slow drain does not spam the phone."""
    low_tank = h.tank <= cfg("alert_tank_pct") and h.source not in ("grid", "gen")
    if low_tank and not h.__dict__.get("_tank_low"):
        h.event("TANK LOW", "{:.0f}% and on {}".format(h.tank, h.source))
    h._tank_low = low_tank
    low_fuel = h.fuel <= cfg("alert_diesel_l")
    if low_fuel and not h.__dict__.get("_fuel_low"):
        h.event("DIESEL LOW", "{:.1f} L left".format(h.fuel))
    h._fuel_low = low_fuel
    low_batt = h.source == "inverter" and h.battery <= cfg("alert_battery_pct")
    if low_batt and not h.__dict__.get("_batt_low"):
        h.event("BATTERY LOW", "{:.0f}% left".format(h.battery))
    h._batt_low = low_batt
    g = STATS.gen_status()
    due = g["since_service_h"] >= g["interval_h"]
    if due and not h.__dict__.get("_service_due"):
        h.event("SERVICE DUE", "generator ran {:.0f} h since its last service".format(g["since_service_h"]))
    h._service_due = due


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()


def send_alert(a, cfg):
    """Pushes one alert out. Runs on the notifier thread, never under LOCK,
    so a slow or absent internet connection cannot stall the house."""
    site = cfg["site_name"]
    sent, errors = [], []
    text = "{} - {}".format(a["title"], a["body"]) if a["body"] else a["title"]
    if cfg["ntfy_topic"]:
        try:
            _post(cfg["ntfy_server"].rstrip("/") + "/" + cfg["ntfy_topic"],
                  text.encode("utf-8"),
                  {"Title": ("A.D.A. " + site).encode("ascii", "ignore").decode(),
                   "Priority": {"bad": "high"}.get(a["level"], "default"),
                   "Tags": {"bad": "rotating_light", "warn": "warning"}.get(a["level"], "white_check_mark")})
            sent.append("ntfy")
        except Exception as err:            # no internet is normal here
            errors.append("ntfy: %s" % err)
    if cfg["telegram_token"] and cfg["telegram_chat"]:
        try:
            body = json.dumps({"chat_id": cfg["telegram_chat"],
                               "text": "A.D.A. {} ({})\n{}".format(site, a["time"], text)}).encode()
            _post("https://api.telegram.org/bot%s/sendMessage" % cfg["telegram_token"],
                  body, {"Content-Type": "application/json"})
            sent.append("telegram")
        except Exception as err:
            errors.append("telegram: %s" % err)
    a["sent"] = sent
    NOTIFY_STATE["last_error"] = "; ".join(errors)
    if sent:
        NOTIFY_STATE["last_sent"] = time.strftime("%H:%M:%S")
    return sent, errors


def notifier():
    while True:
        NOTIFY_EVENT.wait(5)
        NOTIFY_EVENT.clear()
        while NOTIFY_QUEUE:
            a = NOTIFY_QUEUE.popleft()
            cfg = load_config()
            if cfg["ntfy_topic"] or cfg["telegram_token"]:
                send_alert(a, cfg)


# ---------------------------------------------------------------- history
# One bucket per (simulated) day. This is what the weekly report is built
# from, and it survives a restart.

DAY_FIELDS = ("min_grid", "min_gen", "min_inverter", "min_none", "gen_l",
              "refill_l", "drop_l", "drops", "pump_min", "wh", "spikes",
              "alerts", "overflow_min", "offline_min")


class Stats:
    def __init__(self):
        self.lock = threading.RLock()
        self.days = {}
        self.seeded = False
        self.start = date.today()
        self.today_key = self.start.isoformat()
        # generator hour meter and diesel deliveries, kept with the history
        self.meta = {"gen_total_min": 0, "service_at_min": 0, "services": [], "deliveries": []}
        self.last_refill = None          # {"l", "at", "matched"} for matching a delivery

    def _bucket(self):
        b = self.days.get(self.today_key)
        if b is None:
            b = {f: 0 for f in DAY_FIELDS}
            b["batt_min"] = 100.0
            b["hours"] = [[0, 0, 0, 0] for _ in range(24)]   # per hour: grid, gen, inverter, none minutes
            self.days[self.today_key] = b
        return b

    def roll(self, simday):
        self.today_key = (self.start + timedelta(days=simday)).isoformat()

    def add(self, field, n):
        with self.lock:
            b = self._bucket()
            b[field] = round(b.get(field, 0) + n, 3)

    def minute(self, h, offline):
        with self.lock:
            b = self._bucket()
            b["min_" + h.source] += 1
            if h.source == "gen":
                self.meta["gen_total_min"] += 1
            hrs = b.get("hours")
            if not isinstance(hrs, list) or len(hrs) != 24:
                hrs = b["hours"] = [[0, 0, 0, 0] for _ in range(24)]
            hrs[h.hour][SOURCES.index(h.source)] += 1
            b["wh"] = round(b["wh"] + (h.draw / 60.0 if h.source != "none" else 0), 1)
            if h.is_on("pump"):
                b["pump_min"] += 1
            if h.tank >= 100:
                b["overflow_min"] += 1
            if offline:
                b["offline_min"] += 1
            b["batt_min"] = round(min(b["batt_min"], h.battery), 1)

    def load(self):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self.days = {k: v for k, v in raw.get("days", {}).items() if isinstance(v, dict)}
            self.seeded = bool(raw.get("seeded"))
            m = raw.get("meta")
            if isinstance(m, dict):
                for k in self.meta:
                    if isinstance(m.get(k), type(self.meta[k])):
                        self.meta[k] = m[k]
            if raw.get("start"):
                self.start = date.fromisoformat(raw["start"])
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        with self.lock:
            data = {"start": self.start.isoformat(), "seeded": self.seeded, "days": self.days,
                    "meta": self.meta}
        tmp = HISTORY_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, HISTORY_FILE)
        except OSError:
            pass

    def seed_week(self):
        """Sample days before today, so a demo report is not empty. The report
        says plainly that it contains sample data."""
        import random as rnd
        rnd.seed(7)
        with self.lock:
            for back in range(7, 0, -1):
                k = (self.start - timedelta(days=back)).isoformat()
                self.days[k] = sample_day(rnd, back)
            self.seeded = True
        self.save()

    def gen_status(self):
        with self.lock:
            total = self.meta["gen_total_min"] / 60.0
            since = (self.meta["gen_total_min"] - self.meta["service_at_min"]) / 60.0
            last = self.meta["services"][-1] if self.meta["services"] else None
        interval = float(cfg("service_hours"))
        return {"total_h": round(total, 1), "since_service_h": round(since, 1),
                "interval_h": interval, "left_h": round(max(0.0, interval - since), 1),
                "last_service": last}

    def service_done(self, note, today):
        with self.lock:
            self.meta["services"].append({"date": today, "at_h": round(self.meta["gen_total_min"] / 60.0, 1),
                                          "note": note[:120]})
            self.meta["services"] = self.meta["services"][-20:]
            self.meta["service_at_min"] = self.meta["gen_total_min"]
        self.save()

    def refill_seen(self, litres, at):
        """A measured rise in the tank. Matched to a delivery logged just before
        or just after, so a short delivery is caught either way round."""
        with self.lock:
            for d in reversed(self.meta["deliveries"]):
                if d.get("measured_l") is None and at - d.get("at", -10**9) <= 120:
                    return self._settle(d, litres)
            self.last_refill = {"l": litres, "at": at, "matched": False}
        return None

    def add_delivery(self, vendor, litres, price, at, today, clock):
        with self.lock:
            d = {"id": len(self.meta["deliveries"]) + 1, "date": today, "time": clock, "at": at,
                 "vendor": vendor[:60], "claimed_l": round(litres, 1), "price": price,
                 "measured_l": None, "short_l": None}
            self.meta["deliveries"].append(d)
            self.meta["deliveries"] = self.meta["deliveries"][-60:]
            r = self.last_refill
            if r and not r["matched"] and at - r["at"] <= 120:
                r["matched"] = True
                return d, self._settle(d, r["l"])
        return d, None

    def _settle(self, d, measured):
        d["measured_l"] = round(measured, 1)
        d["short_l"] = round(max(0.0, d["claimed_l"] - measured), 1)
        return d

    def report(self, days, cfg):
        with self.lock:
            end = date.fromisoformat(self.today_key)
            keys = [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
            rows = []
            for k in keys:
                b = self.days.get(k)
                if b:
                    rows.append(dict(b, date=k))
        tot = {f: round(sum(r.get(f, 0) for r in rows), 1) for f in DAY_FIELDS}
        tot["batt_min"] = min([r.get("batt_min", 100) for r in rows] or [100])
        return {"site": cfg["site_name"], "diesel_price": cfg["diesel_price"],
                "from": keys[0], "to": keys[-1], "days_requested": days,
                "days": rows, "totals": tot, "seeded": self.seeded,
                "gen_lph": cfg["gen_lph"], "diesel_cap": cfg["diesel_cap"],
                "generated": time.strftime("%Y-%m-%d %H:%M"),
                "deliveries": [d for d in self.meta["deliveries"] if keys[0] <= d["date"] <= keys[-1]],
                "gen": self.gen_status()}


def sample_day(rnd, back):
    """A believable Lagos day, hour by hour: NEPA in blocks, generator in the
    evening gap, inverter overnight, sometimes a stretch with nothing."""
    hours = []
    nepa_from = rnd.choice([5, 6, 7, 8]); nepa_to = nepa_from + rnd.randint(4, 9)
    gen_from = nepa_to + rnd.randint(0, 3); gen_to = gen_from + rnd.randint(1, 3)
    eve_nepa = rnd.random() < 0.5
    flat = rnd.random() < 0.3
    for hr in range(24):
        if nepa_from <= hr < nepa_to or (eve_nepa and 19 <= hr < 22):
            src = 0
        elif gen_from <= hr < gen_to:
            src = 1
        elif flat and hr in (4, 5):
            src = 3
        else:
            src = 2
        row = [0, 0, 0, 0]
        row[src] = 60
        hours.append(row)
    tot = [sum(r[i] for r in hours) for i in range(4)]
    return {
        "min_grid": tot[0], "min_gen": tot[1], "min_inverter": tot[2], "min_none": tot[3],
        "gen_l": round(tot[1] / 60 * GEN_LPH, 1), "refill_l": 20 if back == 4 else 0,
        "drop_l": 6 if back == 2 else 0, "drops": 1 if back == 2 else 0,
        "pump_min": rnd.randint(40, 110), "wh": rnd.randint(9000, 16000),
        "spikes": rnd.randint(0, 3), "alerts": rnd.randint(0, 4),
        "overflow_min": 0, "offline_min": rnd.choice([0, 0, 0, 12]),
        "batt_min": rnd.randint(18, 60), "hours": hours,
    }


STATS = Stats()


def discovery(port):
    """Shouts "ADA-HUB <port>" on the local network every two seconds."""
    msg = "ADA-HUB {}".format(port).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            sock.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
        except OSError:
            pass                            # no network yet; keep trying
        time.sleep(2)


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
HUB_URLS = []                    # filled in by main(): where phones can reach this hub
LOCK = threading.Lock()

# What the hub knows about the controller. Guarded by LOCK.
DEVICE = {"id": None, "last": 0.0, "rssi": None, "uptime": None,
          "relays": {}, "online": False}


def device_key():
    """The shared key the controller must send. Made once, kept next to hub.py."""
    try:
        with open(DEVICE_KEY_FILE) as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(8)
    try:
        with open(DEVICE_KEY_FILE, "w") as fh:
            fh.write(key + "\n")
    except OSError:
        pass                        # read-only folder: the key lives for this run only
    return key


DEVICE_KEY = device_key()


def device_live():
    """True while the controller has reported recently. Caller holds LOCK."""
    live = DEVICE["id"] is not None and time.monotonic() - DEVICE["last"] <= DEVICE_TIMEOUT
    if DEVICE["online"] and not live:
        DEVICE["online"] = False
        HOUSE.event("DEVICE OFFLINE", "no report for {:.0f}s".format(DEVICE_TIMEOUT))
    return live


def simulate():
    """One real second is one simulated minute, looping the day forever."""
    next_tick = time.monotonic()
    while True:
        next_tick += TICK_SECONDS
        with LOCK:
            live = device_live()
            source = HOUSE.source
            with quiet():
                HOUSE.tick()
                if live:
                    # the controller owns the source; the scripted outage waits
                    HOUSE.source = source
                else:
                    house_sim.scenario_night_outage(HOUSE)
                house_sim.run_rules(HOUSE)
                check_conditions(HOUSE)
            STATS.roll(HOUSE.simday)
            STATS.minute(HOUSE, DEVICE["id"] is not None and not live)
            if HOUSE.minute >= WRAP_AT:
                HOUSE.minute -= DAY
            if HOUSE.minute % 30 == 0:
                save_now = True
            else:
                save_now = False
        if save_now:
            STATS.save()
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
        "fuel": round(HOUSE.fuel, 2),
        "fuel_measured": HOUSE.fuel_measured,
        "battery_measured": HOUSE.battery_measured,
        "diesel_cap": cfg("diesel_cap"),
        "gen": STATS.gen_status(),
        "deliveries": STATS.meta["deliveries"][-6:][::-1],
        "alerts": list(ALERTS)[:20],
        "loads": [
            {"name": name, "watts": load["watts"], "on": load["on"]}
            for name, load in HOUSE.loads.items()
        ],
        "events": list(reversed(HOUSE.events[-EVENT_LIMIT:])),
        "device": {
            "id": DEVICE["id"],
            "online": device_live(),
            "age": None if DEVICE["id"] is None else round(time.monotonic() - DEVICE["last"], 1),
            "rssi": DEVICE["rssi"],
            "relays": DEVICE["relays"],
        },
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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass                        # the phone wandered off mid-response

    def _json(self, code, payload):
        self._send(code, json.dumps(payload), "application/json; charset=utf-8")

    def _who(self):
        """(name, role) for this request, or ("", "owner") while no PINs exist."""
        db = load_users()
        if not db["users"]:
            return "", "owner", False
        tok = self.headers.get("X-ADA-Token", "")
        s = db["sessions"].get(tok) if tok else None
        if s and any(u["name"] == s["name"] and u["role"] == s["role"] for u in db["users"]):
            return s["name"], s["role"], True
        return None, None, True

    def _allowed(self, method, route):
        need = NEEDS.get((method, route))
        if need is None:
            return True
        name, role, locked = self._who()
        self.who = (name, role)
        if role is None:
            self._json(401, {"error": "login needed", "login": True}); return False
        if ROLES[role] < need:
            self._json(403, {"error": "a {} can't do that".format(ROLE_LABEL[role].lower())}); return False
        return True

    def _login(self, body):
        ip = self.client_address[0]
        fails = LOGIN_FAILS.get(ip, [0, 0])
        if time.monotonic() < fails[1]:
            self._json(429, {"error": "too many wrong PINs, wait a minute"}); return
        pin = str(body.get("pin") or "")
        with USERS_LOCK:
            db = load_users()
            for u in db["users"]:
                if secrets.compare_digest(pin_hash(pin, u["salt"]), u["hash"]):
                    tok = secrets.token_hex(16)
                    db["sessions"][tok] = {"name": u["name"], "role": u["role"], "at": time.strftime("%Y-%m-%d %H:%M")}
                    # keep the newest 50 sessions
                    if len(db["sessions"]) > 50:
                        for k in list(db["sessions"])[:-50]:
                            del db["sessions"][k]
                    save_users(db)
                    LOGIN_FAILS.pop(ip, None)
                    self._json(200, {"token": tok, "name": u["name"], "role": u["role"], "label": ROLE_LABEL[u["role"]]})
                    return
        fails[0] += 1
        if fails[0] >= 5:
            fails = [0, time.monotonic() + 60]
        LOGIN_FAILS[ip] = fails
        self._json(401, {"error": "wrong PIN"})

    def _logout(self, _body):
        tok = self.headers.get("X-ADA-Token", "")
        with USERS_LOCK:
            db = load_users()
            db["sessions"].pop(tok, None)
            save_users(db)
        self._json(200, {"ok": True})

    def _users(self, body):
        """Add or remove a person. Managers can't create or remove owners, and
        there is always at least one owner once PINs exist."""
        name, role, _ = self._who()
        action = body.get("action")
        with USERS_LOCK:
            db = load_users()
            if action == "add":
                who = str(body.get("name") or "").strip()[:30]
                new_role = body.get("role")
                pin = str(body.get("pin") or "")
                if not who or new_role not in ROLES:
                    self._json(400, {"error": "name and role needed"}); return
                if not (pin.isdigit() and 4 <= len(pin) <= 8):
                    self._json(400, {"error": "PIN must be 4 to 8 digits"}); return
                if not db["users"] and new_role != "owner":
                    self._json(400, {"error": "the first person must be the owner"}); return
                if ROLES[new_role] > ROLES[role]:
                    self._json(403, {"error": "you can't create someone above your own role"}); return
                for u in db["users"]:
                    if secrets.compare_digest(pin_hash(pin, u["salt"]), u["hash"]):
                        self._json(400, {"error": "that PIN is already used by someone else"}); return
                db["users"] = [u for u in db["users"] if u["name"].lower() != who.lower()]
                salt = secrets.token_hex(8)
                db["users"].append({"name": who, "role": new_role, "salt": salt, "hash": pin_hash(pin, salt)})
            elif action == "remove":
                who = str(body.get("name") or "")
                target = next((u for u in db["users"] if u["name"] == who), None)
                if not target:
                    self._json(404, {"error": "no such person"}); return
                if ROLES[target["role"]] > ROLES[role]:
                    self._json(403, {"error": "you can't remove someone above your own role"}); return
                rest = [u for u in db["users"] if u is not target]
                if rest and not any(u["role"] == "owner" for u in rest):
                    self._json(400, {"error": "keep at least one owner"}); return
                db["users"] = rest
                db["sessions"] = {k: v for k, v in db["sessions"].items() if v.get("name") != who}
            else:
                self._json(400, {"error": "action must be add or remove"}); return
            save_users(db)
        self._json(200, {"users": public_users(db)})

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-ADA-Token")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self, limit=8192):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(min(length, limit)) or b"{}")
        if not isinstance(body, dict):
            raise ValueError
        return body

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            try:
                with open(DASHBOARD_FILE, "r", encoding="utf-8") as fh:
                    page = fh.read()
            except OSError:
                page = STUB
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/api":
            self._send(200, STUB, "text/html; charset=utf-8")
            return
        if path == "/me":
            name, role, locked = self._who()
            self._json(200, {"name": name, "role": role, "label": ROLE_LABEL.get(role or "", ""),
                             "pins": locked, "logged_in": role is not None and (not locked or bool(name))})
            return
        if not self._allowed("GET", path):
            return
        if path == "/users":
            self._json(200, {"users": public_users(load_users())})
            return
        if path == "/setup":
            # what an installer needs to pair the controller
            with LOCK:
                dev = snapshot()["device"]
            self._json(200, {"device_key": DEVICE_KEY, "urls": HUB_URLS,
                             "discovery_port": DISCOVERY_PORT, "device": dev,
                             "loads": list(HOUSE.loads), "users": len(load_users()["users"])})
            return
        if path == "/report.json":
            days = 7
            m = re.search(r"days=(\d+)", query)
            if m:
                days = max(1, min(31, int(m.group(1))))
            self._json(200, STATS.report(days, load_config()))
            return
        if path == "/config":
            self._json(200, public_config(load_config()))
            return
        if path == "/alerts":
            self._json(200, {"alerts": list(ALERTS)})
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
        if route in ("/login", "/logout", "/users"):
            try:
                body = self._body()
            except (ValueError, TypeError):
                self._json(400, {"error": "expected a JSON object"}); return
            if route == "/login":
                self._login(body); return
            # the very first owner can be created while the hub is still open
            if not self._allowed("POST", route):
                return
            getattr(self, "_" + route.strip("/"))(body)
            return
        if route != "/device" and not self._allowed("POST", route):
            return
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
        if route == "/device":
            self._device()
            return
        if route in ("/config", "/notify/test", "/source", "/fuel", "/service", "/delivery"):
            try:
                body = self._body()
            except (ValueError, TypeError):
                self._json(400, {"error": "expected a JSON object"}); return
            getattr(self, "_" + route.strip("/").replace("/", "_"))(body)
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


    def _config(self, body):
        """Alert channels, site name and diesel price. Secrets are write-only."""
        with CONFIG_LOCK:
            cfg = load_config()
            for k, v in body.items():
                if k in CONFIG_DEFAULTS:
                    v = clean_config_value(k, v)
                    if v is not None:
                        cfg[k] = v
            try:
                save_config(cfg)
            except OSError as err:
                self._json(500, {"error": "could not save: %s" % err}); return
            CFG.update(cfg)
            with LOCK:
                HOUSE.fuel = min(HOUSE.fuel, cfg["diesel_cap"])
        self._json(200, public_config(cfg))

    def _notify_test(self, _body):
        cfg = load_config()
        if not (cfg["ntfy_topic"] or (cfg["telegram_token"] and cfg["telegram_chat"])):
            self._json(400, {"error": "no alert channel set up yet"}); return
        a = {"id": 0, "time": time.strftime("%H:%M"), "level": "ok", "key": "TEST",
             "title": "Test alert", "body": "Phone alerts from A.D.A. are working"}
        sent, errors = send_alert(a, cfg)
        self._json(200 if sent else 502, {"sent": sent, "errors": errors})

    def _service(self, body):
        """The generator was serviced: restart its hour countdown."""
        STATS.service_done(str(body.get("note") or ""), STATS.today_key)
        with LOCK:
            with quiet():
                HOUSE.event("GEN SERVICED", str(body.get("note") or "")[:60])
            payload = snapshot()
        self._json(200, payload)

    def _delivery(self, body):
        """A diesel delivery as the vendor tells it. The tank sensor says how
        much actually went in; a gap of 5% (2 L at least) raises an alert."""
        try:
            litres = float(body.get("litres"))
            price = float(body.get("price") or cfg("diesel_price"))
            if not 0 < litres <= 5000 or price < 0:
                raise ValueError
        except (TypeError, ValueError):
            self._json(400, {"error": "expected {litres, price, vendor}"}); return
        with LOCK:
            at = HOUSE.simday * DAY + HOUSE.minute % DAY
            d, settled = STATS.add_delivery(str(body.get("vendor") or ""), litres, price, at,
                                            STATS.today_key, HOUSE.clock)
            with quiet():
                HOUSE.event("DIESEL DELIVERY", "{} {:.0f} L at N{:,.0f}/L".format(
                    d["vendor"] or "vendor", litres, price))
                if settled:
                    HOUSE.delivery_checked(settled)
            payload = snapshot()
        STATS.save()
        payload["delivery"] = d
        self._json(200, payload)

    def _source(self, body):
        """Demo only: change the power source while no controller owns it."""
        src = body.get("source")
        if src not in SOURCES:
            self._json(400, {"error": "source must be one of " + ", ".join(SOURCES)}); return
        with LOCK:
            if device_live():
                self._json(409, {"error": "the controller is reporting the source"}); return
            if src != HOUSE.source:
                with quiet():
                    HOUSE.source = src
                    what, why = SOURCE_EVENTS[src]
                    HOUSE.event(what, why or "set from dashboard")
                    house_sim.run_rules(HOUSE)
            payload = snapshot()
        self._json(200, payload)

    def _fuel(self, body):
        """Demo only: refill or siphon the tank. Refused once a sensor reports."""
        with LOCK:
            if HOUSE.fuel_measured:
                self._json(409, {"error": "a real fuel sensor is reporting"}); return
            try:
                delta = float(body.get("delta", 0))
            except (TypeError, ValueError):
                self._json(400, {"error": "expected {delta: litres}"}); return
            with quiet():
                if HOUSE.source == "gen":
                    HOUSE.fuel_base = HOUSE.fuel   # compare from now, not from gen stop
                HOUSE.set_fuel(HOUSE.fuel + delta)
                check_conditions(HOUSE)
            payload = snapshot()
        self._json(200, payload)

    def _device(self):
        """The controller's once-a-second report. It sends what it sees (the
        power source, its relays); the reply says which loads should be on."""
        if not secrets.compare_digest(self.headers.get("X-Device-Key", ""), DEVICE_KEY):
            self._json(403, {"error": "wrong or missing X-Device-Key"}); return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
            dev_id = str(body["id"])[:40]
            source = body.get("source")
            relays = body.get("relays") or {}
            if source is not None and source not in SOURCES:
                raise ValueError
            if not isinstance(relays, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "expected JSON {id, source, relays}"}); return

        with LOCK:
            was_live = device_live()
            DEVICE.update(id=dev_id, last=time.monotonic(), rssi=body.get("rssi"),
                          uptime=body.get("uptime"), online=True,
                          relays={str(k)[:20]: bool(v) for k, v in relays.items()})
            with quiet():
                if not was_live:
                    HOUSE.event("DEVICE ONLINE", dev_id)
                if source and source != HOUSE.source:
                    if HOUSE.source == "gen":
                        HOUSE.fuel_base = HOUSE.fuel
                    HOUSE.source = source
                    what, why = SOURCE_EVENTS[source]
                    HOUSE.event(what, why or "reported by " + dev_id)
                # optional real readings; anything missing stays simulated
                rd = body.get("readings") or {}
                if isinstance(rd, dict):
                    try:
                        if "fuel_l" in rd:
                            HOUSE.set_fuel(rd["fuel_l"], measured=True)
                        if "battery" in rd:
                            HOUSE.battery = max(0.0, min(100.0, float(rd["battery"])))
                            HOUSE.battery_measured = True
                        if "tank" in rd:
                            HOUSE.tank = max(0.0, min(120.0, float(rd["tank"])))
                        if "voltage" in rd:
                            HOUSE.voltage = max(0.0, float(rd["voltage"]))
                    except (TypeError, ValueError):
                        pass
                house_sim.run_rules(HOUSE)
                check_conditions(HOUSE)
            loads = {name: load["on"] for name, load in HOUSE.loads.items()}
        # cmd repeats loads as "name=1;name=0" so the firmware needs no JSON library
        cmd = ";".join("{}={}".format(n, int(on)) for n, on in loads.items())
        self._json(200, {"ok": True, "loads": loads, "cmd": cmd})


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
<li>POST /device &mdash; the ESP32's report, with header X-Device-Key; see firmware/README.md</li>
<li><a href="/report.json">/report.json</a>?days=7 &mdash; the history the weekly report is built from</li>
<li><a href="/config">/config</a> &mdash; site name, diesel price and whether phone alerts are set up; POST to change</li>
<li>POST /notify/test &mdash; send a test alert to your phone</li>
<li>POST /source, POST /fuel &mdash; demo controls, refused while real hardware reports</li>
</ul>
<p>One real second is one simulated minute, and the day repeats.</p>
"""


# ---------------------------------------------------------------- main

def main():
    port = PORT
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        try:
            port = int(args[0])
        except ValueError:
            sys.exit("usage: python hub.py [port] [--seed-week]")

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    CFG.update(load_config())
    STATS.load()
    if "--seed-week" in sys.argv:
        STATS.seed_week()
        print("  added a sample week of history (the report will say so)")
    threading.Thread(target=simulate, daemon=True).start()
    threading.Thread(target=notifier, daemon=True).start()
    threading.Thread(target=discovery, args=(port,), daemon=True).start()

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
    HUB_URLS.append("http://127.0.0.1:{}".format(port))
    try:
        # the address this machine would use to reach the network; no packet
        # is sent, and it is right more often than gethostbyname(hostname)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        lan = probe.getsockname()[0]
        probe.close()
        if not lan.startswith("127."):
            print("  http://{}:{}   (same wifi)".format(lan, port))
            HUB_URLS.insert(0, "http://{}:{}".format(lan, port))
    except OSError:
        pass
    print("  open the address above on any phone or laptop on the same wifi")
    print("  one real second = one simulated minute")
    cfg = load_config()
    print("  phone alerts: {}".format("on" if cfg["ntfy_topic"] or cfg["telegram_token"] else
                                      "off (set them up in the dashboard: Power > Alerts)"))
    print("  device key: {}   (put this in the ESP32's config)".format(DEVICE_KEY))
    print("  Ctrl-C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        STATS.save()
        server.server_close()


if __name__ == "__main__":
    main()
