"""Measure a house .blend for a BOQ take-off — headless, read-only (never saves the file).

    Blender -b <house.blend> --factory-startup --disable-autoexec --python-exit-code 1 -P blend_takeoff.py -- --out <takeoff.json> [--skip "Car*,Tree*"]
    Blender -b --factory-startup --disable-autoexec --python-exit-code 1 -P blend_takeoff.py -- --selftest

Prints a compact Markdown summary and writes every measure to the JSON. Names follow the nuea-ai-blender-house skill: walls in "<PROJ> Walls", openings in "<PROJ> Openings",
Roof_* parts, Str_* structure, Slab/Floor/Ceiling. Anything else is listed under "## other" with its size, so no object is lost.
The "## Source" line, before the walls, says which kind of model it is (construction model with STnn stages, sales model, or another model).
All lengths m, areas m2, volumes m3, world space, modifiers applied.
"""
import fnmatch
import json
import math
import os
import re
import sys
import tempfile

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(key, default=None):
    return ARGV[ARGV.index(key) + 1] if key in ARGV else default


SKIP = [s for s in (arg("--skip", "") or "").split(",") if s] + ["Plan_*", "Ground*", "Road*", "Sky*", "Cam_*"]
HEAVY = 300_000          # faces: above this only the bounding box is measured (roof tile meshes)
r = lambda x, n=2: round(x, n)


def world_bm(o, dg):
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    ev.to_mesh_clear()
    bm.transform(o.matrix_world)
    if o.matrix_world.determinant() < 0:   # mirrored: the transform flipped every face's winding
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.normal_update()
    return bm


def bbox(o):
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


def faces(bm):
    """Face areas by direction. slope = upward faces 3..87 deg; plan = their horizontal projection."""
    d = dict(up=0.0, down=0.0, side=0.0, slope=0.0, plan=0.0)
    for f in bm.faces:
        a, nz = f.calc_area(), f.normal.z
        if nz > 0.9986:
            d["up"] += a
        elif nz < -0.9986:
            d["down"] += a
        elif abs(nz) < 0.0523:
            d["side"] += a
        elif nz > 0:
            d["slope"] += a
            d["plan"] += a * nz
        else:
            d["down"] += a
    return d


def parts(bm):
    """One row per connected piece: (longest edge, volume, z0, z1, open). A box member's longest edge is its
    length; an open piece (boundary edges) has no true volume, so its section is unreliable."""
    bm.verts.index_update()
    root = list(range(len(bm.verts)))

    def find(i):
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    for e in bm.edges:
        a, b = find(e.verts[0].index), find(e.verts[1].index)
        if a != b:
            root[a] = b
    acc = {}
    for v in bm.verts:
        row = acc.setdefault(find(v.index), [0.0, 0.0, 1e9, -1e9, False])
        row[2], row[3] = min(row[2], v.co.z), max(row[3], v.co.z)
    for e in bm.edges:
        row = acc[find(e.verts[0].index)]
        row[0] = max(row[0], e.calc_length())
        row[4] = row[4] or e.is_boundary
    for f in bm.faces:   # divergence theorem per piece
        acc[find(f.verts[0].index)][1] += f.calc_area() * f.calc_center_median().dot(f.normal) / 3
    return [(L, abs(V), z0, z1, op) for L, V, z0, z1, op in acc.values() if L > 0]


def members(pl):
    """Group pieces by cross-section: {section_cm2: [count, total_length]}. Modelled rebar is a square bar,
    so its size is the side sqrt(A): 0.36 cm2 = RB6, 1.44 cm2 = DB12."""
    g = {}
    for L, V, *_ in pl:
        a = round(V / L * 1e4, 1)
        row = g.setdefault(a, [0, 0.0])
        row[0] += 1
        row[1] += L
    return {(f"{a}cm2 bar≈{math.sqrt(a) * 10:.0f}mm" if a < 6 else f"{a}cm2"): [n, r(L)]
            for a, (n, L) in sorted(g.items())}


def run_length(pl):
    """Cornice/gutter/fascia: parallel bands stacked at several z levels share one run, so run = total / levels.
    Only for those: an architrave (two jambs + head) or a downpipe uses the total."""
    levels = {(round(p[2], 2), round(p[3], 2)) for p in pl}
    return r(sum(p[0] for p in pl) / max(1, len(levels))), len(levels)


def roof_lines(bm):
    """Lines of the sloped (upward) faces. Between two slopes: hip (convex, inclined), valley (concave), ridge
    (convex, level); coplanar splits are skipped. Edge of one slope: eave (level, slope rises from it), top edge
    (level, slope falls to it, e.g. a lean-to against a wall), rake/verge (inclined, a gable's edge)."""
    def sloped(f):
        return 0.0523 < f.normal.z < 0.9986
    sl = [f for f in bm.faces if sloped(f)]
    if not sl:
        return {}
    d = dict(eave=0.0, ridge=0.0, hip=0.0, valley=0.0, rake=0.0, top_edge=0.0)
    for e in bm.edges:
        fs = [f for f in e.link_faces if sloped(f)]
        a, b = e.verts[0].co, e.verts[1].co
        flat, L, ez = abs(a.z - b.z) < 0.01, e.calc_length(), (a.z + b.z) / 2
        if len(fs) == 2:
            if e.calc_face_angle(0.0) < math.radians(1):
                continue
            k = ("ridge" if flat else "hip") if e.is_convex else "valley"
        elif len(fs) == 1:
            k = ("eave" if fs[0].calc_center_median().z > ez else "top_edge") if flat else "rake"
        else:
            continue
        d[k] += L
    d = {k: r(v) for k, v in d.items() if v}
    d["z_eave"] = r(min(v.co.z for f in sl for v in f.verts))
    d["z_top"] = r(max(v.co.z for f in sl for v in f.verts))
    return d


OPEN_SKIP = re.compile(r"_(glass|sill|spandrel|Handles?|Panels?)$")
DOOR = re.compile(r"(^|_)(Door|D\d)", re.I)


def ancestors(o):
    p = o.parent
    while p:
        yield p
        p = p.parent


def classify(o, cols):
    n = o.name
    if any(c.endswith(" Walls") for c in cols) or re.match(r"W_", n):
        return "wall"
    if any(c.endswith(" Openings") for c in cols):
        return "trim" if n.startswith("Trim_") or n.endswith("_trim") else (None if OPEN_SKIP.search(n) else "opening")
    if n.startswith("Str_Rebar"):
        return "rebar"
    if n.startswith("Str_"):
        return "structure"
    if re.search(r"_Hip$", n):
        return "roof"
    if re.search(r"(Frame|Battens|Purlins?|Rafters?)$", n) and ("Roof" in n or "Carport" in n):
        return "steel"
    if re.search(r"Tiles$", n):
        return "tiles"
    if re.search(r"Downlight|Lamp|Light", n):
        return "lamp"
    if re.search(r"Caps$|Valleys?$|Gutter|Cornice|Fascia|Flashing|Downpipe", n):
        return "linear"
    if re.search(r"Fence|Paving|Driveway|Gate|Lawn", n):
        return "site"
    if re.search(r"Slab|Floor|Steps?$|Terrace", n):
        return "slab"
    if re.search(r"Ceiling|Soffit|Eave$", n):
        return "ceiling"
    if re.search(r"Column|Pedestal|Beam|Fillet|Post", n):
        return "column_beam"
    if re.search(r"Sheet|Carport", n):
        return "sheet"
    if re.match(r"(Clad|Tile|Splash|Skirting)", n):
        return "finish"
    if re.match(r"(Kit|Fx|Bath|Shower|Counter|Sink|Basin)", n):
        return "fixture"
    return "other"


STAGE = re.compile(r"(?:^|\s)ST(\d{2})(?:\s|$)")


def source(col_names, blend_dir):
    """The nuea-ai-blender-house naming is the channel's mark in the file: '<PROJ> STnn <name>' stage collections
    (construction model) or '<PROJ> Walls' / '<PROJ> Openings' (sales model). Spec files sit beside the .blend."""
    stages = sorted({m.group(1) for m in map(STAGE.search, col_names) if m})
    specs, with_table = [], 0
    if blend_dir and os.path.isdir(blend_dir):
        try:
            names = os.listdir(blend_dir)
        except OSError:
            names = []
        specs = sorted(f for f in names
                       if fnmatch.fnmatch(f, "ST[0-9][0-9]_*_spec.md") and os.path.isfile(os.path.join(blend_dir, f)))
        for f in specs:
            try:
                with open(os.path.join(blend_dir, f), encoding="utf-8", errors="replace") as fh:
                    with_table += bool(re.search(r"^## ปริมาณ", fh.read(), re.M))
            except OSError:
                pass   # unreadable: counted as a spec without a table, the take-off goes on
    walls = {c[:-len(" Walls")] for c in col_names if c.endswith(" Walls")}
    opens = {c[:-len(" Openings")] for c in col_names if c.endswith(" Openings")}
    if stages:
        kind = "construction"
    elif walls & opens:   # the skill makes both for one project; another tool's "Exterior Walls" alone is not its mark
        kind = "sales"
    else:
        kind = "other"
    return dict(kind=kind, stages=stages, specs=len(specs), specs_with_table=with_table)


SOURCE_TEXT = {
    "construction": "nuea-ai-blender-house construction model, built stage by stage: read the stage quantity tables first",
    "construction_no_specs": "nuea-ai-blender-house construction model, built stage by stage, but no STnn spec files "
                             "beside the .blend: ask the user where they are, else take quantities from the measures below",
    "construction_old_specs": "nuea-ai-blender-house construction model, built stage by stage; its spec files have no "
                              "## ปริมาณ table (older format): read their quantities and cross-check with the measures below",
    "sales": "nuea-ai-blender-house sales model (Walls/Openings collections, no STnn stages): structure, rebar, pipes "
             "and wiring are not modelled",
    "other": "not made with nuea-ai-blender-house: objects are sorted by name and size only",
}


def source_line(src, known, total):
    kind = src["kind"]
    if kind == "construction" and not src["specs_with_table"]:
        kind = "construction_old_specs" if src["specs"] else "construction_no_specs"
    stages = f" · stages {' '.join('ST' + s for s in src['stages'])}" if src["stages"] else ""
    specs = (f" · {src['specs']} spec files beside the .blend, {src['specs_with_table']} with a ## ปริมาณ table"
             if src["kind"] == "construction" or src["specs"] else "")
    return f"## Source: {SOURCE_TEXT[kind]}{stages}{specs} · {known} of {total} objects recognised by name"


def outside(bvh, p, ax, sgn, zs):
    """True when rays leaving p along ±axis hit no wall at every height in zs (one height may pass a window)."""
    d = Vector((0.0, 0.0, 0.0))
    d[ax] = sgn
    return all(bvh.ray_cast(Vector((p[0], p[1], z)), d, 60.0)[0] is None for z in zs)


def wall_sides(walls, bvh):
    """ext_frac = share of the wall's length whose more-exposed face is outside (porch-side stretch of a wall counts).
    Rays at 0.3 m above the base (under sills) and 0.15 m under the top (over heads) both have to miss."""
    for w in walls:
        lo, hi = w["lo"], w["hi"]
        ax = 0 if (hi.x - lo.x) < (hi.y - lo.y) else 1
        u = 1 - ax
        zs = (lo.z + 0.3, hi.z - 0.15)
        best = 0.0
        for sgn in (-1, 1):
            miss = 0
            for k in range(10):
                p = Vector((0.0, 0.0, 0.0))
                p[u] = lo[u] + (k + 0.5) / 10 * (hi[u] - lo[u])
                p[ax] = (hi[ax] + 0.01) if sgn > 0 else (lo[ax] - 0.01)
                miss += outside(bvh, p, ax, sgn, zs)
            best = max(best, miss / 10)
        w["ext_frac"] = best
        w["ax"] = ax


def measure(skip=SKIP):
    dg = bpy.context.evaluated_depsgraph_get()
    vl = bpy.context.view_layer.objects
    rows, lights, assets, wall_polys = [], {}, {}, ([], [])
    for o in vl:
        if any(fnmatch.fnmatch(o.name, p) for p in skip):
            continue
        cols = [c.name for c in o.users_collection]
        if o.type == "LIGHT":
            k = re.sub(r"[._]\d+$", "", o.name)
            lights[k] = lights.get(k, 0) + 1
            continue
        if o.type == "EMPTY" and o.parent is None and ("asset_folder" in o or o.instance_collection):
            k = re.sub(r"[._]\d+$", "", o.name)
            assets[k] = assets.get(k, 0) + 1
            continue
        if any("asset_root" in a or "asset_folder" in a for a in ancestors(o)):
            continue
        if o.type not in ("MESH", "CURVE") or any(c.startswith(("ASSET_", "SRC_", "LIB_")) or
                                                    c.endswith(" Landscape") for c in cols):
            continue
        cls = classify(o, cols)
        if cls is None:
            continue
        lo, hi = bbox(o)
        row = dict(name=o.name, cls=cls, col=cols[0] if cols else "", hide_render=o.hide_render,
                   mat=[m.name for m in o.data.materials if m][:2], lo=lo, hi=hi,
                   dims=[r(hi[i] - lo[i]) for i in range(3)])
        if o.type == "CURVE":
            sc = sum(o.matrix_world.to_scale()) / 3
            row["length"] = r(sum(s.calc_length() for s in o.data.splines) * sc)
        nf = len(o.data.polygons) if o.type == "MESH" else 0
        if nf > HEAVY:
            row["heavy"] = nf
            rows.append(row)
            continue
        bm = world_bm(o, dg)
        row["area"] = {k: r(v) for k, v in faces(bm).items() if v > 0.005}
        if cls in ("structure", "slab", "column_beam", "rebar"):
            row["volume"] = r(abs(bm.calc_volume()), 3)
        if cls == "wall":
            ax = 0 if row["dims"][0] < row["dims"][1] else 1
            n = Vector((1, 0, 0)) if ax == 0 else Vector((0, 1, 0))
            row["thickness"] = row["dims"][ax]
            row["length"] = row["dims"][1 - ax]
            row["height"] = row["dims"][2]
            row["net_area_one_side"] = r(sum(f.calc_area() for f in bm.faces if abs(f.normal.dot(n)) > 0.9) / 2)
            axis_area = sum(f.calc_area() for f in bm.faces if max(abs(f.normal.x), abs(f.normal.y)) > 0.9)
            if axis_area < 0.9 * row["area"].get("side", 0):
                row["warn"] = "not axis-aligned: measure this wall by hand"
            base = len(wall_polys[0])
            wall_polys[0].extend(v.co.copy() for v in bm.verts)
            wall_polys[1].extend([v.index + base for v in f.verts] for f in bm.faces)
        if cls in ("steel", "rebar", "linear", "structure", "lamp", "column_beam", "trim") and o.type == "MESH":
            pl = parts(bm)
            row["pieces"] = len(pl)
            if cls in ("steel", "rebar"):
                row["members"] = members(pl)
            if cls in ("linear", "trim"):
                row["total_length"] = r(sum(p[0] for p in pl))
            if cls == "linear":
                row["run_length"], row["bands"] = run_length(pl)
            opened = sum(p[4] for p in pl)
            if opened and cls in ("steel", "rebar", "structure", "column_beam"):
                row["open_pieces"] = opened
        if cls == "roof":
            row.update(roof_lines(bm))
            a = row["area"]
            if a.get("slope"):
                row["pitch_deg"] = r(math.degrees(math.acos(min(1.0, a["plan"] / a["slope"]))), 1)
        bm.free()
        rows.append(row)

    walls = [w for w in rows if w["cls"] == "wall"]
    if walls:
        bm2 = bmesh.new()
        for v in wall_polys[0]:
            bm2.verts.new(v)
        bm2.verts.ensure_lookup_table()
        for f in wall_polys[1]:
            try:
                bm2.faces.new([bm2.verts[i] for i in f])
            except ValueError:   # duplicate face where two pieces touch
                pass
        bvh = BVHTree.FromBMesh(bm2)
        bm2.free()
        wall_sides(walls, bvh)
        openings = group_openings([o for o in rows if o["cls"] == "opening"], walls, bvh)
    else:
        openings = []
    return rows, openings, lights, assets


def group_openings(items, walls, bvh):
    """Leaves and frames that touch form one opening. Exterior = rays from its host wall face, at the opening's
    mid height and over the wall's openings, miss every wall on one side."""
    n = len(items)
    root = list(range(n))

    def find(i):
        while root[i] != i:
            i = root[i]
        return i

    def touch(a, b):
        """Overlap within 3 cm; across the wall (an axis where both are thin) a frame box may sit 30 cm out."""
        def gap(k):
            thin = max(a["hi"][k] - a["lo"][k], b["hi"][k] - b["lo"][k]) < 0.3
            return 0.3 if thin else 0.03
        return all(a["lo"][k] - gap(k) <= b["hi"][k] and b["lo"][k] - gap(k) <= a["hi"][k] for k in range(3))

    for i in range(n):
        for j in range(i + 1, n):
            if touch(items[i], items[j]):
                root[find(i)] = find(j)
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    out = []
    for g in groups.values():
        lo = Vector([min(x["lo"][k] for x in g) for k in range(3)])
        hi = Vector([max(x["hi"][k] for x in g) for k in range(3)])
        c = (lo + hi) / 2
        host = min(walls, key=lambda w: sum(max(0.0, w["lo"][k] - c[k], c[k] - w["hi"][k]) for k in range(2)),
                   default=None)
        ext = False
        if host:
            ax = host["ax"]
            zs = (c.z, host["hi"].z - 0.15)
            for sgn in (-1, 1):
                p = c.copy()
                p[ax] = (host["hi"][ax] + 0.01) if sgn > 0 else (host["lo"][ax] - 0.01)
                ext = ext or outside(bvh, p, ax, sgn, zs)
        out.append(dict(names=sorted(x["name"] for x in g),
                        kind="door" if any(DOOR.search(x["name"]) for x in g) else "window",
                        w=r(max(hi.x - lo.x, hi.y - lo.y)), h=r(hi.z - lo.z), z0=r(lo.z),
                        wall=host["name"] if host else None, exterior=ext,
                        hidden=any(x["hide_render"] for x in g)))
    return sorted(out, key=lambda o: (o["kind"], -o["w"] * o["h"]))


def report(rows, openings, lights, assets, src):
    P = print
    P(source_line(src, sum(x["cls"] != "other" for x in rows), len(rows)))
    walls = [w for w in rows if w["cls"] == "wall"]
    P("## Walls (net = one face, openings removed · ext = face area outdoors)")
    for t in sorted({w["thickness"] for w in walls}):
        ws = [w for w in walls if w["thickness"] == t]
        P(f"- t={t}: {len(ws)} walls, length {r(sum(w['length'] for w in ws))} m, net "
          f"{r(sum(w['net_area_one_side'] for w in ws))} m2, ext {r(sum(w['net_area_one_side'] * w['ext_frac'] for w in ws))} m2")
    for w in walls:
        flags = " ".join(f for f in (w.get("warn"), "hidden" if w["hide_render"] else None) if f)
        P(f"  - {w['name']} t={w['thickness']} L={w['length']} H={w['height']} net={w['net_area_one_side']} "
          f"ext_frac={w['ext_frac']} {flags}".rstrip())
    P("## Openings (w x h · z0 = bottom level)")
    for o in openings:
        P(f"- {o['kind']} {o['w']}x{o['h']} z0 {o['z0']} {'EXT' if o['exterior'] else 'int'} in {o['wall']} "
          f"[{', '.join(o['names'])}]{' hidden' if o['hidden'] else ''}")
    for cls in ("roof", "sheet", "tiles", "steel", "linear", "trim", "structure", "rebar", "slab", "ceiling",
                "column_beam", "lamp", "finish", "fixture", "site", "other"):
        rs = [x for x in rows if x["cls"] == cls]
        if not rs:
            continue
        if cls == "other":
            P(f"## other (not classified — look at each before ignoring): {', '.join(x['name'] for x in rs)}")
            continue
        P(f"## {cls}")
        for x in rs:
            keep = {k: v for k, v in x.items() if k not in ("name", "cls", "col", "lo", "hi", "mat") and v}
            P(f"- {x['name']}: {json.dumps(keep, ensure_ascii=False)}")
    if lights:
        P(f"## lights {json.dumps(lights, ensure_ascii=False)}")
    if assets:
        P(f"## placed assets {json.dumps(assets, ensure_ascii=False)}")


def selftest():
    """4 x 3 m room, 0.15 walls 3 m high, one 1.2 x 1.0 window, an inner wall, a 30 deg roof plane, 2 steel members."""
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    wc = bpy.data.collections.new("T Walls")
    oc = bpy.data.collections.new("T Openings")
    sc = bpy.context.scene.collection
    for c in (wc, oc):
        sc.children.link(c)

    def obj(name, boxes, col=sc):
        bm = bmesh.new()
        for x0, y0, z0, x1, y1, z1 in boxes:
            g = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=(x1 - x0, y1 - y0, z1 - z0), verts=g["verts"])
            bmesh.ops.translate(bm, vec=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), verts=g["verts"])
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        o = bpy.data.objects.new(name, me)
        col.objects.link(o)
        return o

    t = 0.15
    obj("W_Front", [(-t / 2, -t / 2, 0, 1.4, t / 2, 3), (2.6, -t / 2, 0, 4 + t / 2, t / 2, 3),
                    (1.4, -t / 2, 0, 2.6, t / 2, 1.0), (1.4, -t / 2, 2.0, 2.6, t / 2, 3)], wc)
    obj("W_Rear", [(-t / 2, 3 - t / 2, 0, 4 + t / 2, 3 + t / 2, 3)], wc)
    obj("W_Left", [(-t / 2, t / 2, 0, t / 2, 3 - t / 2, 3)], wc)
    obj("W_Right", [(4 - t / 2, t / 2, 0, 4 + t / 2, 3 - t / 2, 3)], wc)
    obj("W_Mid", [(1.95, t / 2, 0, 2.05, 3 - t / 2, 3)], wc)
    obj("Win_Test_frame", [(1.4, -0.035, 1.0, 2.6, 0.035, 2.0)], oc)
    obj("Win_Test_glass", [(1.4, -0.005, 1.0, 2.6, 0.005, 2.0)], oc)
    obj("Roof_Frame", [(0, 0, 3, 3.0, 0.05, 3.1), (0, 1, 3, 0.05, 3.0, 3.1)])
    bm = bmesh.new()
    h = 3 * math.tan(math.radians(30))
    vs = [bm.verts.new(v) for v in ((0, 0, 3), (4, 0, 3), (4, 3, 3 + h), (0, 3, 3 + h))]
    bm.faces.new(vs)
    me = bpy.data.meshes.new("Roof_Hip")
    bm.to_mesh(me)
    sc.objects.link(bpy.data.objects.new("Roof_Hip", me))
    mirror = bpy.data.objects.new("Roof_Mirror_Hip", me)
    mirror.location.x, mirror.scale.x = 10.0, -1.0
    sc.objects.link(mirror)

    rows, openings, *_ = measure(skip=[])
    by = {x["name"]: x for x in rows}
    assert abs(by["W_Front"]["net_area_one_side"] - (4.15 * 3 - 1.2)) < 0.01, by["W_Front"]
    assert by["W_Front"]["ext_frac"] == 1.0 and by["W_Rear"]["ext_frac"] == 1.0, by["W_Front"]
    assert by["W_Mid"]["ext_frac"] == 0.0, by["W_Mid"]
    assert len(openings) == 1 and openings[0]["w"] == 1.2 and openings[0]["h"] == 1.0, openings
    assert openings[0]["exterior"] and openings[0]["wall"] == "W_Front", openings
    m = by["Roof_Frame"]["members"]
    assert list(m.values()) == [[2, 5.0]] and list(m)[0].startswith("50.0cm2"), m
    assert abs(by["Roof_Hip"]["area"]["slope"] - 12 / math.cos(math.radians(30))) < 0.01, by["Roof_Hip"]
    assert by["Roof_Hip"]["pitch_deg"] == 30.0 and by["Roof_Hip"]["eave"] == 4.0, by["Roof_Hip"]
    assert by["Roof_Hip"]["top_edge"] == 4.0 and by["Roof_Hip"]["rake"] == r(2 * 3 / math.cos(math.radians(30))), by["Roof_Hip"]
    assert by["Roof_Mirror_Hip"]["area"]["slope"] == by["Roof_Hip"]["area"]["slope"], by["Roof_Mirror_Hip"]
    assert by["Roof_Mirror_Hip"]["eave"] == 4.0, by["Roof_Mirror_Hip"]
    sales = source([c.name for c in sc.children_recursive], "")
    assert sales["kind"] == "sales" and "sales model" in source_line(sales, 3, 5), sales
    other = source(["Collection", "BEST01 x", "Exterior Walls"], "")
    assert other["kind"] == "other" and "not made with" in source_line(other, 3, 5), other
    with tempfile.TemporaryDirectory() as d:
        for f, text in (("ST01_footings_spec.md", "# ST01\n\n## ปริมาณ\n| a |\n"), ("ST03_beams_spec.md", "# ST03\n"),
                        ("notes.md", "## ปริมาณ\n")):
            with open(os.path.join(d, f), "w", encoding="utf-8") as fh:
                fh.write(text)
        os.mkdir(os.path.join(d, "ST02_folder_spec.md"))   # a folder with a spec's name is skipped, not an error
        s = source(["E42 ST01 ฐานราก", "E42 ST03 คานคอดิน", "E42 Walls"], d)
        assert s == dict(kind="construction", stages=["01", "03"], specs=2, specs_with_table=1), s
        line = source_line(s, 3, 5)
        assert "read the stage quantity tables first · stages ST01 ST03 · 2 spec files" in line, line
        assert line.endswith("3 of 5 objects recognised by name"), line
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "ST12_conduit_spec.md"), "w", encoding="utf-8") as fh:
            fh.write("# ST12\n\n## ตารางท่อ\n")
        old = source(["E42 ST12 ไฟฟ้า"], d)
        assert "older format" in source_line(old, 3, 5) and "1 spec files" in source_line(old, 3, 5), old
    bare = source(["E42 ST01 ฐานราก"], "")
    assert "no STnn spec files" in source_line(bare, 3, 5) and "0 spec files" in source_line(bare, 3, 5), bare
    print("SELFTEST OK")


def main():
    if "--selftest" in ARGV:
        selftest()
        return
    rows, openings, lights, assets = measure()
    src = source([c.name for c in bpy.context.scene.collection.children_recursive],
                 os.path.dirname(bpy.path.abspath(bpy.data.filepath)))
    report(rows, openings, lights, assets, src)
    out = arg("--out")
    if out and (not out.lower().endswith(".json") or bpy.path.abspath(out) == bpy.data.filepath):
        sys.exit("blend_takeoff: --out must be a .json file, not the model")
    if out:
        for x in rows:
            x["lo"], x["hi"] = [r(v) for v in x["lo"]], [r(v) for v in x["hi"]]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(dict(file=bpy.path.basename(bpy.data.filepath), source=src, objects=rows, openings=openings, lights=lights, assets=assets),
                      f, ensure_ascii=False, indent=1)
        print(f"WROTE {out}")


main()
