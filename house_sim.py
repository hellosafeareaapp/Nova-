"""
A.D.A. house simulator.

Runs a fake Nigerian house so the automation rules can be written and tested
before any hardware exists. No dependencies, no broker, no network.

    python house_sim.py

Everything below the RULES section is the part that eventually becomes the real
product. Everything above it is throwaway scaffolding that a real ESP32 replaces.
"""

import random

# ---------------------------------------------------------------- the house

LOADS = {
    #  name        watts   on at start
    "freezer":    (200,   True),
    "pump":       (750,   False),
    "tv":         (120,   False),
    "kettle":     (2000,  False),
    "lights":     (150,   True),
    "fans":       (180,   True),
    "ac_bed":     (1100,  True),
    "ac_liv":     (1300,  False),
    "iron":       (1000,  False),
}

# Which loads are allowed to run on inverter, and which must never.
SHEDDABLE = ["kettle", "pump", "tv", "ac_bed", "ac_liv", "iron"]


class House:
    def __init__(self):
        self.minute = 6 * 60          # start at 06:00
        self.source = "grid"          # grid | gen | inverter | none
        self.voltage = 220
        self.tank = 45.0              # percent full
        self.battery = 100.0          # inverter charge, percent
        self.loads = {n: {"watts": w, "on": on} for n, (w, on) in LOADS.items()}
        self.log = []

    # --- readings the hub can actually see -------------------------------

    @property
    def clock(self):
        return "{:02d}:{:02d}".format((self.minute // 60) % 24, self.minute % 60)

    @property
    def hour(self):
        return (self.minute // 60) % 24

    @property
    def draw(self):
        return sum(l["watts"] for l in self.loads.values() if l["on"])

    def is_on(self, name):
        return self.loads[name]["on"]

    # --- things the hub can do -------------------------------------------

    def switch(self, name, on, why=""):
        if self.loads[name]["on"] == on:
            return
        self.loads[name]["on"] = on
        verb = "ON " if on else "OFF"
        self.event("{} {}".format(verb, name), why)

    def event(self, what, why=""):
        line = "{}  {:<18} {}".format(self.clock, what, why)
        self.log.append(line)
        print(line)

    # --- physics ----------------------------------------------------------

    def tick(self):
        self.minute += 1

        # Voltage depends on source, and gen is dirtier than grid.
        if self.source == "grid":
            self.voltage = random.gauss(225, 12)
        elif self.source == "gen":
            self.voltage = random.gauss(235, 25)
        elif self.source == "inverter":
            self.voltage = random.gauss(220, 3)
        else:
            self.voltage = 0

        # Occasional spike. This is what killed the TV and the kettle.
        if self.source in ("grid", "gen") and random.random() < 0.004:
            self.voltage = random.uniform(280, 340)
            self.event("VOLTAGE SPIKE", "{:.0f}V".format(self.voltage))

        # Battery drains on inverter, charges otherwise.
        if self.source == "inverter":
            self.battery -= self.draw / 6000.0
            self.battery = max(0.0, self.battery)
            if self.battery == 0:
                self.source = "none"
                self.event("BATTERY FLAT", "everything off")
        elif self.source in ("grid", "gen"):
            self.battery = min(100.0, self.battery + 0.08)

        # Tank fills while the pump runs, and drains as the house uses water.
        if self.is_on("pump"):
            self.tank = min(120.0, self.tank + 0.55)
            if self.tank >= 100 and self.tank - 0.55 < 100:
                self.event("TANK FULL", "pump still running")
            if self.tank >= 115:
                self.event("TANK OVERFLOWING", "water wasted")
        else:
            self.tank = max(0.0, self.tank - 0.02)


# ---------------------------------------------------------------- RULES
# This is the actual product. Each rule reads the house and decides one thing.
# Keep them small and independent so they stay readable as they multiply.

def rule_shed_on_inverter(h):
    """High draw loads must not run on battery."""
    if h.source != "inverter":
        return
    for name in SHEDDABLE:
        if h.is_on(name):
            h.switch(name, False, "shed: on inverter")


def rule_freezer_at_night(h):
    """
    The freezer is the biggest overnight drain and nobody opens it while
    everyone is asleep. Off at night on inverter only.
    """
    night = h.hour >= 21 or h.hour < 5
    if h.source == "inverter" and night:
        h.switch("freezer", False, "shed: night, on inverter")
    elif h.source != "inverter" and not h.is_on("freezer"):
        h.switch("freezer", True, "restored: mains back")


def rule_pump_stop_when_full(h):
    """Nobody should have to remember to switch the pump off."""
    if h.is_on("pump") and h.tank >= 95:
        h.switch("pump", False, "tank at {:.0f}%".format(h.tank))


def rule_pump_start_when_low(h):
    if not h.is_on("pump") and h.tank <= 20 and h.source in ("grid", "gen"):
        h.switch("pump", True, "tank at {:.0f}%".format(h.tank))


def rule_protect_from_spike(h):
    """Cut sensitive loads when voltage goes out of range."""
    if h.voltage > 260:
        for name in ("tv", "kettle"):
            if h.is_on(name):
                h.switch(name, False, "spike {:.0f}V".format(h.voltage))


def rule_low_battery_warning(h):
    if h.source == "inverter" and 19.5 < h.battery <= 20.5:
        h.event("ALERT", "battery at 20 percent")


RULES = [
    rule_protect_from_spike,
    rule_shed_on_inverter,
    rule_freezer_at_night,
    rule_pump_stop_when_full,
    rule_pump_start_when_low,
    rule_low_battery_warning,
]


def run_rules(h):
    for rule in RULES:
        rule(h)


# ---------------------------------------------------------------- scenario

def scenario_night_outage(h):
    """
    A normal evening. Light goes at 22:40, gen runs for a bit, then the house
    falls back to inverter overnight.
    """
    at = {
        21 * 60 + 30: ("tv", True),
        22 * 60 + 0:  ("kettle", True),
        22 * 60 + 6:  ("kettle", False),
    }
    if h.minute in at:
        name, on = at[h.minute]
        h.switch(name, on, "someone in the house")

    if h.minute == 22 * 60 + 40:
        h.source = "none"
        h.event("NEPA TOOK LIGHT", "")
    if h.minute == 22 * 60 + 45:
        h.source = "gen"
        h.event("GEN STARTED", "")
    if h.minute == 23 * 60 + 30:
        h.source = "inverter"
        h.event("GEN OFF", "on battery now")
    if h.minute == 6 * 60 + 15 + 24 * 60:
        h.source = "grid"
        h.event("LIGHT IS BACK", "")


def main():
    h = House()
    print("time   what               why")
    print("-" * 58)
    for _ in range(60 * 26):          # 26 hours
        h.tick()
        scenario_night_outage(h)
        run_rules(h)
    print("-" * 58)
    print("battery {:.0f}%   tank {:.0f}%   draw {}W".format(
        h.battery, h.tank, h.draw))


if __name__ == "__main__":
    main()
