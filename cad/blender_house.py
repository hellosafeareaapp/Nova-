"""
A Nigerian duplex with A.D.A., cut open, for explaining how it all connects.

    blender -b -P blender_house.py             # out/house/renders/*.png + out/house/ada_house.blend
    blender -b -P blender_house.py -- --fast

Uses the panel model from ada_panel.py (out/ada_panel.glb). Everything else
is built here from simple shapes, in metres, Z up, the house front facing -Y.
"""
import math, os, sys
import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "house")
FAST = "--fast" in sys.argv
os.makedirs(os.path.join(OUT, "renders"), exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ---------------------------------------------------------------- helpers
def mat(name, color, rough=0.6, metallic=0.0, emit=0.0, alpha=1.0, transmission=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Transmission Weight"].default_value = transmission
    b.inputs["Alpha"].default_value = alpha
    if emit:
        b.inputs["Emission Color"].default_value = (*color, 1)
        b.inputs["Emission Strength"].default_value = emit
    return m


def box(name, x0, x1, y0, y1, z0, z1, m):
    bpy.ops.mesh.primitive_cube_add(size=1, location=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    o = bpy.context.object; o.name = name
    o.scale = (abs(x1 - x0), abs(y1 - y0), abs(z1 - z0))
    o.data.materials.append(m)
    return o


def cyl(name, x, y, z0, h, r, m, verts=24):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h, location=(x, y, z0 + h / 2), vertices=verts)
    o = bpy.context.object; o.name = name; o.data.materials.append(m); return o


def tube(name, pts, r, m):
    cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"
    cu.bevel_depth = r; cu.bevel_resolution = 4; cu.use_fill_caps = True
    sp = cu.splines.new("POLY"); sp.points.add(len(pts) - 1)
    for p, c in zip(sp.points, pts):
        p.co = (*c, 1)
    o = bpy.data.objects.new(name, cu); scene.collection.objects.link(o)
    o.data.materials.append(m); return o


def sag(a, b, drop, n=12):
    a, b = Vector(a), Vector(b)
    return [tuple(a.lerp(b, i / n) - Vector((0, 0, drop * 4 * (i / n) * (1 - i / n)))) for i in range(n + 1)]


# ---------------------------------------------------------------- materials
M = dict(
    grass=mat("Grass", (0.20, 0.32, 0.12), 0.9), paving=mat("Interlock paving", (0.45, 0.40, 0.34), 0.8),
    ext=mat("Exterior paint", (0.88, 0.82, 0.70), 0.85), int=mat("Interior paint", (0.93, 0.91, 0.86), 0.9),
    cut=mat("Cut wall", (0.62, 0.56, 0.48), 0.9), slab=mat("Slab", (0.70, 0.68, 0.64), 0.85),
    tile=mat("Floor tile", (0.80, 0.77, 0.72), 0.35), wood=mat("Wood floor", (0.45, 0.30, 0.18), 0.5),
    fence=mat("Fence paint", (0.80, 0.76, 0.66), 0.9), coping=mat("Coping", (0.52, 0.47, 0.40), 0.8),
    gate=mat("Gate steel", (0.04, 0.04, 0.045), 0.45, 0.6), glass=mat("Window glass", (0.10, 0.16, 0.20), 0.05, 0.3),
    sofa=mat("Sofa", (0.30, 0.18, 0.12), 0.8), white=mat("White appliance", (0.94, 0.94, 0.92), 0.35),
    dark=mat("Dark", (0.05, 0.05, 0.06), 0.4), bed=mat("Bedding", (0.85, 0.82, 0.76), 0.9),
    tank=mat("Tank black", (0.04, 0.04, 0.045), 0.5), steel=mat("Steel", (0.55, 0.56, 0.58), 0.4, 1.0),
    gen=mat("Generator green", (0.10, 0.35, 0.20), 0.45), batt=mat("Battery", (0.08, 0.08, 0.09), 0.5),
    pole=mat("Concrete pole", (0.62, 0.60, 0.56), 0.9), solar=mat("Solar cell", (0.04, 0.07, 0.16), 0.15, 0.5),
    leaf=mat("Leaves", (0.13, 0.30, 0.10), 0.8), trunk=mat("Trunk", (0.28, 0.20, 0.12), 0.9),
    pipe=mat("PVC pipe", (0.30, 0.32, 0.35), 0.5),
)
# energy and water flows: emission is switched per scene
F = dict(nepa=mat("Flow NEPA", (1.0, 0.78, 0.10), 0.4), gen=mat("Flow generator", (1.0, 0.45, 0.05), 0.4),
         inv=mat("Flow inverter", (0.15, 0.55, 1.0), 0.4), water=mat("Flow water", (0.10, 0.85, 1.0), 0.4))


F["house"] = mat("Flow house wiring", (1.0, 0.78, 0.10), 0.4)
FLOW_COLOR = {k: tuple(F[k].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value[:3]) for k in F}


def flow(key, on, color=None):
    """An active flow glows in its colour; an idle one is a plain grey cable."""
    b = F[key].node_tree.nodes["Principled BSDF"]
    col = color or FLOW_COLOR[key]
    b.inputs["Base Color"].default_value = (*(col if on else (0.18, 0.18, 0.19)), 1)
    b.inputs["Emission Color"].default_value = (*col, 1)
    b.inputs["Emission Strength"].default_value = 9.0 if on else 0.0


# ---------------------------------------------------------------- ground and fence
box("Grass", -17, 17, -13, 13, -0.05, 0, M["grass"])
box("Land", -120, 120, -120, 120, -0.12, -0.07, mat("Land", (0.42, 0.36, 0.26), 0.95))
# neighbours, so the plot sits in a street
for i, (x0, x1, y0, y1, h) in enumerate(((-34, -19, -6, 10, 6.5), (19, 33, -4, 12, 3.4), (-30, -18, 16, 28, 7.0),
                                         (-8, 8, 16, 30, 6.8), (14, 30, 17, 30, 3.5), (-30, -16, -36, -24, 6.6),
                                         (-10, 6, -38, -25, 3.4), (12, 30, -38, -24, 6.6))):
    box(f"Neighbour {i}", x0, x1, y0, y1, 0, h, mat(f"Neighbour {i}", (0.80 - i * 0.02, 0.74, 0.64 + i * 0.02), 0.9))
    box(f"Neighbour roof {i}", x0 - 0.3, x1 + 0.3, y0 - 0.3, y1 + 0.3, h, h + 0.25, mat(f"Roof {i}", (0.45, 0.22 + i * 0.02, 0.16), 0.8))
box("Driveway", -1, 8, -11, -1, 0, 0.02, M["paving"])
box("Street", -60, 60, -21, -13, -0.06, -0.01, mat("Asphalt", (0.16, 0.16, 0.17), 0.9))
FX0, FX1, FY0, FY1, FH = -15, 15, -11, 11, 2.4
fence = [(FX0, FX1, FY1 - 0.23, FY1), (FX0, FX0 + 0.23, FY0, FY1), (FX1 - 0.23, FX1, FY0, FY1),
         (FX0, 3, FY0, FY0 + 0.23), (9.4, FX1, FY0, FY0 + 0.23)]
for i, (x0, x1, y0, y1) in enumerate(fence):
    box(f"Fence {i}", x0, x1, y0, y1, 0, FH, M["fence"])
    box(f"Fence coping {i}", x0 - 0.04, x1 + 0.04, y0 - 0.04, y1 + 0.04, FH, FH + 0.06, M["coping"])
for i, x in enumerate((2.6, 7.4, 9.4)):          # gate pillars
    box(f"Gate pillar {i}", x - 0.22, x + 0.22, FY0 - 0.1, FY0 + 0.33, 0, 2.7, M["fence"])
# sliding vehicle gate (closed) and pedestrian gate (closed)
for i in range(24):
    box(f"Vehicle gate bar {i}", 2.85 + i * 0.19, 2.88 + i * 0.19, FY0 + 0.05, FY0 + 0.1, 0.1, 2.3, M["gate"])
box("Vehicle gate rail top", 2.82, 7.18, FY0 + 0.04, FY0 + 0.11, 2.25, 2.33, M["gate"])
box("Vehicle gate rail bottom", 2.82, 7.18, FY0 + 0.04, FY0 + 0.11, 0.08, 0.16, M["gate"])
for i in range(9):
    box(f"Ped gate bar {i}", 7.68 + i * 0.18, 7.71 + i * 0.18, FY0 + 0.05, FY0 + 0.1, 0.1, 2.2, M["gate"])
box("Ped gate rail", 7.62, 9.18, FY0 + 0.04, FY0 + 0.11, 2.15, 2.22, M["gate"])
# Smart Access keypad on the pedestrian-gate pillar (street side)
box("Smart lock keypad", 9.32, 9.48, FY0 - 0.13, FY0 - 0.1, 1.2, 1.37, M["dark"])
box("Smart lock keypad light", 9.35, 9.45, FY0 - 0.135, FY0 - 0.13, 1.33, 1.335, F["water"])

# ---------------------------------------------------------------- the house (cut open at the front)
HX0, HX1, HY0, HY1 = -9.0, 4.0, -1.0, 8.0
G, S, U, T = 3.2, 0.2, 3.2, 0.2                 # ground height, slab, upper height, wall thickness
Z1 = G + S                                       # upper floor level
ZT = Z1 + U                                      # top of upper walls
box("Ground floor", HX0, HX1, HY0, HY1, 0, 0.06, M["tile"])
box("First floor slab", HX0, HX1, HY0, HY1, G, Z1, M["slab"])
box("Upper floor finish", -5, HX1, HY0, HY1, Z1, Z1 + 0.02, M["wood"])
box("Back wall", HX0, HX1, HY1 - T, HY1, 0, ZT, M["ext"])
box("Left wall", HX0, HX0 + T, HY0, HY1, 0, ZT, M["ext"])
box("Right wall", HX1 - T, HX1, HY0, HY1, 0, ZT, M["ext"])
box("Front wall (kept part)", HX0, -5, HY0, HY0 + T, 0, ZT, M["ext"])
box("Roof slab (kept part)", HX0 - 0.3, -5, HY0 - 0.3, HY1 + 0.3, ZT, ZT + 0.2, M["slab"])
box("Parapet front", HX0 - 0.3, -5, HY0 - 0.3, HY0 - 0.1, ZT + 0.2, ZT + 0.9, M["ext"])
box("Parapet left", HX0 - 0.3, HX0 - 0.1, HY0 - 0.3, HY1 + 0.3, ZT + 0.2, ZT + 0.9, M["ext"])
box("Parapet back", HX0 - 0.3, -5, HY1 + 0.1, HY1 + 0.3, ZT + 0.2, ZT + 0.9, M["ext"])
# windows on the kept facade and the right wall, and the front door
for z in (1.0, Z1 + 1.0):
    box("Window front", -8.2, -6.0, HY0 - 0.02, HY0, z, z + 1.4, M["glass"])
    for y in (1.0, 5.0):
        box("Window right", HX1, HX1 + 0.02, y, y + 1.6, z, z + 1.4, M["glass"])
box("Front door", -5.9, -5.1, HY0 - 0.03, HY0, 0, 2.3, M["wood"])
# cut edges in a darker tone so the section reads clearly
box("Cut cap ground", -5.1, -4.9, HY0, HY1, 0, ZT, M["cut"])
box("Cut cap slab", -4.9, HX1, HY0 - 0.01, HY0 + 0.01, G, Z1, M["cut"])
# interior walls
box("Wall living|kitchen", 0.9, 1.1, HY0, HY1, 0, G, M["int"])
box("Wall inverter|kitchen", 1.1, HX1 - T, 2.9, 3.1, 0, G, M["int"])
box("Wall bed|bath", -0.1, 0.1, HY0, HY1, Z1, ZT, M["int"])
box("Wall bath|bed2", 0.1, HX1 - T, 3.9, 4.1, Z1, ZT, M["int"])

# living room: sofa, TV, AC
box("Sofa seat", -4.2, -1.2, 4.6, 5.6, 0.06, 0.5, M["sofa"])
box("Sofa back", -4.2, -1.2, 5.5, 5.9, 0.06, 1.0, M["sofa"])
box("Rug", -4.0, -1.4, 1.5, 4.3, 0.06, 0.07, mat("Rug", (0.55, 0.20, 0.12), 0.95))
box("TV", -3.4, -1.9, HY1 - T - 0.05, HY1 - T, 1.3, 2.15, M["dark"])  # on the back wall, behind sofa? moved below
bpy.data.objects["TV"].location = (-2.6, 0.35, 1.5); bpy.data.objects["TV"].scale = (1.6, 0.06, 0.9)
box("TV stand", -3.5, -1.7, 0.2, 0.6, 0.06, 0.6, M["wood"])
box("AC living", -4.6, -3.6, HY1 - T - 0.25, HY1 - T, 2.4, 2.7, M["white"])
# kitchen: freezer and counter
box("Freezer", 2.2, 3.6, 6.6, 7.6, 0.06, 0.95, M["white"])
box("Counter", 1.2, 1.8, 3.4, 7.6, 0.06, 0.95, M["wood"])
# inverter room: inverter, batteries, and the A.D.A. panel
box("Inverter", 1.25, 1.75, 2.72, 2.9, 1.1, 1.8, M["white"])
box("Inverter screen", 1.38, 1.62, 2.715, 2.72, 1.55, 1.7, F["inv"])
for i in range(4):
    box(f"Battery {i}", 1.3 + i * 0.6, 1.8 + i * 0.6, 2.35, 2.85, 0.06, 0.36, M["batt"])
# upstairs: beds and AC
box("Bed", -4.0, -1.6, 4.0, 7.6, Z1, Z1 + 0.55, M["bed"])
box("Headboard", -4.1, -1.5, 7.6, 7.75, Z1, Z1 + 1.2, M["wood"])
box("AC bedroom", -3.5, -2.5, HY1 - T - 0.25, HY1 - T, Z1 + 2.3, Z1 + 2.6, M["white"])
box("Bed 2", 1.3, 3.5, 0.0, 2.2, Z1, Z1 + 0.5, M["bed"])
box("Bath tiles", 0.1, HX1 - T, 4.1, HY1 - T, Z1, Z1 + 0.03, mat("Bath tile", (0.75, 0.82, 0.86), 0.2))
# solar panels on the kept roof
for i in range(3):
    for j in range(2):
        o = box(f"Solar {i}{j}", -8.6 + i * 1.15, -7.55 + i * 1.15, 0.5 + j * 2.1, 2.4 + j * 2.1, ZT + 0.5, ZT + 0.55, M["solar"])
        o.rotation_euler = (math.radians(-12), 0, 0)
# meter box on the right wall
box("Meter box", HX1, HX1 + 0.12, 0.3, 0.7, 2.6, 3.1, M["white"])

# ---------------------------------------------------------------- compound: generator, borehole, overhead tank, NEPA pole
box("Gen house back", 9, 13, 7.8, 8.0, 0, 2.4, M["fence"])
box("Gen house left", 9, 9.2, 4, 8, 0, 2.4, M["fence"])
box("Gen house right", 12.8, 13, 4, 8, 0, 2.4, M["fence"])
box("Gen house roof", 8.8, 13.2, 3.8, 8.2, 2.4, 2.55, M["slab"])
box("Generator", 10.0, 12.2, 5.6, 6.8, 0, 1.3, M["gen"])
box("Generator panel", 10.2, 10.8, 5.58, 5.6, 0.7, 1.1, M["dark"])
cyl("Generator exhaust", 12.0, 6.6, 1.3, 1.4, 0.06, M["steel"])
cyl("Borehole casing", -12.5, -4.0, 0, 0.8, 0.09, M["steel"])
box("Pump control box", FX0 + 0.23, FX0 + 0.38, -4.6, -4.2, 1.0, 1.4, M["white"])
for sx in (-0.9, 0.9):
    for sy in (-0.9, 0.9):
        box("Tank stand leg", -12 + sx - 0.05, -12 + sx + 0.05, 2 + sy - 0.05, 2 + sy + 0.05, 0, 4.0, M["steel"])
box("Tank stand deck", -13.1, -10.9, 0.9, 3.1, 4.0, 4.15, M["steel"])
cyl("Overhead tank", -12, 2, 4.15, 2.0, 0.95, M["tank"], 40)
cyl("Tank lid", -12, 2, 6.15, 0.12, 0.3, M["tank"])
cyl("NEPA pole", 13.5, -14.5, 0, 9.0, 0.14, M["pole"], 16)
box("NEPA crossarm", 12.6, 14.4, -14.56, -14.44, 8.4, 8.5, M["steel"])
# trees
for i, (x, y, s) in enumerate(((-13.5, -8.5, 1.2), (12.5, -7.5, 1.0), (-6.5, 10.0, 0.9))):
    cyl(f"Tree trunk {i}", x, y, 0, 2.2 * s, 0.14 * s, M["trunk"], 10)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.5 * s, location=(x, y, 2.9 * s))
    t = bpy.context.object; t.name = f"Tree crown {i}"; t.data.materials.append(M["leaf"])

# ---------------------------------------------------------------- the A.D.A. panel (the CAD model), in the inverter room
before = set(scene.objects)
bpy.ops.import_scene.gltf(filepath=os.path.join(HERE, "out", "ada_panel.glb"))
pr = bpy.data.objects.new("ADA panel", None); scene.collection.objects.link(pr)
for o in set(scene.objects) - before:
    if o.parent is None and o is not pr:
        o.parent = pr
for o in set(scene.objects) - before:
    if o.type == "MESH":
        n = o.name.lower()
        mm = M["white"] if ("enclosure" in n or n.startswith(("q1", "f1", "ps1", "u2", "u3", "k1", "k2"))) else \
             mat("Cover", (0.9, 0.95, 1.0), 0.05, transmission=1.0) if "cover" in n else \
             F["nepa"] if "warning" in n else M["dark"] if ("a1_" in n or "pm1" in n) else M["steel"]
        o.data.materials.clear(); o.data.materials.append(mm)
pr.scale = (0.001,) * 3
pr.rotation_euler = (math.radians(90), 0, 0)
pr.location = (3.0, 2.88, 1.55)

# ---------------------------------------------------------------- flows: where power and water go
PANEL_IN = (3.0, 2.72, 1.3)
tube("Flow NEPA line", sag((13.5, -14.5, 8.45), (HX1 + 0.12, 0.5, 3.0), 1.2, 16), 0.06, F["nepa"])
tube("Flow NEPA to panel", [(HX1 + 0.06, 0.5, 2.6), (HX1 + 0.06, 2.6, 2.6), (3.6, 2.7, 1.9), (3.1, 2.7, 1.8)], 0.035, F["nepa"])
tube("Flow generator", [(11.1, 5.55, 0.6), (11.1, 3.0, 0.08), (HX1 + 0.1, 2.0, 0.08), (3.7, 2.7, 0.1), (3.3, 2.7, 1.3)], 0.06, F["gen"])
tube("Flow inverter", [(1.75, 2.8, 1.45), (2.75, 2.75, 1.45)], 0.035, F["inv"])
tube("Flow batteries", [(2.0, 2.6, 0.36), (1.6, 2.8, 1.1)], 0.03, F["inv"])
tube("Flow water borehole", [(-12.5, -4.0, 0.8), (-12.5, 0.95, 0.8), (-12.95, 0.95, 0.8), (-12.95, 0.95, 6.0), (-12.5, 1.6, 6.2)], 0.09, F["water"])
tube("Flow water to house", [(-11.05, 2.0, 4.3), (-9.6, 2.0, 4.3), (HX0, 2.0, 4.3)], 0.09, F["water"])
tube("Pump cable", [(FX0 + 0.38, -4.4, 1.0), (-12.6, -4.0, 0.6)], 0.015, M["dark"])
# the house wiring: from the A.D.A. panel along the ceilings to the rooms, coloured by the source feeding it
tube("Flow house ground", [(3.0, 2.7, 1.95), (3.0, 2.7, 3.05), (0.9, 2.7, 3.05), (-4.6, 2.7, 3.05)], 0.05, F["house"])
tube("Flow house upper", [(0.0, 2.7, 3.05), (0.0, 2.7, 3.45), (-0.05, 2.7, ZT - 0.08), (-4.6, 2.7, ZT - 0.08)], 0.05, F["house"])

# ---------------------------------------------------------------- labels that face the camera
cam_data = bpy.data.cameras.new("Cam"); cam_data.lens = 27
cam = bpy.data.objects.new("Cam", cam_data); scene.collection.objects.link(cam)
cam.location = (9.0, -33.0, 22.0)
cam.rotation_euler = (Vector((-0.5, 0.5, 1.0)) - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam
label_bg = mat("Label backing", (0.04, 0.06, 0.10), 0.5, alpha=0.85)
label_txt = mat("Label text", (1, 1, 1), emit=2.5)


def label(text, loc, size=0.55):
    bpy.ops.object.text_add(location=loc)
    t = bpy.context.object; t.name = "Label " + text
    t.data.body = text; t.data.size = size; t.data.align_x = "CENTER"; t.data.align_y = "CENTER"
    t.data.extrude = 0.005; t.data.materials.append(label_txt)
    c = t.constraints.new("TRACK_TO"); c.target = cam; c.track_axis = "TRACK_Z"; c.up_axis = "UP_Y"
    bpy.context.view_layer.update()
    w, h = t.dimensions.x, t.dimensions.y
    bpy.ops.mesh.primitive_plane_add(size=1)
    p = bpy.context.object; p.name = "Label bg " + text
    p.scale = (w + size * 0.9, h + size * 0.7, 1); p.location = (0, 0, -0.02)
    p.parent = t; p.data.materials.append(label_bg)
    return t


label("NEPA", (13.5, -14.5, 9.6))
label("Generator", (11.1, 6.2, 3.4))
label("A.D.A. panel + inverter", (2.0, -1.6, 2.9), 0.4)
label("Overhead tank", (-12, 2, 7.2))
label("Borehole", (-12.5, -4.0, 2.2))
label("Smart lock", (9.4, -11.6, 3.3))
label("Solar", (-7.3, 2.2, ZT + 1.6))

# ---------------------------------------------------------------- light, world, render
world = bpy.data.worlds.new("World"); scene.world = world; world.use_nodes = True
nt = world.node_tree; bg = nt.nodes["Background"]
sky = nt.nodes.new("ShaderNodeTexSky"); sky.sky_type = "NISHITA"
nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
sun_d = bpy.data.lights.new("Sun", "SUN"); sun_d.angle = math.radians(2)
sun = bpy.data.objects.new("Sun", sun_d); scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(48), 0, math.radians(35))
room_lights = []
for name, loc, e in (("Living", (-2.5, 3.5, 2.9), 300), ("Inverter room", (2.5, 1.0, 2.9), 150),
                     ("Bedroom", (-2.5, 4.0, ZT - 0.3), 220), ("Kitchen", (2.5, 5.5, 2.9), 120),
                     ("Security 1", (-14.5, -10.5, 2.6), 600), ("Security 2", (14.5, -10.5, 2.6), 600),
                     ("Security 3", (-14.5, 10.5, 2.6), 500), ("Security 4", (14.5, 10.5, 2.6), 500),
                     ("Gate light", (8.4, -11.4, 2.9), 400)):
    ld = bpy.data.lights.new(name, "POINT"); ld.energy = e; ld.color = (1.0, 0.82, 0.58); ld.shadow_soft_size = 0.3
    lo = bpy.data.objects.new(name, ld); lo.location = loc; scene.collection.objects.link(lo)
    room_lights.append((lo, name))

scene.render.engine = "CYCLES"; scene.cycles.device = "CPU"
scene.cycles.samples = 16 if FAST else 64; scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = (960, 540) if FAST else (1920, 1080)
scene.view_settings.view_transform = "AgX"; scene.view_settings.look = "AgX - Punchy"


def render(name):
    scene.render.filepath = os.path.join(OUT, "renders", name + ".png")
    bpy.ops.render.render(write_still=True); print("rendered", name)


# day: NEPA on, pump running, generator and inverter idle
sky.sun_elevation = math.radians(42); sky.sun_rotation = math.radians(215)
bg.inputs["Strength"].default_value = 0.35; sun_d.energy = 3.0; scene.view_settings.exposure = -0.7
for lo, n in room_lights: lo.hide_render = True
flow("nepa", True); flow("gen", False); flow("inv", False); flow("water", True); flow("house", True, FLOW_COLOR["nepa"])
render("ada_house_day_nepa")

# close-up of the inverter room: the A.D.A. panel, inverter and batteries (day, NEPA)
main_cam = scene.camera
cd2 = bpy.data.cameras.new("Close"); cd2.lens = 32
close = bpy.data.objects.new("Close", cd2); scene.collection.objects.link(close)
close.location = (2.45, -0.4, 1.75)
close.rotation_euler = (Vector((2.55, 2.8, 1.2)) - close.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = close
for lo, n in room_lights: lo.hide_render = True
exp_main = scene.view_settings.exposure; scene.view_settings.exposure = -1.3
hidden = [o for o in scene.objects if o.name.startswith("Label")]
for o in hidden: o.hide_render = True
render("ada_house_inverter_room")
scene.view_settings.exposure = exp_main
for o in hidden: o.hide_render = False
scene.camera = main_cam

# night: NEPA gone, generator off, house on inverter; only essential lights; pump off
sky.sun_elevation = math.radians(-6)
bg.inputs["Strength"].default_value = 0.0; sun_d.energy = 0.25; sun_d.color = (0.55, 0.65, 1.0); scene.view_settings.exposure = 1.3
nt.links.remove(bg.inputs["Color"].links[0]); bg.inputs["Color"].default_value = (0.02, 0.03, 0.06, 1); bg.inputs["Strength"].default_value = 1.0
for lo, n in room_lights:
    lo.hide_render = n in ("Kitchen",)        # kitchen light shed; the rest are essential
flow("nepa", False); flow("gen", False); flow("inv", True); flow("water", False); flow("house", True, FLOW_COLOR["inv"])
render("ada_house_night_inverter")

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "ada_house.blend"))
print("saved blend")
