# A.D.A. controller panel: 3D model

The panel from drawing **ADA-PNL-01** (`docs/brochure/ADA-Panel-Layout.pdf`) as a real CAD model.

| file | open it in | use |
|---|---|---|
| `out/ada_panel_assembly.step` | SolidWorks, Fusion 360, FreeCAD, Onshape | the whole panel, 62 separate coloured parts |
| `out/ada_mounting_plate.step` | same | the back plate alone, with fixing and DIN-rail holes, for cutting |
| `out/ada_enclosure.step` | same | the enclosure body with the seven gland holes |
| `out/ada_panel.blend` | Blender 4.2+ | lit scene on a wall, three cameras, materials |
| `out/ada_panel.glb` | Blender, web viewers, Windows 3D Viewer | the assembly as a mesh |
| `out/stl/*.stl` | any slicer or mesh tool | one mesh per part |
| `out/renders/*.png` | anything | the renders |

## Opening in SolidWorks
**File → Open**, set the file type to **STEP (*.step; *.stp)**, pick
`ada_panel_assembly.step`. SolidWorks asks whether to import it as an
assembly: choose yes, and every component (rails, contactors, terminals...)
becomes its own part you can move, hide or edit. Units are millimetres.

## What is in it
- IP65 enclosure, 500 × 400 × 160 mm, clear polycarbonate cover hinged on the left (shown open)
- Galvanised mounting plate 456 × 356 mm on four standoffs
- Three 35 mm DIN rails (EN 60715 top-hat profile):
  1. **Power & sensing (230 V):** Q1 2-pole isolator, F1 6 A MCB, PS1 24 V DIN power supply, PM1 PZEM-004T meter, U2/U3 AC detect (NEPA, GEN)
  2. **Control & switching:** A1 A.D.A. controller (ESP32, 4 relays, LEDs, antenna), K1 pump contactor, K2 AC contactor, PM1's CT clamp
  3. **Terminals:** X1 blocks for NEPA in, GEN in, house, pump out, earth, DC, tank sensor, spare
- 40 × 40 slotted wire ducts around every rail
- Seven cable glands in the bottom (4 × M25 power, 3 × M20 signal), four fixing lugs

Component sizes are typical catalogue envelopes, not one brand. Check them
against the parts you buy before cutting the plate.

## Regenerating
```
pip install cadquery
python ada_panel.py                      # STEP, GLB and STL into out/
blender -b -P blender_render.py          # renders + .blend (Blender 4.2)
blender -b -P blender_render.py -- --fast
```
Everything is parametric: change a size at the top of `ada_panel.py` (enclosure,
rail spacing, duct size) and run it again.

# A.D.A. Smart Access hardware (future addition)

`ada_lock.py` models the lock kit from `docs/smart-lock/DESIGN.md`;
`blender_lock.py` renders it. Outputs in `out/lock/`.

| file | what |
|---|---|
| `ada_smart_access_kit.step` | the whole kit, every part separate (SolidWorks: File → Open → STEP, import as assembly) |
| `ada_gate_keypad_reader.step` | outdoor keypad + card reader with rain hood and back plate |
| `ada_electric_strike.step` | fail-secure strike: faceplate, body, keeper |
| `ada_lock_node_box.step` | node enclosure with ESP32 board, MOSFET driver, 12 V 7 Ah battery, charger, glands, clear lid |
| `ada_exit_button.step` | inside exit button |
| `ada_door_contact.step` | magnetic door contact (reed switch + magnet) |
| `ada_smart_deadbolt.step` | front-door deadbolt: outside keypad + key cylinder, inside motor unit with thumb-turn |
| `ada_smart_access.blend`, `renders/*.png` | studio kit shot, keypad close-up, keypad installed on a gate pillar |

```
python ada_lock.py
blender -b -P blender_lock.py
```

# A.D.A. house model

`blender_house.py` builds a Nigerian duplex in its compound, cut open at the
front, with A.D.A. in place: the panel from `ada_panel.py` on the inverter-room
wall, inverter and batteries, NEPA pole and line to the meter, generator house,
borehole, overhead tank on a steel stand, solar on the kept roof, fence, sliding
gate and the pedestrian gate with the Smart Access keypad. Glowing lines show
what is feeding the house (NEPA yellow, generator amber, inverter blue, water
cyan); idle ones turn grey, and the house wiring takes the colour of the
active source.

| render | |
|---|---|
| `out/house/renders/ada_house_day_nepa.png` | day: NEPA on, pump filling the tank |
| `out/house/renders/ada_house_night_inverter.png` | night: NEPA and generator off, house on inverter, essential lights only |
| `out/house/renders/ada_house_inverter_room.png` | close-up: A.D.A. panel, inverter and batteries |
| `out/house/ada_house.blend` | the scene, to open in Blender 4.2+ |

```
python ada_panel.py            # the panel it places on the wall
blender -b -P blender_house.py
```
