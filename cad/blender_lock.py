"""
Renders of the A.D.A. Smart Access kit.

    blender -b -P blender_lock.py            # out/lock/renders/*.png + out/lock/ada_smart_access.blend
    blender -b -P blender_lock.py -- --fast

Run ada_lock.py first (it writes out/lock/ada_smart_access_kit.glb).
"""
import math, os, sys
import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "lock")
FAST = "--fast" in sys.argv
os.makedirs(os.path.join(OUT, "renders"), exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

bpy.ops.import_scene.gltf(filepath=os.path.join(OUT, "ada_smart_access_kit.glb"))
# bake the mm / Z-out-of-wall CAD frame into world metres, standing up
root = bpy.data.objects.new("root", None); scene.collection.objects.link(root)
for o in list(scene.objects):
    if o.parent is None and o is not root:
        o.parent = root
root.scale = (0.001,) * 3
root.rotation_euler = (math.radians(90), 0, 0)
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH"]
for o in meshes:
    mw = o.matrix_world.copy(); o.parent = None; o.matrix_world = mw
for o in list(scene.objects):
    if o.type == "EMPTY":
        bpy.data.objects.remove(o)
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def mat(name, color, metallic=0.0, rough=0.5, transmission=0.0, emit=0.0, coat=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    b.inputs["Transmission Weight"].default_value = transmission
    b.inputs["Coat Weight"].default_value = coat
    if emit:
        b.inputs["Emission Color"].default_value = (*color, 1)
        b.inputs["Emission Strength"].default_value = emit
    return m

M = dict(
    shell=mat("Housing", (0.05, 0.055, 0.065), rough=0.38, coat=0.4),
    face=mat("Glass face", (0.02, 0.022, 0.026), rough=0.08, coat=1.0),
    key=mat("Key rubber", (0.62, 0.63, 0.65), rough=0.6),
    steel=mat("Brushed steel", (0.75, 0.76, 0.78), metallic=1.0, rough=0.28),
    brass=mat("Brass", (0.80, 0.60, 0.28), metallic=1.0, rough=0.3),
    led=mat("LED", (0.1, 1.0, 0.4), emit=8), yellow=mat("Accent", (0.96, 0.70, 0.0), rough=0.4, emit=0.4),
    box=mat("Enclosure ABS", (0.84, 0.85, 0.86), rough=0.42),
    clear=mat("Clear lid", (0.92, 0.96, 1.0), rough=0.04, transmission=1.0),
    pcb=mat("PCB", (0.03, 0.30, 0.16), rough=0.35, coat=0.6),
    chip=mat("Shield can", (0.78, 0.79, 0.81), metallic=1.0, rough=0.25),
    batt=mat("Battery", (0.06, 0.06, 0.065), rough=0.5),
    red=mat("Red", (0.75, 0.08, 0.06), rough=0.4), dark=mat("Black plastic", (0.03, 0.03, 0.035), rough=0.5),
    white=mat("White plastic", (0.92, 0.92, 0.90), rough=0.45), green=mat("Exit green", (0.10, 0.55, 0.25), rough=0.35, emit=0.3),
    term=mat("Terminal green", (0.10, 0.48, 0.25), rough=0.4),
)

def pick(n):
    n = n.lower()
    if "lid_clear" in n: return M["clear"]
    if "led" in n: return M["led"]
    if "nfc_dot" in n or n.startswith("keypad_mark"): return M["yellow"]
    if "nfc_ring" in n: return M["key"]
    if n.startswith("keypad_face"): return M["face"]
    if "glyph" in n: return M["dark"]
    if "keypad_key" in n or "deadbolt_key_" in n and "cylinder" not in n: return M["key"]
    if "back_plate" in n or n.startswith("strike_faceplate") or n.startswith("strike_body") or "thumbturn" in n or "deadbolt_bolt" in n or "standoff" in n: return M["steel"]
    if "keeper" in n or "key_cylinder" in n: return M["brass"]
    if "wire_0" in n or "terminal_0" in n: return M["red"]
    if "wire_1" in n or "terminal_1" in n or "gland" in n or "mosfet" in n or "antenna" in n: return M["dark"]
    if "battery_12v" in n: return M["batt"]
    if "battery_label" in n or "exit_label" in n: return M["white"]
    if "pcb" in n: return M["pcb"]
    if "esp32_module" in n: return M["chip"]
    if "terminal_block" in n: return M["term"]
    if "enclosure" in n: return M["box"]
    if "exit_button" in n: return M["green"]
    if n.startswith(("exit_plate", "contact_", "node_charger", "deadbolt_inside")): return M["white"]
    if "battery_cover" in n: return M["key"]
    return M["shell"]

for o in meshes:
    o.data.materials.clear(); o.data.materials.append(pick(o.name))

# group the parts per product so each can be placed on its own
groups = {}
for o in meshes:
    g = o.name.split("_")[0].lower()
    groups.setdefault(g, []).append(o)

def bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    return Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))), \
           Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))

E = {}
for g, objs in groups.items():
    lo, hi = bbox(objs)
    e = bpy.data.objects.new("KIT_" + g, None); scene.collection.objects.link(e)
    e.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)
    bpy.context.view_layer.update()
    for o in objs:
        mw = o.matrix_world.copy(); o.parent = e; o.matrix_world = mw
    E[g] = e
# stand every product on the table (z = 0), a little apart
for g, e in E.items():
    e.location.z = 0.0
E["node"].location.x -= 0.03
bpy.context.view_layer.update()

# ---------------------------------------------------------------- studio
world = bpy.data.worlds.new("World"); scene.world = world; world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.9, 0.9, 0.92, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.06

def add_plane(name, size, loc, rot, m):
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc, rotation=rot)
    o = bpy.context.object; o.name = name; o.data.materials.append(m); return o

studio = []
studio.append(add_plane("Table", 4, (0, -0.6, 0), (0, 0, 0), mat("Studio floor", (0.62, 0.60, 0.57), rough=0.7)))
studio.append(add_plane("Backdrop", 4, (0, 0.35, 1.2), (math.radians(90), 0, 0), mat("Studio backdrop", (0.70, 0.68, 0.65), rough=0.9)))

def area(name, loc, target, energy, size, color=(1, 1, 1)):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.object; l.name = name
    l.data.energy = energy; l.data.size = size; l.data.color = color
    l.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return l
lights = [area("Key", (0.9, -1.3, 1.1), (0, 0, 0.1), 55, 1.2, (1, 0.96, 0.9)),
          area("Fill", (-1.2, -1.0, 0.7), (0, 0, 0.1), 16, 1.5, (0.9, 0.94, 1)),
          area("Rim", (0, 0.2, 1.4), (0, 0, 0.1), 22, 1.0)]

scene.render.engine = "CYCLES"; scene.cycles.device = "CPU"
scene.cycles.samples = 24 if FAST else 96; scene.cycles.use_denoising = True
scene.view_settings.view_transform = "AgX"; scene.view_settings.look = "AgX - Punchy"

def shot(name, loc, target, lens, res=(1600, 900)):
    cd = bpy.data.cameras.new(name); cd.lens = lens
    cam = bpy.data.objects.new(name, cd); scene.collection.objects.link(cam)
    cam.location = loc; cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = (res[0] // 2, res[1] // 2) if FAST else res
    scene.render.filepath = os.path.join(OUT, "renders", name + ".png")
    bpy.ops.render.render(write_still=True); print("rendered", name)

# 1 the whole kit
shot("smart_access_kit", (0.05, -1.55, 0.55), (0.0, 0, 0.11), 50)
# 2 keypad close-up
kp = E["keypad"].location
shot("smart_access_keypad", (kp.x + 0.16, -0.36, 0.20), (kp.x, 0, 0.09), 60, (1200, 1200))

# ---------------------------------------------------------------- 3 installed on a gate pillar
for g, e in E.items():
    if g not in ("keypad", "strike"):
        for o in e.children: o.hide_render = True
for o in studio: o.hide_render = True
for l in lights: l.hide_render = True
GX = 5.0                                        # build the gate away from the kit
def box(name, size, loc, m):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object; o.name = name; o.scale = size; o.data.materials.append(m); return o
paint = mat("Pillar paint", (0.80, 0.74, 0.62), rough=0.85)
wallp = mat("Fence paint", (0.74, 0.70, 0.62), rough=0.9)
gatep = mat("Gate black", (0.04, 0.04, 0.045), metallic=0.6, rough=0.45)
cap = mat("Coping", (0.55, 0.50, 0.44), rough=0.8)
box("Pillar", (0.45, 0.45, 2.6), (GX, 0, 1.3), paint)
box("Pillar cap", (0.55, 0.55, 0.08), (GX, 0, 2.64), cap)
box("Fence wall", (3.0, 0.23, 2.3), (GX - 1.725, 0.05, 1.15), wallp)
box("Fence coping", (3.0, 0.3, 0.06), (GX - 1.725, 0.05, 2.33), cap)
box("Pillar 2", (0.45, 0.45, 2.6), (GX + 1.55, 0, 1.3), paint)
# steel pedestrian gate between the pillars, slightly ajar
gx0 = GX + 0.24
frame = [((0.04, 0.04, 2.1), (0.02, 0, 1.13)), ((0.04, 0.04, 2.1), (1.04, 0, 1.13)),
         ((1.06, 0.04, 0.05), (0.53, 0, 0.1)), ((1.06, 0.04, 0.05), (0.53, 0, 2.16)), ((1.06, 0.04, 0.05), (0.53, 0, 1.15))]
gate = bpy.data.objects.new("Gate", None); scene.collection.objects.link(gate)
gate.location = (gx0 + 1.06, 0.0, 0)
for i, (s, l) in enumerate(frame):
    o = box(f"Gate frame {i}", s, (l[0] - 1.06, l[1], l[2]), gatep); o.parent = gate
for i in range(13):
    o = box(f"Gate bar {i}", (0.018, 0.018, 2.05), (0.08 * i + 0.06 - 1.06, 0, 1.13), gatep); o.parent = gate
gate.rotation_euler = (0, 0, math.radians(-14))   # hinged at the far pillar, opening inward
bpy.ops.mesh.primitive_plane_add(size=12, location=(GX, -1.5, 0))
ground = bpy.context.object; ground.data.materials.append(mat("Interlock", (0.42, 0.38, 0.33), rough=0.8))
# keypad on the pillar's street face; strike on the pillar's gate-side edge
E["keypad"].location = (GX, -0.225 - 0.003, 1.22)
E["keypad"].rotation_euler = (0, 0, 0)
E["strike"].location = (GX + 0.225 + 0.002, 0.02, 0.98)
E["strike"].rotation_euler = (0, 0, math.radians(90))
sun = bpy.data.lights.new("Sun", "SUN"); sun.energy = 2.4; sun.angle = math.radians(3)
so = bpy.data.objects.new("Sun", sun); scene.collection.objects.link(so)
so.rotation_euler = (math.radians(52), 0, math.radians(-38))
sky = world.node_tree.nodes.new("ShaderNodeTexSky"); sky.sky_type = "NISHITA"; sky.sun_elevation = math.radians(38)
world.node_tree.links.new(sky.outputs["Color"], world.node_tree.nodes["Background"].inputs["Color"])
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.12
scene.view_settings.exposure = -0.6
shot("smart_access_gate", (GX - 1.5, -3.3, 1.6), (GX + 0.45, 0, 1.2), 35, (1600, 1000))

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "ada_smart_access.blend"))
print("saved blend")
