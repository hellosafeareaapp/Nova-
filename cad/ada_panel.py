"""
A.D.A. controller panel, drawing ADA-PNL-01 rev A, as a parametric CadQuery model.

    pip install cadquery
    python ada_panel.py          # writes the files into cad/out/

Outputs (open in SolidWorks, Fusion 360, FreeCAD, Onshape...):
    out/ada_panel_assembly.step   whole panel, every part separate and coloured
    out/ada_mounting_plate.step   the back plate on its own, with fixing holes (for cutting)
    out/ada_enclosure.step        the enclosure body with gland holes
    out/ada_panel.glb             same assembly for Blender / web viewers
    out/stl/*.stl                 one mesh per part

Units are millimetres. The origin is the centre of the enclosure's back wall;
X to the right, Y up, Z out of the wall towards you. Component sizes are
typical catalogue envelopes, not a specific brand: check them against the
parts you actually buy before cutting anything.
"""
import os
import cadquery as cq

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# ------------------------------------------------------------------ sizes
W, H, D = 400.0, 500.0, 160.0      # enclosure outside
WALL = 3.0
R_EDGE = 10.0
COVER_D = 22.0                     # clear cover depth
PLATE_W, PLATE_H, PLATE_T = 356.0, 456.0, 2.5
PLATE_Z = 12.0                     # standoff from the back wall
RAIL_L = 270.0
RAIL_Y = (140.0, 5.0, -140.0)      # three DIN rails, top to bottom
DUCT = 40.0                        # 40 x 40 slotted wire duct
MOD = 18.0                         # one DIN module width

C = {  # colours (r, g, b, a)
    "enclosure": (0.86, 0.87, 0.88, 1), "cover": (0.75, 0.86, 0.95, 0.25),
    "plate": (0.70, 0.72, 0.74, 1), "rail": (0.80, 0.81, 0.82, 1),
    "duct": (0.55, 0.57, 0.60, 1), "device": (0.95, 0.95, 0.93, 1),
    "dark": (0.16, 0.17, 0.19, 1), "toggle": (0.10, 0.10, 0.10, 1),
    "red": (0.80, 0.15, 0.12, 1), "blue": (0.15, 0.35, 0.75, 1),
    "grey": (0.55, 0.56, 0.58, 1), "earth": (0.30, 0.65, 0.25, 1),
    "yellow": (0.96, 0.72, 0.0, 1), "pcb": (0.05, 0.35, 0.20, 1),
    "copper": (0.80, 0.50, 0.25, 1), "gland": (0.12, 0.12, 0.13, 1),
}

assy = cq.Assembly(name="ADA-PNL-01")
parts = {}                          # name -> shape, for STL export


def add(name, shape, color, loc=None):
    loc = loc or cq.Location()
    assy.add(shape, name=name, color=cq.Color(*C[color]), loc=loc)
    parts[name] = (shape, loc)


# ------------------------------------------------------------------ enclosure
def enclosure():
    outer = (cq.Workplane("XY").rect(W, H).extrude(D)
             .edges("|Z").fillet(R_EDGE))
    inner = (cq.Workplane("XY").workplane(offset=WALL).rect(W - 2 * WALL, H - 2 * WALL)
             .extrude(D).edges("|Z").fillet(R_EDGE - WALL))
    body = outer.cut(inner)
    # seven gland holes in the bottom wall, as on the drawing
    xs = [-150, -100, -50, 0, 50, 100, 150]
    for i, x in enumerate(xs):
        dia = 25.0 if i < 4 else 20.0          # M25 for power, M20 for signals
        hole = (cq.Workplane("XZ", origin=(0, -H / 2 + WALL + 1, 0))
                .center(x, D * 0.45).circle(dia / 2).extrude(WALL + 4))
        body = body.cut(hole)
    # four fixing lugs on the back
    for sx in (-1, 1):
        for sy in (-1, 1):
            lug = (cq.Workplane("XY").center(sx * (W / 2 + 12), sy * (H / 2 - 40))
                   .rect(24, 30).extrude(4).edges("|Z").fillet(4)
                   .faces(">Z").workplane().hole(7))
            body = body.union(lug)
    # internal standoffs for the mounting plate
    for sx in (-1, 1):
        for sy in (-1, 1):
            boss = (cq.Workplane("XY").workplane(offset=WALL)
                    .center(sx * (PLATE_W / 2 - 14), sy * (PLATE_H / 2 - 14))
                    .circle(7).extrude(PLATE_Z - WALL).faces(">Z").workplane().hole(4))
            body = body.union(boss)
    return body


def cover():
    """Clear polycarbonate cover, hinged on the left, shown open at 105 degrees."""
    c = (cq.Workplane("XY").rect(W, H).extrude(COVER_D).edges("|Z").fillet(R_EDGE)
         .faces("<Z").shell(-3.0))
    # quarter-turn locks on the right edge
    for y in (-150, 150):
        c = c.union(cq.Workplane("XY", origin=(W / 2 - 18, y, COVER_D))
                    .circle(9).extrude(4))
    # hinge knuckles on the left edge
    for y in (-170, 0, 170):
        c = c.union(cq.Workplane("XZ", origin=(-W / 2, y + 20, COVER_D / 2))
                    .circle(6).extrude(40))
    # open it: rotate about the hinge line
    hinge = cq.Vector(-W / 2, 0, D)
    return c, cq.Location(cq.Vector(0, 0, D)) , hinge


# ------------------------------------------------------------------ plate, rails, ducts
def mounting_plate():
    p = cq.Workplane("XY").rect(PLATE_W, PLATE_H).extrude(PLATE_T)
    pts = [(sx * (PLATE_W / 2 - 14), sy * (PLATE_H / 2 - 14)) for sx in (-1, 1) for sy in (-1, 1)]
    p = p.faces(">Z").workplane().pushPoints(pts).hole(5)
    # DIN rail fixing holes (M5, every 100 mm) so the plate can be cut and drilled from this file
    rail_pts = [(x, y) for y in RAIL_Y for x in (-120, -40, 40, 120)]
    p = p.faces(">Z").workplane().pushPoints(rail_pts).hole(5.5)
    return p


def din_rail(length=RAIL_L):
    """35 x 7.5 mm top-hat rail (EN 60715), drawn as a profile and extruded."""
    t = 1.0
    prof = (cq.Workplane("YZ").polyline([
        (-17.5, 0), (-17.5, t), (-12.5, t), (-12.5, 7.5), (12.5, 7.5), (12.5, t),
        (17.5, t), (17.5, 0), (12.5 - t, 0), (12.5 - t, 7.5 - t), (-12.5 + t, 7.5 - t),
        (-12.5 + t, 0)]).close().extrude(length))
    prof = prof.translate((-length / 2, 0, 0))
    # long slots
    for x in range(-int(length / 2) + 20, int(length / 2) - 10, 25):
        prof = prof.cut(cq.Workplane("XY").center(x, 0).slot2D(15, 5.2).extrude(10))
    return prof


def duct(length, horizontal=True):
    d = (cq.Workplane("XY").rect(length if horizontal else DUCT, DUCT if horizontal else length)
         .extrude(DUCT).faces(">Z").shell(-1.8))
    # finger slots on both long sides
    n = int(length // 12)
    for i in range(n):
        off = -length / 2 + 6 + i * 12
        if horizontal:
            for sy in (-1, 1):
                d = d.cut(cq.Workplane("XY").center(off, sy * DUCT / 2).rect(5, 4).extrude(DUCT - 6).translate((0, 0, 6)))
        else:
            for sx in (-1, 1):
                d = d.cut(cq.Workplane("XY").center(sx * DUCT / 2, off).rect(4, 5).extrude(DUCT - 6).translate((0, 0, 6)))
    lid = (cq.Workplane("XY").rect((length if horizontal else DUCT) + 2, (DUCT if horizontal else length) + 2)
           .extrude(2).translate((0, 0, DUCT)))
    return d, lid


# ------------------------------------------------------------------ DIN devices
def din_device(modules, height=88.0, depth=62.0, label=None, step=True):
    """Generic DIN-rail device: stepped body like an MCB, with an optional label."""
    w = modules * MOD - 0.6
    base = cq.Workplane("XY").rect(w, height).extrude(depth * 0.62)
    if step:
        front = cq.Workplane("XY").rect(w, height * 0.5).extrude(depth).edges(">Z").fillet(1.2)
        body = base.union(front)
    else:
        body = cq.Workplane("XY").rect(w, height).extrude(depth).edges(">Z").fillet(1.5)
    if label:
        try:
            txt = (cq.Workplane("XY").workplane(offset=depth).text(label, 5.5, 0.4, halign="center", valign="center")
                   .translate((0, -height * 0.18 if step else -height * 0.32, 0)))
            body = body.union(txt)
        except Exception:
            pass                    # no font on this machine: skip the lettering
    return body


def toggle(modules, depth):
    w = modules * MOD - 6
    return cq.Workplane("XY").rect(w, 9).extrude(10).translate((0, 6, depth)).edges(">Z").fillet(1.5)


def contactor():
    body = (cq.Workplane("XY").rect(45, 82).extrude(52)
            .faces(">Z").workplane().rect(45, 50).extrude(26).edges(">Z").fillet(2))
    return body


def terminal(color):
    t = (cq.Workplane("XY").rect(5.2, 48).extrude(30)
         .faces(">Z").workplane().rect(5.2, 22).extrude(8).edges(">Z").fillet(0.8))
    return t


def gland(dia):
    nut = cq.Workplane("XZ").polygon(6, dia * 1.45).extrude(6)
    dome = cq.Workplane("XZ").workplane(offset=-6).circle(dia * 0.62).extrude(14).faces("<Y").fillet(3)
    thread = cq.Workplane("XZ").workplane(offset=6).circle(dia / 2 - 0.3).extrude(10)
    return nut.union(dome).union(thread)


def build():
    os.makedirs(os.path.join(OUT, "stl"), exist_ok=True)
    enc = enclosure()
    add("enclosure", enc, "enclosure")

    c, _, hinge = cover()
    cover_loc = (cq.Location(cq.Vector(hinge.x, hinge.y, hinge.z))
                 * cq.Location(cq.Vector(), cq.Vector(0, 1, 0), -105)
                 * cq.Location(cq.Vector(-hinge.x, -hinge.y, -hinge.z))
                 * cq.Location(cq.Vector(0, 0, D)))
    add("cover_clear", c, "cover", cover_loc)
    warn = (cq.Workplane("XY").polygon(3, 70).extrude(0.6).rotate((0, 0, 0), (0, 0, 1), 90)
            .translate((0, 150, COVER_D + D)))
    add("warning_label", warn, "yellow", cover_loc * cq.Location(cq.Vector(0, 0, -D)))

    plate = mounting_plate()
    add("mounting_plate", plate, "plate", cq.Location(cq.Vector(0, 0, PLATE_Z)))
    z0 = PLATE_Z + PLATE_T                    # plate front face

    for i, y in enumerate(RAIL_Y):
        add(f"din_rail_{i+1}", din_rail(), "rail", cq.Location(cq.Vector(0, y, z0)))
    zr = z0 + 7.5                             # rail top: devices sit here

    # wire ducts between the rails and down both sides
    for name, y in (("duct_top", 205), ("duct_mid_1", 72), ("duct_mid_2", -68), ("duct_bottom", -205)):
        d, lid = duct(RAIL_L + 2 * DUCT if name in ("duct_top", "duct_bottom") else RAIL_L)
        add(name, d, "duct", cq.Location(cq.Vector(0, y, z0)))
        add(name + "_lid", lid, "duct", cq.Location(cq.Vector(0, y, z0)))
    for sx, name in ((-1, "duct_left"), (1, "duct_right")):
        d, lid = duct(360, horizontal=False)
        vx = sx * (PLATE_W / 2 - DUCT / 2 - 2)        # inside the plate edge, clear of the walls
        add(name, d, "duct", cq.Location(cq.Vector(vx, 0, z0)))
        add(name + "_lid", lid, "duct", cq.Location(cq.Vector(vx, 0, z0)))

    # ---- rail 1: power & sensing (230 V)
    x = -RAIL_L / 2 + 10
    def place(name, shape, mods, color="device", rail=0, extra=None):
        nonlocal x
        w = mods * MOD
        loc = cq.Location(cq.Vector(x + w / 2, RAIL_Y[rail], zr))
        add(name, shape, color, loc)
        if extra:
            for nm, shp, col in extra:
                add(nm, shp, col, loc)
        x += w + 2
    place("Q1_isolator_2P_20A", din_device(2, label="Q1"), 2, extra=[("Q1_handle", toggle(2, 62), "red")])
    place("F1_MCB_6A", din_device(1, label="F1"), 1, extra=[("F1_toggle", toggle(1, 62), "toggle")])
    place("PS1_DIN_PSU_24V", din_device(3, height=90, depth=56, label="24V", step=False), 3)
    place("PM1_PZEM_004T", din_device(4, height=90, depth=58, label="PZEM", step=False), 4, "dark")
    place("U2_AC_detect_NEPA", din_device(1, label="U2"), 1)
    place("U3_AC_detect_GEN", din_device(1, label="U3"), 1)
    ct = cq.Workplane("XY").circle(17).circle(9).extrude(16)
    add("PM1_CT_clamp", ct, "dark", cq.Location(cq.Vector(RAIL_L / 2 - 25, RAIL_Y[1] + 8, zr + 4)))  # on the house live, in the free space of rail 2

    # ---- rail 2: control & switching
    x = -RAIL_L / 2 + 10
    place("A1_ADA_controller_ESP32", din_device(6, height=90, depth=60, label="A.D.A.", step=False), 6, "dark",
          rail=1, extra=[("A1_antenna", cq.Workplane("XY").circle(3.5).extrude(28).translate((48, 38, 60)), "toggle")]
          + [(f"A1_led_{k}", cq.Workplane("XY").circle(1.6).extrude(1.2).translate((-40 + k * 9, 28, 60)), c)
             for k, c in enumerate(["earth", "earth", "yellow", "red"])])
    for name in ("K1_contactor_pump_25A", "K2_contactor_AC_25A"):
        w = 45
        add(name, contactor(), "device", cq.Location(cq.Vector(x + w / 2, RAIL_Y[1], zr)))
        x += w + 4

    # ---- rail 3: terminals (L brown/grey, N blue, E green-yellow), every wire labelled
    x = -RAIL_L / 2 + 14
    groups = [("NEPA_IN", ["grey", "blue"]), ("GEN_IN", ["grey", "blue"]), ("HOUSE", ["grey", "blue"]),
              ("PUMP_OUT", ["grey", "blue"]), ("EARTH", ["earth", "earth"]), ("DC", ["red", "blue"]),
              ("TANK", ["grey", "grey", "grey"]), ("SPARE", ["grey", "grey"])]
    for g, cols in groups:
        for k, col in enumerate(cols):
            add(f"X1_{g}_{k+1}", terminal(col), col, cq.Location(cq.Vector(x, RAIL_Y[2], zr)))
            x += 5.4
        x += 7                                  # end plate gap between groups
    for sx in (-1, 1):
        stop = cq.Workplane("XY").rect(9, 40).extrude(28)
        add(f"X1_endstop_{'L' if sx < 0 else 'R'}", stop, "grey",
            cq.Location(cq.Vector((-RAIL_L / 2 + 6) if sx < 0 else (x + 2), RAIL_Y[2], zr)))

    # ---- cable glands on the bottom wall
    for i, gx in enumerate([-150, -100, -50, 0, 50, 100, 150]):
        dia = 25.0 if i < 4 else 20.0
        add(f"gland_{i+1}_M{int(dia)}", gland(dia), "gland",
            cq.Location(cq.Vector(gx, -H / 2 - 6, D * 0.45)))

    # ------------------------------------------------------------ export
    assy.save(os.path.join(OUT, "ada_panel_assembly.step"))
    cq.exporters.export(plate, os.path.join(OUT, "ada_mounting_plate.step"))
    cq.exporters.export(enc, os.path.join(OUT, "ada_enclosure.step"))
    try:
        assy.save(os.path.join(OUT, "ada_panel.glb"))
    except Exception as err:
        print("glTF export skipped:", err)
    for name, (shape, loc) in parts.items():
        s = shape.val() if isinstance(shape, cq.Workplane) else shape
        s = s.moved(loc)
        cq.exporters.export(cq.Workplane().add(s), os.path.join(OUT, "stl", name + ".stl"),
                            tolerance=0.2, angularTolerance=0.2)
    print("parts:", len(parts))


if __name__ == "__main__":
    build()
