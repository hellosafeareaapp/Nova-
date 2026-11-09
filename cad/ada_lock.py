"""
A.D.A. Smart Access hardware (docs/smart-lock/DESIGN.md) as a CadQuery model.

    python ada_lock.py        # writes cad/out/lock/

Parts (millimetres; each part's front faces +Z, up is +Y):
    keypad      outdoor keypad + card reader with rain hood, for the gate pillar
    strike      fail-secure electric strike for the pedestrian gate
    node        lock node box: ESP32 board, driver, 12 V 7 Ah battery, charger
    exit        inside exit button
    contact     magnetic door contact (reed switch + magnet)
    deadbolt    smart deadbolt for the front door: outside keypad + inside motor unit

Sizes are typical for the class of part, not a specific brand.
"""
import os
import cadquery as cq

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "lock")
C = {
    "shell": (0.13, 0.14, 0.16, 1), "face": (0.20, 0.21, 0.24, 1), "key": (0.78, 0.79, 0.80, 1),
    "glyph": (0.10, 0.10, 0.11, 1), "led": (0.15, 0.85, 0.45, 1), "yellow": (0.96, 0.72, 0.0, 1),
    "steel": (0.72, 0.73, 0.75, 1), "brass": (0.78, 0.62, 0.30, 1), "box": (0.86, 0.87, 0.88, 1),
    "pcb": (0.05, 0.36, 0.20, 1), "chip": (0.75, 0.76, 0.78, 1), "batt": (0.10, 0.10, 0.11, 1),
    "red": (0.80, 0.12, 0.10, 1), "term": (0.15, 0.55, 0.30, 1), "white": (0.95, 0.95, 0.93, 1),
    "green": (0.15, 0.62, 0.30, 1), "dark": (0.06, 0.06, 0.07, 1), "orange": (0.95, 0.45, 0.10, 1),
}
assy = cq.Assembly(name="ADA-Smart-Access")
parts = {}


PRODUCTS = {}                    # product -> its own assembly, for one STEP per product


def add(name, shape, color, loc):
    assy.add(shape, name=name, color=cq.Color(*C[color]), loc=loc)
    parts[name] = (shape, loc)
    prod = name.split("_")[0]
    if prod not in PRODUCTS:
        PRODUCTS[prod] = cq.Assembly(name="ADA-" + prod)
    PRODUCTS[prod].add(shape, name=name, color=cq.Color(*C[color]), loc=loc)


def text(s, size, depth=0.5):
    try:
        return cq.Workplane("XY").text(s, size, depth, halign="center", valign="center")
    except Exception:
        return None


def at(x, y=0, z=0):
    return cq.Location(cq.Vector(x, y, z))


# ------------------------------------------------------------------ 1 keypad
def keypad(X):
    W, H, D = 82.0, 168.0, 30.0
    body = cq.Workplane("XY").rect(W, H).extrude(D).edges("|Z").fillet(10).edges(">Z").fillet(3)
    add("keypad_body", body, "shell", at(X))
    face = cq.Workplane("XY").rect(W - 10, H - 16).extrude(1.2).edges("|Z").fillet(6)
    add("keypad_face", face, "face", at(X, -4, D))
    # card zone: contactless rings + A.D.A. mark
    for i, r in enumerate((9, 14, 19)):
        arc = cq.Workplane("XY").circle(r).circle(r - 1.2).extrude(0.6)
        add(f"keypad_nfc_ring_{i}", arc, "key", at(X, 48, D + 1.2))
    add("keypad_nfc_dot", cq.Workplane("XY").circle(3).extrude(0.6), "yellow", at(X, 48, D + 1.2))
    led = cq.Workplane("XY").rect(44, 2.4).extrude(0.8).edges("|Z").fillet(1.1)
    add("keypad_led_bar", led, "led", at(X, 22, D + 1.2))
    # 3 x 4 keys with engraved digits
    labels = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]
    for i, lab in enumerate(labels):
        cx, cy = (i % 3 - 1) * 20, 2 - (i // 3) * 17
        k = cq.Workplane("XY").rect(16, 12.5).extrude(2.6).edges("|Z").fillet(3).edges(">Z").fillet(0.8)
        nm = lab if lab.isdigit() else ('star' if lab == '*' else 'hash')
        add(f"keypad_key_{nm}", k, "key", at(X + cx, cy - 20, D + 1.2))
        t = text(lab, 7.5, 0.3)
        if t is not None:
            add(f"keypad_glyph_{nm}", t, "glyph", at(X + cx, cy - 20, D + 1.2 + 2.6))
    mark = text("A.D.A.", 6, 0.5)
    if mark is not None:
        add("keypad_mark", mark, "yellow", at(X, -72, D + 1.2))
    # rain hood
    hood = (cq.Workplane("YZ").polyline([(0, 0), (0, 42), (10, 42), (24, 0)]).close().extrude(W + 8)
            .translate((-(W + 8) / 2, 0, 0)))
    add("keypad_rain_hood", hood, "shell", at(X, H / 2 - 2, 0))
    plate = cq.Workplane("XY").rect(W + 8, H + 30).extrude(3).edges("|Z").fillet(6)
    add("keypad_back_plate", plate, "steel", at(X, 8, -3))


# ------------------------------------------------------------------ 2 strike
def strike(X):
    fp = (cq.Workplane("XY").rect(25, 160).extrude(3).edges("|Z").fillet(4)
          .faces(">Z").workplane().pushPoints([(0, -68), (0, -50), (0, 50), (0, 68)]).cskHole(4.5, 9, 90))
    fp = fp.cut(cq.Workplane("XY").rect(14, 44).extrude(3))
    add("strike_faceplate", fp, "steel", at(X, 0, 30))
    bodyh = cq.Workplane("XY").rect(22, 120).extrude(30).edges("|Z").fillet(3)
    add("strike_body", bodyh, "steel", at(X))
    keeper = cq.Workplane("XY").rect(13, 42).extrude(26).edges(">Z").fillet(2)
    add("strike_keeper", keeper, "brass", at(X, 0, 4))
    for i, col in enumerate(("red", "dark")):
        w = cq.Workplane("XZ").circle(1.4).extrude(60)
        add(f"strike_wire_{i}", w, col, at(X - 4 + i * 8, -60, 8))


# ------------------------------------------------------------------ 3 node box (lid off)
def node(X):
    W, H, D, T = 200.0, 250.0, 110.0, 3.0
    outer = cq.Workplane("XY").rect(W, H).extrude(D).edges("|Z").fillet(8)
    inner = cq.Workplane("XY").workplane(offset=T).rect(W - 2 * T, H - 2 * T).extrude(D).edges("|Z").fillet(5)
    add("node_enclosure", outer.cut(inner), "box", at(X))
    batt = cq.Workplane("XY").rect(151, 65).extrude(94).edges("|Z").fillet(3)
    add("node_battery_12V_7Ah", batt, "batt", at(X, -70, T))
    for i, col in enumerate(("red", "glyph")):
        tm = cq.Workplane("XY").rect(6.5, 8).extrude(8)
        add(f"node_battery_terminal_{i}", tm, col, at(X - 50 + i * 100, -45, T + 94))
    lab = text("12V 7Ah", 9, 0.5)
    if lab is not None:
        add("node_battery_label", lab, "white", at(X, -70, T + 94))
    pcb = cq.Workplane("XY").rect(110, 75).extrude(1.6)
    add("node_pcb", pcb, "pcb", at(X, 55, T + 12))
    for sx in (-1, 1):
        for sy in (-1, 1):
            add(f"node_standoff_{sx}_{sy}", cq.Workplane("XY").circle(3).extrude(12), "chip",
                at(X + sx * 48, 55 + sy * 30, T))
    esp = cq.Workplane("XY").rect(18, 25.5).extrude(3.2)
    add("node_esp32_module", esp, "chip", at(X - 30, 62, T + 13.6))
    ant = cq.Workplane("XY").rect(18, 6).extrude(0.4)
    add("node_esp32_antenna", ant, "glyph", at(X - 30, 78, T + 13.6))
    for i in range(2):
        fet = cq.Workplane("XY").rect(10, 15).extrude(4)
        add(f"node_mosfet_{i}", fet, "glyph", at(X + 8 + i * 16, 62, T + 13.6))
    tb = cq.Workplane("XY").rect(50, 9).extrude(10)
    add("node_terminal_block", tb, "term", at(X + 18, 30, T + 13.6))
    chg = cq.Workplane("XY").rect(55, 35).extrude(22).edges("|Z").fillet(2)
    add("node_charger", chg, "white", at(X - 45, 0, T))
    for i, gx in enumerate((-50, 0, 50)):
        g = (cq.Workplane("XZ").polygon(6, 24).extrude(5)
             .union(cq.Workplane("XZ").workplane(offset=-5).circle(9).extrude(10)))
        add(f"node_gland_{i}", g, "dark", at(X + gx, -H / 2 - 4, D * 0.45))
    lid = (cq.Workplane("XY").rect(W, H).extrude(14).edges("|Z").fillet(8).faces("<Z").shell(-2.5))
    add("node_lid_clear", lid, "box", at(X + W + 30, 0, 0))


# ------------------------------------------------------------------ 4 exit button
def exit_button(X):
    plate = cq.Workplane("XY").rect(86, 86).extrude(4).edges("|Z").fillet(8).edges(">Z").fillet(1.5)
    add("exit_plate", plate, "white", at(X))
    btn = cq.Workplane("XY").rect(52, 52).extrude(6).edges("|Z").fillet(8).edges(">Z").fillet(2)
    add("exit_button", btn, "green", at(X, 0, 4))
    t = text("EXIT", 11, 0.6)
    if t is not None:
        add("exit_label", t, "white", at(X, 0, 10))


# ------------------------------------------------------------------ 5 door contact
def contact(X):
    sw = cq.Workplane("XY").rect(14, 34).extrude(12).edges("|Z").fillet(3)
    mg = cq.Workplane("XY").rect(14, 34).extrude(12).edges("|Z").fillet(3)
    add("contact_switch", sw, "white", at(X - 10))
    add("contact_magnet", mg, "white", at(X + 10))
    add("contact_lead", cq.Workplane("XZ").circle(1.6).extrude(40), "white", at(X - 10, -17, 6))


# ------------------------------------------------------------------ 6 smart deadbolt
def deadbolt(X):
    # outside: slim keypad escutcheon with a key cylinder
    out = cq.Workplane("XY").rect(66, 165).extrude(22).edges("|Z").fillet(12).edges(">Z").fillet(4)
    add("deadbolt_outside_body", out, "shell", at(X - 50))
    for i in range(10):
        cx, cy = (i % 2) * 22 - 11, 50 - (i // 2) * 16
        k = cq.Workplane("XY").circle(6).extrude(1.6)
        add(f"deadbolt_key_{i}", k, "key", at(X - 50 + cx, cy, 22))
    cyl = cq.Workplane("XY").circle(11).extrude(4)
    cyl = cyl.cut(cq.Workplane("XY").rect(2.2, 12).extrude(4).translate((0, -1, 1)))
    add("deadbolt_key_cylinder", cyl, "brass", at(X - 50, -55, 22))
    add("deadbolt_led", cq.Workplane("XY").circle(2).extrude(0.8), "led", at(X - 50, 68, 22))
    # inside: motor unit with thumb-turn and battery cover
    inn = cq.Workplane("XY").rect(72, 170).extrude(34).edges("|Z").fillet(12).edges(">Z").fillet(5)
    add("deadbolt_inside_body", inn, "white", at(X + 50))
    turn = (cq.Workplane("XY").circle(18).extrude(3)
            .union(cq.Workplane("XY").rect(10, 34).extrude(16).edges("|Z").fillet(4).translate((0, 0, 3))))
    add("deadbolt_thumbturn", turn, "steel", at(X + 50, 25, 34))
    cover = cq.Workplane("XY").rect(56, 50).extrude(0.8).edges("|Z").fillet(6)
    add("deadbolt_battery_cover", cover, "key", at(X + 50, -45, 34))
    bolt = cq.Workplane("YZ").circle(11).extrude(25).translate((0, 0, 0))
    add("deadbolt_bolt", bolt, "steel", at(X + 87, 25, 17))


def build():
    os.makedirs(os.path.join(OUT, "stl"), exist_ok=True)
    keypad(-470)
    strike(-360)
    node(-180)
    exit_button(240)
    contact(330)
    deadbolt(470)
    assy.save(os.path.join(OUT, "ada_smart_access_kit.step"))
    names = {"keypad": "gate_keypad_reader", "strike": "electric_strike", "node": "lock_node_box",
             "exit": "exit_button", "contact": "door_contact", "deadbolt": "smart_deadbolt"}
    for prod, a in PRODUCTS.items():
        a.save(os.path.join(OUT, "ada_" + names.get(prod, prod) + ".step"))
    assy.save(os.path.join(OUT, "ada_smart_access_kit.glb"))
    for name, (shape, loc) in parts.items():
        s = (shape.val() if isinstance(shape, cq.Workplane) else shape).moved(loc)
        cq.exporters.export(cq.Workplane().add(s), os.path.join(OUT, "stl", name + ".stl"),
                            tolerance=0.1, angularTolerance=0.15)
    print("parts:", len(parts))


if __name__ == "__main__":
    build()
