"""
Turn the CAD model into Blender renders.

    blender -b -P blender_render.py               # renders cad/out/renders/*.png, saves cad/out/ada_panel.blend
    blender -b -P blender_render.py -- --fast     # quick low-sample preview

Run ada_panel.py first: this imports out/ada_panel.glb.
"""
import math, os, sys
import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
FAST = "--fast" in sys.argv
os.makedirs(os.path.join(OUT, "renders"), exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# ---------------------------------------------------------------- import
bpy.ops.import_scene.gltf(filepath=os.path.join(OUT, "ada_panel.glb"))
objs = [o for o in scene.objects if o.type == "MESH"]
root = bpy.data.objects.new("ADA_panel", None)
scene.collection.objects.link(root)
for o in scene.objects:
    if o.parent is None and o is not root:
        o.parent = root
# CadQuery works in mm with Z out of the wall; stand it on a wall in metres
root.scale = (0.001, 0.001, 0.001)
root.rotation_euler = (math.radians(90), 0, 0)
root.location = (0, 0, 1.45)              # panel centre 1.45 m above the floor
bpy.context.view_layer.update()

# ---------------------------------------------------------------- materials
def mat(name, color, metallic=0.0, rough=0.5, transmission=0.0, alpha=1.0, coat=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    b.inputs["Transmission Weight"].default_value = transmission
    b.inputs["Alpha"].default_value = alpha
    b.inputs["Coat Weight"].default_value = coat
    if alpha < 1 or transmission > 0:
        m.blend_method = "BLEND" if hasattr(m, "blend_method") else None
    return m

M = {
    "enclosure": mat("Enclosure ABS", (0.80, 0.81, 0.82), rough=0.42, coat=0.2),
    "cover": mat("Polycarbonate cover", (0.92, 0.96, 1.0), rough=0.03, transmission=1.0),
    "metal": mat("Galvanised steel", (0.72, 0.73, 0.75), metallic=1.0, rough=0.32),
    "duct": mat("Wire duct PVC", (0.42, 0.44, 0.47), rough=0.55),
    "device": mat("Device plastic", (0.93, 0.93, 0.91), rough=0.45),
    "dark": mat("Dark plastic", (0.06, 0.065, 0.075), rough=0.35, coat=0.3),
    "red": mat("Red handle", (0.75, 0.07, 0.05), rough=0.35),
    "blue": mat("Blue terminal", (0.10, 0.27, 0.70), rough=0.4),
    "grey": mat("Grey terminal", (0.50, 0.51, 0.53), rough=0.4),
    "earth": mat("Earth terminal", (0.25, 0.60, 0.20), rough=0.4),
    "yellow": mat("Warning label", (0.96, 0.70, 0.0), rough=0.5),
    "gland": mat("Gland nylon", (0.05, 0.05, 0.055), rough=0.5),
    "led_g": mat("LED green", (0.1, 1.0, 0.25)), "led_y": mat("LED amber", (1.0, 0.65, 0.0)),
    "led_r": mat("LED red", (1.0, 0.1, 0.05)),
}
for k in ("led_g", "led_y", "led_r"):
    b = M[k].node_tree.nodes["Principled BSDF"]
    b.inputs["Emission Color"].default_value = b.inputs["Base Color"].default_value
    b.inputs["Emission Strength"].default_value = 6.0

def pick(name):
    n = name.lower()
    if "cover" in n: return M["cover"]
    if "warning" in n: return M["yellow"]
    if n.startswith("enclosure"): return M["enclosure"]
    if "plate" in n or "din_rail" in n: return M["metal"]
    if "duct" in n: return M["duct"]
    if "gland" in n: return M["gland"]
    if "handle" in n: return M["red"]
    if "led" in n:
        return M["led_r"] if n.endswith("_3") else M["led_y"] if n.endswith("_2") else M["led_g"]
    if any(s in n for s in ("pzem", "a1_ada", "antenna", "toggle", "ct_clamp")): return M["dark"]
    if n.startswith("x1_"):
        if "earth" in n: return M["earth"]
        if "endstop" in n or "tank" in n or "spare" in n: return M["grey"]
        if n.endswith("_2") or "dc_2" in n: return M["blue"]
        if "dc_1" in n: return M["red"]
        return M["grey"]
    return M["device"]

for o in objs:
    o.data.materials.clear()
    o.data.materials.append(pick(o.name))
    for p in o.data.polygons:
        p.use_smooth = False

# ---------------------------------------------------------------- room
bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0.002, 1.5), rotation=(math.radians(90), 0, 0))
wall = bpy.context.object; wall.name = "Wall"
wall.data.materials.append(mat("Painted wall", (0.42, 0.38, 0.34), rough=0.9))
bpy.ops.mesh.primitive_plane_add(size=6, location=(0, -2.5, 0))
floor = bpy.context.object; floor.name = "Floor"
floor.data.materials.append(mat("Floor tiles", (0.30, 0.28, 0.26), rough=0.6))

# conduit pipes coming up into the glands from below
for i, gx in enumerate([-150, -100, -50, 0, 50, 100, 150]):
    r = 0.0125 if i < 4 else 0.010
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=1.2, location=(gx / 1000, -0.072, 1.45 - 0.25 - 0.6 - 0.018))
    c = bpy.context.object; c.name = f"Conduit_{i+1}"
    c.data.materials.append(mat(f"Conduit {i}", (0.15, 0.15, 0.16) if i < 4 else (0.85, 0.85, 0.84), rough=0.5))

# ---------------------------------------------------------------- light & world
world = bpy.data.worlds.new("World"); scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.92, 0.93, 0.95, 1); bg.inputs["Strength"].default_value = 0.12

def area(name, loc, energy, size, color=(1, 1, 1)):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.object; l.name = name
    l.data.energy = energy; l.data.size = size; l.data.color = color
    l.rotation_euler = (Vector((0, 0, 1.45)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return l
area("Key", (1.6, -2.2, 2.6), 160, 1.6, (1.0, 0.96, 0.9))
area("Fill", (-2.0, -1.6, 1.6), 45, 2.0, (0.9, 0.94, 1.0))
area("Top", (0.0, -0.6, 3.2), 35, 1.2)

# ---------------------------------------------------------------- render settings
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 24 if FAST else 128
scene.cycles.use_denoising = True
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Punchy"
scene.render.resolution_x, scene.render.resolution_y = (960, 720) if FAST else (1600, 1200)
scene.render.film_transparent = False

def shot(name, loc, target, lens=50):
    cam_data = bpy.data.cameras.new(name)
    cam_data.lens = lens
    cam = bpy.data.objects.new(name, cam_data); scene.collection.objects.link(cam)
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    scene.render.filepath = os.path.join(OUT, "renders", name + ".png")
    bpy.ops.render.render(write_still=True)
    print("rendered", name)

shot("ada_panel_hero", (0.95, -1.55, 1.75), (-0.02, 0, 1.43), lens=45)
shot("ada_panel_front", (0.0, -1.75, 1.45), (0.0, 0, 1.45), lens=40)
shot("ada_panel_controller_closeup", (0.22, -0.55, 1.55), (-0.06, 0, 1.45), lens=50)

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "ada_panel.blend"))
print("saved blend")
