"""Blender headless scene builder & renderer for MEBELGEN.

Run inside Blender:
    blender --background --python build_scene.py -- <specs.json> <out_dir> [idx]

Builds a parametric 3D model for every item in specs.json, assigns realistic
materials (matte painted MDF, brass, black plastic, dark plinth), lights it
with a soft studio setup and renders a transparent PNG per item to <out_dir>.

This module is standalone (no refsheets imports): it only needs the JSON.
"""
import bpy
import bmesh
import json
import math
import os
import sys

MM = 0.001  # mm -> meters


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
def _srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_lin(h):
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b), 1.0)


RAL = {
    "8019": "#3D3635",  # grey brown (dark)
    "9011": "#1C1C1C",  # graphite black
    "9005": "#0A0A0A",
    "7016": "#383E42",
    "1015": "#E6D2B5",
}
NCS = {
    "2000-N": "#D4D4D2", "3000-N": "#B7B7B5", "4000-N": "#9C9C9A",
    "2000": "#D4D4D2", "3000": "#B7B7B5", "1000-N": "#E9E9E7",
}
CREAM = "#E9E2D4"     # default warm MDF when colour is "по согласованию"
BRASS = "#C8A13C"
DARK = "#1A1815"


def resolve_color(material):
    sysname = (material.get("color_system") or "").upper()
    code = (material.get("color_code") or "").upper()
    if sysname == "RAL" and code in RAL:
        return RAL[code]
    if sysname == "NCS":
        return NCS.get(code, NCS.get(code.replace("S", "").strip("- "), CREAM))
    return CREAM


# ---------------------------------------------------------------------------
# Material factory
# ---------------------------------------------------------------------------
def mat_paint(name, hexcol, roughness=0.6, bump=True, sheen=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = hex_lin(hexcol)
    bsdf.inputs["Roughness"].default_value = roughness
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.4
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    if bump:
        tex = nt.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = 220.0
        tex.inputs["Detail"].default_value = 2.0
        bmp = nt.nodes.new("ShaderNodeBump")
        bmp.inputs["Strength"].default_value = 0.06
        nt.links.new(tex.outputs["Fac"], bmp.inputs["Height"])
        nt.links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    return m


def mat_metal(name, hexcol, roughness=0.32):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = hex_lin(hexcol)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = roughness
    return m


def mat_plastic(name, hexcol="#141414", roughness=0.5):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = hex_lin(hexcol)
    b.inputs["Roughness"].default_value = roughness
    return m


# ---------------------------------------------------------------------------
# Geometry helpers (units: meters). Center model in x,y; z from 0 up.
# ---------------------------------------------------------------------------
def box(name, size, center, mat=None, bevel=0.004):
    sx, sy, sz = size
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    o = bpy.context.active_object
    o.name = name
    o.scale = (sx, sy, sz)
    bpy.ops.object.transform_apply(scale=True)
    if bevel and min(sx, sy, sz) > bevel * 3:
        md = o.modifiers.new("bevel", "BEVEL")
        md.width = bevel
        md.segments = 3
        md.harden_normals = True
    if mat:
        o.data.materials.append(mat)
    return o


def cylinder(name, radius, depth, center, mat=None, verts=72, bevel=0.004):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius,
                                        depth=depth, location=center)
    o = bpy.context.active_object
    o.name = name
    if bevel:
        md = o.modifiers.new("bevel", "BEVEL")
        md.width = bevel
        md.segments = 2
    if mat:
        o.data.materials.append(mat)
    _shade_smooth_auto(o)
    return o


def rotate_about_z(objs, px, py, angle):
    """Rotate object(s) about a vertical axis at (px, py) by `angle` radians.

    Bakes each object's transform into its mesh first so the pivot is honoured
    in world space (used to swing doors open on a hinge edge)."""
    import mathutils
    if not isinstance(objs, (list, tuple)):
        objs = [objs]
    M = (mathutils.Matrix.Translation((px, py, 0)) @
         mathutils.Matrix.Rotation(angle, 4, "Z") @
         mathutils.Matrix.Translation((-px, -py, 0)))
    for o in objs:
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = o
        o.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        o.data.transform(M)
    bpy.ops.object.select_all(action="DESELECT")


def _shade_smooth_auto(o):
    bpy.context.view_layer.objects.active = o
    try:
        bpy.ops.object.shade_auto_smooth(angle=math.radians(40))
    except Exception:
        try:
            bpy.ops.object.shade_smooth()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Archetype builders. Each returns nothing; objects are added to scene.
# Convention: width along X (centered), depth along Y (centered), height Z 0..h
# Front of furniture faces -Y (toward camera placed at -Y).
# ---------------------------------------------------------------------------
def build_casegood(spec, mats):
    d = spec["dimensions"]
    f = spec.get("features", {})
    W, D, H = d["w"] * MM, d["d"] * MM, d["h"] * MM
    plinth = (f.get("thickness", {}).get("plinth") or (80 if d["h"] >= 1500 else 60)) * MM
    doors = 2 if (d["w"] >= 600 or f.get("symmetric")) else 1
    body = mats["body"]
    t = 0.018  # panel thickness
    z0 = plinth
    Hb = H - plinth                 # carcass height
    iz0, iz1 = z0 + t, z0 + Hb - t  # interior z range
    ix0, ix1 = -W / 2 + t, W / 2 - t
    iw = ix1 - ix0
    idepth = D - t                  # interior depth (open front)

    # plinth
    box("plinth", (W, D, plinth), (0, 0, plinth / 2), mats["dark"], bevel=0.002)
    # open carcass shell (no front panel -> interior visible)
    box("back", (W, t, Hb), (0, D / 2 - t / 2, z0 + Hb / 2), body)
    box("side_l", (t, D, Hb), (-W / 2 + t / 2, 0, z0 + Hb / 2), body)
    box("side_r", (t, D, Hb), (W / 2 - t / 2, 0, z0 + Hb / 2), body)
    box("bottom", (iw, idepth, t), (0, -t / 2, z0 + t / 2), body)
    box("top", (iw, idepth, t), (0, -t / 2, z0 + Hb - t / 2), body)

    # interior fittings
    if f.get("rod"):
        hat_z = iz1 - 0.30
        box("hat", (iw, idepth, t), (0, -t / 2, hat_z), body)
        rod_z = hat_z - 0.10
        rod_y = -D * 0.18
        rod_len = iw * 0.90
        box("rod", (rod_len, 0.032, 0.032), (0, rod_y, rod_z), mats["steel"], bevel=0.006)
        shoe_z = iz0 + 0.33
        box("shoe", (iw, idepth, t), (0, -t / 2, shoe_z), body)
    else:
        n = f.get("shelves") or (4 if d["h"] >= 1500 else 1)
        for i in range(1, n + 1):
            z = iz0 + (iz1 - iz0) * i / (n + 1)
            box(f"shelf{i}", (iw, idepth, t), (0, -t / 2, z), body)

    # doors swung open on outer hinges so the interior is visible
    gap = 0.003
    dw = (W - (doors + 1) * gap) / doors
    dt = 0.018
    door_h = Hb - 2 * gap
    cz = z0 + gap + door_h / 2
    cy = -D / 2 - dt / 2
    open_ang = math.radians(95)
    for i in range(doors):
        cx = -W / 2 + gap + dw / 2 + i * (dw + gap)
        parts = [box(f"door{i}", (dw, dt, door_h), (cx, cy, cz), body, bevel=0.003)]
        if f.get("brass"):
            parts += _brass_frame(mats["brass"], cx, cy - dt / 2, cz,
                                  dw * 0.92, door_h * 0.94)
        if f.get("handles_profile"):
            inner = cx + (dw * 0.40 if i == 0 else -dw * 0.40)
            parts.append(box(f"handle{i}", (0.012, 0.02, door_h * 0.55),
                             (inner, cy - dt / 2 - 0.01, cz), mats["dark"], bevel=0.002))
        if f.get("lock") and (i == doors - 1):
            parts.append(cylinder(f"lock{i}", 0.006, 0.01,
                                  (cx + (dw * 0.32 if doors == 1 else -dw * 0.32),
                                   cy - dt / 2 - 0.006, cz),
                                  mats["dark"], verts=18, bevel=0))
        # только одна открытая створка: правая (или единственная)
        open_door = (i == doors - 1)
        if doors > 1:
            hinge_x = (-W / 2 + 0.008) if i == 0 else (W / 2 - 0.008)
        else:
            hinge_x = W / 2 - 0.008
        if open_door:
            rotate_about_z(parts, W / 2, -D / 2, open_ang)
        _hinges(mats["steel"], hinge_x, -D / 2 - 0.004, cz, door_h)


def _hinges(mat, x, y, z_center, h, n=3):
    """Vertical barrel hinges at a door's pivot edge (do not rotate with door)."""
    for k in range(n):
        dz = (k - (n - 1) / 2) * (h * 0.8 / max(1, n - 1))
        cylinder(f"hinge_{k}", 0.009, 0.055, (x, y, z_center + dz), mat,
                 verts=16, bevel=0.001)


def _brass_frame(mat, cx, cy, cz, w, h, t=0.01, depth=0.006):
    # four thin brass bars forming a rectangle on the front (y = cy)
    return [
        box("br_t", (w + t, depth, t), (cx, cy, cz + h / 2), mat, bevel=0.001),
        box("br_b", (w + t, depth, t), (cx, cy, cz - h / 2), mat, bevel=0.001),
        box("br_l", (t, depth, h + t), (cx - w / 2, cy, cz), mat, bevel=0.001),
        box("br_r", (t, depth, h + t), (cx + w / 2, cy, cz), mat, bevel=0.001),
    ]


def build_desk(spec, mats):
    d = spec["dimensions"]
    f = spec.get("features", {})
    W, D, H = d["w"] * MM, d["d"] * MM, d["h"] * MM
    top_th = (f.get("thickness", {}).get("worktop") or 50) * MM
    leg_w = 50 * MM
    screen_h = (f.get("thickness", {}).get("screen") or 300) * MM
    oh = 25 * MM  # отрыв столешницы от опор
    body = mats["body"]
    leg_h = H - top_th
    # столешница шире и глубже опор на oh с каждой стороны
    box("worktop", (W + 2 * oh, D + 2 * oh, top_th),
        (0, 0, H - top_th / 2), body, bevel=0.006)
    # опоры уже и не глубже корпуса — отрыв 25 мм виден по периметру
    leg_d = D - 2 * oh
    for sgn in (-1, 1):
        cx = sgn * (W / 2 - leg_w / 2)
        box(f"leg{sgn}", (leg_w, leg_d, leg_h), (cx, 0, leg_h / 2), body)
        if f.get("brass"):
            box(f"legbrass{sgn}", (leg_w * 0.5, 0.012, leg_h - 0.05),
                (cx, -leg_d / 2 - 0.006, leg_h / 2), mats["brass"], bevel=0.002)
    # front screen (между опорами, заподлицо с их передней гранью)
    box("screen", (W - 2 * leg_w, 0.02, screen_h),
        (0, -leg_d / 2 + 0.05, leg_h - screen_h / 2), body, bevel=0.004)
    cylinder("grommet", 0.03, top_th + 0.004, (W * 0.12, 0.0, H - top_th / 2),
             mats["dark"], verts=28, bevel=0.001)
    if f.get("pc_holder"):
        # подвес ПК у правой опоры, передний край — хорошо виден сзади-сбоку
        rx = W / 2 - leg_w * 1.4
        _pc_holder(mats["dark"], rx, -D / 2 + oh + 0.14, H - top_th)
    if f.get("cable_channel"):
        _cable_channel(mats["dark"], W * 0.16, D * 0.05, H - top_th)


def _pc_holder(mat, x, y, z_top):
    # U-подвес для системного блока (крупнее, чтобы читался на рендере)
    box("pc_plate", (0.025, 0.18, 0.34), (x, y, z_top - 0.19), mat, bevel=0.004)
    box("pc_bot", (0.16, 0.18, 0.025), (x + 0.08, y, z_top - 0.36), mat, bevel=0.004)
    box("pc_lip", (0.025, 0.18, 0.07), (x + 0.165, y, z_top - 0.32), mat, bevel=0.004)


def _cable_channel(mat, x, y, z_top):
    # flexible vertical cable spine: stacked rounded segments down to the floor
    n = 16
    for i in range(n):
        t = i / (n - 1)
        z = z_top - 0.06 - t * (z_top - 0.06)
        sway = 0.04 * math.sin(t * math.pi * 1.2)
        box(f"cable{i}", (0.05, 0.03, (z_top / n) * 0.7),
            (x + sway, y, z + (z_top / n) * 0.35), mat, bevel=0.004)
    cylinder("cable_base", 0.06, 0.012, (x, y, 0.006), mat, verts=28, bevel=0.002)


def build_drawer_unit(spec, mats):
    d = spec["dimensions"]
    f = spec.get("features", {})
    W, D, H = d["w"] * MM, d["d"] * MM, d["h"] * MM
    top_th = (f.get("thickness", {}).get("worktop") or 50) * MM
    plinth = 25 * MM
    drawers = f.get("drawers", 3)
    ped_w = W * 0.45
    oh = 20 * MM
    body = mats["body"]
    box("worktop", (W + 2 * oh, D + 2 * oh, top_th), (0, 0, H - top_th / 2), body, bevel=0.006)
    # left side panel
    box("side", (25 * MM, D, H - top_th), (-W / 2 + 12 * MM, 0, (H - top_th) / 2), body)
    # right pedestal carcass (open front)
    px = W / 2 - ped_w / 2
    t = 0.016
    ped_h = H - top_th - plinth
    box("ped_back", (ped_w, t, ped_h), (px, D / 2 - t / 2, plinth + ped_h / 2), body)
    box("ped_side_l", (t, D, ped_h), (px - ped_w / 2 + t / 2, 0, plinth + ped_h / 2), body)
    box("ped_side_r", (t, D, ped_h), (px + ped_w / 2 - t / 2, 0, plinth + ped_h / 2), body)
    # drawers pulled open (staggered) with a real tray so the inside is visible
    avail = ped_h
    dh = avail / drawers
    gap = 0.004
    fr_t = 0.018
    wall = 0.012
    for i in range(drawers):
        cz = plinth + dh / 2 + i * dh
        # только нижний ящик выдвинут
        is_open = (i == 0)
        out = D * 0.40 if is_open else 0
        fy = -D / 2 - fr_t / 2 - out
        if is_open:
            for sx in (-1, 1):
                rx = px + sx * (ped_w / 2 - t - 0.005)
                box(f"runner{i}_{sx}", (0.012, D * 0.82, 0.014),
                    (rx, -D * 0.04, cz - dh * 0.12), mats["steel"], bevel=0.001)
        box(f"drawer{i}", (ped_w - 2 * gap, fr_t, dh - gap), (px, fy, cz), body, bevel=0.004)
        if is_open:
            tray_d = D * 0.78
            tray_w = ped_w - 2 * gap - 2 * wall
            by = fy + fr_t / 2 + tray_d / 2
            bz = cz - dh / 2 + gap + wall / 2
            box(f"tray_b{i}", (tray_w, tray_d, wall), (px, by, bz), body, bevel=0.002)
            box(f"tray_l{i}", (wall, tray_d, dh * 0.5), (px - tray_w / 2, by, bz + dh * 0.22), body, bevel=0.002)
            box(f"tray_r{i}", (wall, tray_d, dh * 0.5), (px + tray_w / 2, by, bz + dh * 0.22), body, bevel=0.002)
            box(f"tray_k{i}", (tray_w, wall, dh * 0.5), (px, by + tray_d / 2, bz + dh * 0.22), body, bevel=0.002)
        if f.get("lock") and i == drawers // 2:
            cylinder("lock", 0.006, 0.01, (px, fy - fr_t / 2 - 0.006, cz),
                     mats["dark"], verts=18, bevel=0)
    box("plinth", (ped_w, D, plinth), (px, 0, plinth / 2), mats["dark"], bevel=0.002)


def build_coffee_round(spec, mats):
    d = spec["dimensions"]
    Dia = (d.get("diameter") or d.get("w") or 500) * MM
    H = d["h"] * MM
    top_th = 0.038
    ped_d = Dia * 0.62
    body = mats["body"]
    cylinder("pedestal", ped_d / 2, H - top_th, (0, 0, (H - top_th) / 2), body, verts=64)
    cylinder("top", Dia / 2, top_th, (0, 0, H - top_th / 2), body, verts=80, bevel=0.012)


def build_coffee_fluted(spec, mats):
    d = spec["dimensions"]
    Dia = (d.get("diameter") or d.get("w") or 400) * MM
    H = d["h"] * MM
    body = mats["body"]
    R = Dia / 2
    top_th = 0.038  # сплошная столешница сверху (поверхность для предметов)
    flute_h = H - top_th
    rr = R * 0.92
    n = max(20, int(2 * math.pi * rr / 0.032))
    fr = (2 * math.pi * rr / n) * 0.52
    # внутреннее заполнение — не полая «труба»; гофра по бокам на всю высоту тела
    cylinder("core", R * 0.82, flute_h - 0.002, (0, 0, flute_h / 2), body,
             verts=96, bevel=0.0)
    for i in range(n):
        a = 2 * math.pi * i / n
        cx, cy = rr * math.cos(a), rr * math.sin(a)
        cylinder(f"flute{i}", fr, flute_h, (cx, cy, flute_h / 2), body,
                 verts=12, bevel=0)
    # гладкая столешница-диск (не «ободок» — рабочая поверхность); снизу без колпака
    cylinder("top", R * 1.01, top_th, (0, 0, H - top_th / 2), body, verts=96,
             bevel=0.01)


def build_coffee_rect(spec, mats):
    d = spec["dimensions"]
    f = spec.get("features", {})
    W, D, H = d["w"] * MM, d["d"] * MM, d["h"] * MM
    top_th = (f.get("thickness", {}).get("worktop") or 50) * MM
    body = mats["body"]
    if d["w"] >= 800:  # legged table with oval pedestals
        box("top", (W, D, top_th), (0, 0, H - top_th / 2), body, bevel=0.02)
        leg_h = H - top_th
        for sgn in (-1, 1):
            cx = sgn * W * 0.27
            o = cylinder(f"leg{sgn}", D * 0.22, leg_h, (cx, 0, leg_h / 2), body, verts=48)
            o.scale = (1.0, 0.6, 1.0)  # oval footprint
            bpy.ops.object.transform_apply(scale=True)
    else:  # solid block
        box("block", (W, D, H), (0, 0, H / 2), body, bevel=0.02)


BUILDERS = {
    "wardrobe": build_casegood, "cabinet": build_casegood,
    "desk_panel": build_desk, "drawer_unit": build_drawer_unit,
    "coffee_round": build_coffee_round, "coffee_fluted": build_coffee_fluted,
    "coffee_rect": build_coffee_rect,
}


# ---------------------------------------------------------------------------
# Scene setup, camera, lights, render
# ---------------------------------------------------------------------------
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras):
        for b in list(coll):
            if b.users == 0:
                coll.remove(b)


def setup_world():
    """Procedural studio dome: soft vertical gradient (HDRI-like, no file).

    Gives smooth ambient fill and believable graded reflections on brass,
    while sun lights below keep exposure stable.
    """
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.45
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    e = ramp.color_ramp.elements
    e[0].position = 0.0
    e[0].color = (0.32, 0.32, 0.35, 1.0)   # horizon / lower hemisphere
    e[1].position = 1.0
    e[1].color = (0.92, 0.93, 0.96, 1.0)   # zenith
    mid = ramp.color_ramp.elements.new(0.5)
    mid.color = (0.66, 0.67, 0.70, 1.0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    texco = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(texco.outputs["Generated"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def add_sun(name, energy, angle_deg, from_dir, shadow=True):
    import mathutils
    ld = bpy.data.lights.new(name, type="SUN")
    ld.energy = energy
    ld.angle = math.radians(angle_deg)  # bigger angle -> softer shadow edges
    ld.use_shadow = shadow
    ob = bpy.data.objects.new(name, ld)
    bpy.context.collection.objects.link(ob)
    travel = -mathutils.Vector(from_dir).normalized()  # light travels this way
    ob.rotation_euler = travel.to_track_quat("-Z", "Y").to_euler()
    return ob


def setup_lights(target, R):
    # Distance-independent sun lights -> stable exposure for any object size.
    # Only the key casts a shadow, placed mostly overhead and softened so the
    # contact shadow is a small, faint pool that just reads volume.
    # Intensities kept moderate so light/cream objects do NOT blow out to white.
    add_sun("key", 2.2, 17, (0.28, -0.42, 1.05), shadow=True)
    add_sun("fill", 0.7, 35, (-0.75, -0.45, 0.45), shadow=False)
    add_sun("rim", 0.85, 12, (-0.25, 0.85, 0.55), shadow=False)


def _aim(ob, target):
    import mathutils
    d = mathutils.Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def _add_shadow_catcher(size):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, 0))
    o = bpy.context.active_object
    o.name = "shadow_catcher"
    o.is_shadow_catcher = True


def scene_bounds():
    """World-space bounding box of all mesh objects except the shadow catcher."""
    mn = [1e9, 1e9, 1e9]
    mx = [-1e9, -1e9, -1e9]
    for o in bpy.data.objects:
        if o.type != "MESH" or o.name == "shadow_catcher":
            continue
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            for i in range(3):
                mn[i] = min(mn[i], w[i])
                mx[i] = max(mx[i], w[i])
    center = [(mn[i] + mx[i]) / 2 for i in range(3)]
    diag = math.sqrt(sum((mx[i] - mn[i]) ** 2 for i in range(3)))
    return center, diag * 0.5, mn, mx


def framing_radius(spec, arch):
    """Camera distance radius from spec dims (not open-door bounds).

    Open doors inflate scene_bounds and push the camera far away, making tall
    items look tiny on the sheet. Framing uses the carcass box instead.
    """
    d = spec["dimensions"]
    W = (d.get("w") or d.get("diameter") or 500) * MM
    D = (d.get("d") or d.get("diameter") or 400) * MM
    H = (d.get("h") or 500) * MM
    R = 0.5 * math.sqrt(W * W + D * D + H * H)
    # carcass radius only (open doors не раздувают кадр); без агрессивного zoom
    pad = {"wardrobe": 1.05, "cabinet": 1.02}.get(arch, 1.0)
    return R * pad


# margin камеры: меньше = крупнее в кадре (без slice!)
CAM_MARGIN = {
    "wardrobe": 1.02,
    "cabinet": 1.04,
    "desk_panel": 0.96,
    "drawer_unit": 1.04,
}


def setup_camera(center, R, direction, margin=1.15):
    cam_d = bpy.data.cameras.new("cam")
    cam_d.lens = 80
    cam_d.sensor_width = 36
    cam = bpy.data.objects.new("cam", cam_d)
    bpy.context.collection.objects.link(cam)
    fov = 2 * math.atan(cam_d.sensor_width / (2 * cam_d.lens))
    dist = (R / math.sin(fov / 2)) * margin
    import mathutils
    d = mathutils.Vector(direction).normalized()
    cam.location = mathutils.Vector(center) + d * dist
    _aim(cam, center)
    bpy.context.scene.camera = cam
    return R


def setup_render(out_png, res=1100, samples=24):
    sc = bpy.context.scene
    # Cycles on CPU: works headless on any hardware (no GPU compute needed).
    # Low sample count + adaptive sampling + OpenImageDenoise keeps these simple
    # matte scenes clean while roughly halving render time.
    sc.render.engine = "CYCLES"
    try:
        sc.cycles.device = "CPU"
        sc.cycles.samples = samples
        sc.cycles.use_adaptive_sampling = True
        sc.cycles.adaptive_threshold = 0.03
        sc.cycles.use_denoising = True
        try:
            sc.cycles.denoiser = "OPENIMAGEDENOISE"
        except Exception:
            pass
        sc.cycles.max_bounces = 6
    except Exception:
        pass
    sc.render.film_transparent = True
    sc.render.resolution_x = res
    sc.render.resolution_y = res
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    try:
        sc.view_settings.view_transform = "Standard"
        sc.view_settings.exposure = -0.25  # запас от пересвета светлых поверхностей
    except Exception:
        pass
    sc.render.filepath = out_png


def make_mats(spec):
    mat = spec.get("material", {})
    col = resolve_color(mat)
    return {
        "body": mat_paint("mdf", col, roughness=0.6),
        "brass": mat_metal("brass", BRASS),
        "dark": mat_plastic("dark", DARK, roughness=0.55),
        "steel": mat_metal("steel", "#8C9298", roughness=0.38),  # петли/направляющие
    }


def render_item(spec, out_dir):
    clear_scene()
    setup_world()
    mats = make_mats(spec)
    arch = spec.get("archetype") or "cabinet"
    builder = BUILDERS.get(arch, build_casegood)
    builder(spec, mats)
    elev = {"drawer_unit": 0.34}.get(arch, 0.5)
    if arch == "desk_panel":
        # низкий ракурс справа-сзади — видны подвес ПК и кабель-канал
        direction = (0.75, 0.55, 0.18)
    else:
        direction = (0.62, -1.0, elev)
    center, _, mn, mx = scene_bounds()
    R = framing_radius(spec, arch)
    margin = CAM_MARGIN.get(arch, 1.12)
    setup_camera(center, R, direction, margin=margin)
    setup_lights(center, R)
    _add_shadow_catcher(max(mx[0] - mn[0], mx[1] - mn[1]) * 3.0 + 1.0)
    slug = spec.get("_slug") or (spec.get("name", "item"))
    out_png = os.path.join(out_dir, slug + ".png")
    setup_render(out_png)
    bpy.ops.render.render(write_still=True)
    return out_png


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    specs_path = argv[0]
    out_dir = argv[1]
    only_idx = int(argv[2]) if len(argv) > 2 else None
    os.makedirs(out_dir, exist_ok=True)
    with open(specs_path, encoding="utf-8") as fh:
        specs = json.load(fh)
    for i, spec in enumerate(specs):
        if only_idx is not None and i != only_idx:
            continue
        try:
            p = render_item(spec, out_dir)
            print("RENDERED", p)
        except Exception as e:  # keep going
            print("FAILED", spec.get("name"), repr(e))


if __name__ == "__main__":
    main()
