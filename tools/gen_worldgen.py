"""Generate Karstscape worldgen JSON from tools/worldgen/karst_params.toml + the vanilla 26.3 export.

  python tools/gen_worldgen.py           # write generated files
  python tools/gen_worldgen.py --check   # exit 1 if any generated file is missing or differs

Generated (do not edit by hand; edit karst_params.toml and rerun). Everything under src/main/resources/data
and assets/karstscape/lang is generated:
  data/karstscape/dimension_type/overworld.json          (taller overworld, [world])
  data/karstscape/worldgen/biome/tower_karst.json
  data/karstscape/worldgen/density_function/<preset>/{mask,offset,factor,jaggedness,...}.json
  data/karstscape/worldgen/noise_settings/<preset>.json
  data/karstscape/worldgen/world_preset/{classic,<preset>}.json
  data/minecraft/worldgen/world_preset/normal.json       (the default world type -> presets.overworld)
  data/minecraft/tags/worldgen/world_preset/normal.json   (adds our presets to the World Type button)
  assets/karstscape/lang/{en_us,zh_cn}.json

Large-scale terrain (v0.2, [terrain]): the vanilla climate noises continents/erosion/ridges are copied into
karstscape:terrain/ with a smaller xz_scale (larger continents and ranges; biomes follow because they are
picked from the same router values), and the vanilla offset/factor/jaggedness splines are copied there with
their values transformed (taller mountains, deeper oceans). Every preset builds on karstscape:terrain/*.

How the region works (one parameter box per preset, used twice):
  * biome: the box is carved out of every vanilla multi-noise entry (reports/biome_parameters) and filled
    with karstscape:tower_karst, so the biome is exactly the box;
  * terrain: a 2D mask (1 inside the box minus `edge`, 0 at its boundary) blends vanilla `offset`,
    `factor` and `jaggedness` towards a flat plain. Every vanilla density function that depends on them
    (depth, sloped_cheese, final_density, preliminary_surface_level, chunk_surface_level) is copied into
    karstscape:<preset>/ with references rewritten, so biome depth, aquifers and surface rules all see the
    same flattened terrain. Outside the mask the terrain is the large-scale terrain above.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ks_common as ks  # noqa: E402

PARAMS = ks.ROOT / "tools" / "worldgen" / "karst_params.toml"
VANILLA = ks.REFERENCE_DIR / "vanilla-26.3"
VWG = VANILLA / "data" / "minecraft" / "worldgen"
RES = ks.ROOT / "src" / "main" / "resources"
OUT_WG = RES / "data" / "karstscape" / "worldgen"

NS = "karstscape"
TERRAIN = f"{NS}:terrain"
BIOME_ID = f"{NS}:tower_karst"
CLIMATE = ["temperature", "humidity", "continentalness", "erosion", "weirdness"]
# router density function behind each climate parameter (NoiseRouter.createClimateSampler)
CLIMATE_DF = {
    "temperature": "minecraft:overworld/temperature",
    "humidity": "minecraft:overworld/vegetation",
    "continentalness": f"{TERRAIN}/continents",
    "erosion": f"{TERRAIN}/erosion",
    "weirdness": f"{TERRAIN}/ridges",
}
SHAPERS = ["offset", "factor", "jaggedness"]
STRETCHED = ["continents", "erosion", "ridges"]  # climate noises copied with a larger horizontal scale
# vanilla overworld functions replaced by karstscape:terrain/* copies (in every preset)
TERRAIN_MAP = {f"minecraft:overworld/{n}": f"{TERRAIN}/{n}"
               for n in STRETCHED + ["ridges_folded"] + SHAPERS}
LIMIT = 2.0  # multi-noise parameter range is [-2, 2]


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def dumps(obj, compact: bool = False) -> str:
    if compact:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ box ----

def preset_box(cfg: dict) -> dict[str, tuple[float, float, bool, bool]]:
    """param -> (lo, hi, lo_closed, hi_closed). A bound at ±1 is open and widened to ±2."""
    box = {}
    for p in CLIMATE:
        lo, hi = (float(x) for x in cfg[p])
        if not lo < hi:
            raise SystemExit(f"bad range for {p}: {cfg[p]}")
        lo_closed, hi_closed = lo > -1.0, hi < 1.0
        box[p] = (lo if lo_closed else -LIMIT, hi if hi_closed else LIMIT, lo_closed, hi_closed)
    return box


def as_range(v) -> tuple[float, float]:
    return (float(v), float(v)) if not isinstance(v, list) else (float(v[0]), float(v[1]))


def carve(entry_params: dict, box: dict) -> list[dict]:
    """entry minus box, as a list of parameter dicts (empty if the entry lies inside the box)."""
    rng = {p: as_range(entry_params[p]) for p in CLIMATE}
    if any(rng[p][1] <= box[p][0] or rng[p][0] >= box[p][1] for p in CLIMATE):
        return [entry_params]  # no overlap with positive volume
    pieces = []
    cur = dict(rng)
    for p in CLIMATE:
        lo, hi = cur[p]
        blo, bhi = box[p][0], box[p][1]
        if lo < blo:
            piece = dict(cur)
            piece[p] = (lo, blo)
            pieces.append(piece)
        if hi > bhi:
            piece = dict(cur)
            piece[p] = (bhi, hi)
            pieces.append(piece)
        cur[p] = (max(lo, blo), min(hi, bhi))
    out = []
    for piece in pieces:
        params = {k: v for k, v in entry_params.items()}
        for p in CLIMATE:
            params[p] = [piece[p][0], piece[p][1]]
        out.append(params)
    return out


def biome_entries(box: dict) -> list[dict]:
    vanilla = load(VANILLA / "reports" / "biome_parameters" / "minecraft" / "overworld.json")["biomes"]
    out = []
    for e in vanilla:
        depth = as_range(e["parameters"]["depth"])
        # only surface (0) and deep (1) entries are replaced; cave biomes (depth 0.2..0.9, 1.1) are kept
        if depth in ((0.0, 0.0), (1.0, 1.0)):
            for params in carve(e["parameters"], box):
                out.append({"biome": e["biome"], "parameters": params})
        else:
            out.append(e)
    for depth in (0.0, 1.0):
        params = {p: [box[p][0], box[p][1]] for p in CLIMATE}
        params["depth"] = depth
        params["offset"] = 0.0
        out.append({"biome": BIOME_ID, "parameters": params})
    return out


# ------------------------------------------------------------ densities ----

def df_add(a, b):
    return {"type": "minecraft:add", "left": a, "right": b}


def df_mul(a, b):
    return {"type": "minecraft:mul", "left": a, "right": b}


def df_clamp01(x):
    return {"type": "minecraft:clamp", "input": x, "min": 0.0, "max": 1.0}


def mask_function(box: dict, edge: float) -> dict:
    """Product over closed sides of linear ramps 0 (at the box boundary) -> 1 (edge inside)."""
    factors = []
    for p in CLIMATE:
        lo, hi, lo_closed, hi_closed = box[p]
        if lo_closed:
            factors.append(df_clamp01(df_mul(df_add(CLIMATE_DF[p], -lo), 1.0 / edge)))
        if hi_closed:
            factors.append(df_clamp01(df_mul(df_add(CLIMATE_DF[p], -hi), -1.0 / edge)))
    if not factors:
        return 1.0
    acc = factors[0]
    for f in factors[1:]:
        acc = df_mul(acc, f)
    return {"type": "minecraft:cache", "input": acc}


WORLD: dict = {}  # [world] from karst_params.toml, set in build()


def world_top() -> int:
    """First y above the world (vanilla 320)."""
    return int(WORLD["min_y"]) + int(WORLD["height"])


def y_to_offset(y: float) -> float:
    """Inverse of depth = (depth_zero_y - y) / depth_blocks + offset: the offset that puts the surface at y.

    Vanilla: zero 128, 128 blocks per unit (gradient -64 -> 1.5, 320 -> -1.5)."""
    return (y - float(WORLD["depth_zero_y"])) / float(WORLD["depth_blocks"])


def depth_gradient() -> dict:
    """The y part of `depth` over the whole world height (gradient clamps outside its two coordinates)."""
    lo, hi = int(WORLD["min_y"]), world_top()
    return {"type": "minecraft:gradient", "axis": "y", "from_coordinate": lo, "from_value": -y_to_offset(lo),
            "to_coordinate": hi, "to_value": -y_to_offset(hi)}


def retarget_heights(obj, counts: dict):
    """Rewrite the vanilla height-tied constants of a copied overworld density function for [world].

    * the depth gradient (-64 -> 1.5, 320 -> -1.5) spans the whole world (it clamps at its ends);
    * the top slide (fade to air from 240 to 256) moves to [world].top_slide;
    * find_top_surface searches from the world top (vanilla clamps the upper bound to 320).
    """
    if isinstance(obj, list):
        return [retarget_heights(v, counts) for v in obj]
    if not isinstance(obj, dict):
        return obj
    obj = {k: retarget_heights(v, counts) for k, v in obj.items()}
    if obj.get("type") == "minecraft:gradient" and obj.get("axis") == "y":
        key = (obj["from_coordinate"], obj["from_value"], obj["to_coordinate"], obj["to_value"])
        if key == (-64, 1.5, 320, -1.5):
            counts["depth"] = counts.get("depth", 0) + 1
            return depth_gradient()
        if key == (240, 1.0, 256, 0.0):
            counts["top_slide"] = counts.get("top_slide", 0) + 1
            a, b = (int(v) for v in WORLD["top_slide"])
            return dict(obj, from_coordinate=a, to_coordinate=b)
    if obj.get("type") == "minecraft:clamp" and obj.get("min") == -40.0 and obj.get("max") == 320.0:
        counts["surface_search"] = counts.get("surface_search", 0) + 1
        return dict(obj, max=float(world_top()))
    return obj


# expected rewrites over all copied dependents (vanilla 26.3: depth + preliminary_surface_level, ...)
RETARGET_EXPECTED = {"depth": 2, "top_slide": 2, "surface_search": 1}


def piecewise(points: list) -> tuple:
    """Piecewise-linear f and f' through [[x, y], ...] (sorted by x), extrapolated with the end slopes."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2 or any(b[0] <= a[0] for a, b in zip(pts, pts[1:])):
        raise SystemExit(f"curve needs >= 2 points with increasing x: {points}")

    def seg(x):
        for k in range(1, len(pts) - 1):
            if x < pts[k][0]:
                return k - 1
        return len(pts) - 2

    def slope(x):
        k = seg(x)
        (x0, y0), (x1, y1) = pts[k], pts[k + 1]
        return (y1 - y0) / (x1 - x0)

    def f(x):
        k = seg(x)
        return pts[k][1] + (x - pts[k][0]) * slope(x)

    return f, slope


def spline_leaves(sp: dict) -> list[float]:
    out = []
    for pt in sp["points"]:
        v = pt["value"]
        out += spline_leaves(v) if isinstance(v, dict) else [float(v)]
    return out


def map_spline(sp: dict, f, df) -> None:
    """In place: leaf values v -> f(v); derivatives scaled by df at the point value (mean leaf if nested)."""
    for pt in sp["points"]:
        v = pt["value"]
        if isinstance(v, dict):
            leaves = spline_leaves(v)
            pt["derivative"] = round(pt["derivative"] * df(sum(leaves) / len(leaves)), 6)
            map_spline(v, f, df)
        else:
            pt["derivative"] = round(pt["derivative"] * df(float(v)), 6)
            pt["value"] = round(f(float(v)), 6)


def the_spline(df_json: dict) -> dict:
    """The single top-level minecraft:spline node of a vanilla shaper (its `spline` object)."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "minecraft:spline":
                found.append(o["spline"])
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(df_json)
    if len(found) != 1:
        raise SystemExit("unexpected vanilla shaper shape; update the_spline()")
    return found[0]


OFFSET_SEA = -0.50375  # vanilla offset = add(-0.50375, spline): spline value 0 is ~ sea level


def offset_curve(t: dict) -> tuple:
    """f, f' on spline values: vanilla height above sea (128 * value) -> [terrain].offset_curve -> value."""
    blocks = float(WORLD["depth_blocks"])
    f, df = piecewise(t["offset_curve"])
    return (lambda v: f(v * blocks) / blocks), (lambda v: df(v * blocks))


def terrain_functions(t: dict) -> dict[str, dict]:
    """karstscape:terrain/*: stretched climate noises and transformed shaper splines."""
    out = {}
    for n in STRETCHED:
        fn = load(VWG / "density_function" / "overworld" / f"{n}.json")
        noise = fn["input"] if fn.get("type") == "minecraft:cache" else fn
        if noise.get("type") != "minecraft:noise":
            raise SystemExit(f"unexpected vanilla overworld/{n} shape; update terrain_functions()")
        noise["xz_scale"] = noise["xz_scale"] / float(t[f"{n}_stretch"])
        bias = float(t.get(f"{n}_bias", 0.0))
        if bias:  # keep the cache outermost (the router and splines read the cached value)
            fn = {"type": "minecraft:cache", "input": df_add(noise, bias)}
        out[n] = fn
    out["ridges_folded"] = rewrite_refs(load(VWG / "density_function" / "overworld" / "ridges_folded.json"),
                                        TERRAIN_MAP)
    f, df = offset_curve(t)
    for n, (fv, dfv) in {
        "offset": (f, df),
        "factor": ((lambda v: v * float(t["factor_scale"])), (lambda v: 1.0)),
        "jaggedness": ((lambda v: v * float(t["jaggedness_scale"])), (lambda v: 1.0)),
    }.items():
        fn = rewrite_refs(load(VWG / "density_function" / "overworld" / f"{n}.json"), TERRAIN_MAP)
        map_spline(the_spline(fn), fv, dfv)
        out[n] = fn
    return out


def dimension_type() -> dict:
    dt = load(VANILLA / "data" / "minecraft" / "dimension_type" / "overworld.json")
    dt["min_y"] = int(WORLD["min_y"])
    dt["height"] = int(WORLD["height"])
    dt["logical_height"] = int(WORLD["height"])
    dt["attributes"]["minecraft:visual/cloud_height"] = float(WORLD["cloud_height"])
    if dt["height"] % 16 or dt["min_y"] % 16:
        raise SystemExit("[world] min_y and height must be multiples of 16 (DimensionType)")
    return dt


RIDGES = f"{TERRAIN}/ridges"


def abs_ridges_ramp(start: float, width: float) -> dict:
    """0 where |ridges| <= start, rising linearly to 1 at start + width."""
    return df_clamp01(df_mul(df_add({"type": "minecraft:abs", "input": RIDGES}, -start), 1.0 / width))


def shaper_functions(preset: str, plain: dict, river: dict) -> dict[str, dict]:
    mask = f"{NS}:{preset}/mask"
    cap_y_off = y_to_offset(plain["cap_y"])
    cap = df_add(cap_y_off,
                 df_mul({"type": "minecraft:noise", "noise": plain["cap_noise"],
                         "xz_scale": plain["cap_noise_xz_scale"], "y_scale": 0.0},
                        plain["cap_noise_blocks"] / 128.0))
    # river channel: bed_y in the corridor centre, rising to just above the plain over the bank width
    bed = y_to_offset(river["bed_y"])
    above_plain = cap_y_off + (plain["cap_noise_blocks"] + 4) / 128.0
    channel = df_add(bed, df_mul(abs_ridges_ramp(river["center"], river["bank"]), above_plain - bed))
    lowered = {"type": "minecraft:min",
               "left": {"type": "minecraft:min", "left": "minecraft:overworld/offset", "right": cap},
               "right": channel}
    # shallow: inside the corridor the bed is never below bed_y (vanilla river beds are deeper);
    # outside the corridor the floor drops away and has no effect (lakes keep their depth)
    floor = df_add(bed, df_mul(abs_ridges_ramp(river["center"] + river["bank"], 0.02), -4.0))
    plain_terrain = {"type": "minecraft:max", "left": lowered, "right": floor}
    return {
        "offset": {"type": "minecraft:cache", "input": {
            "type": "minecraft:lerp", "alpha": mask, "first": f"{TERRAIN}/offset",
            "second": rewrite_refs(plain_terrain, TERRAIN_MAP)}},
        "factor": {"type": "minecraft:cache", "input": {
            "type": "minecraft:lerp", "alpha": mask, "first": f"{TERRAIN}/factor",
            "second": {"type": "minecraft:max", "left": f"{TERRAIN}/factor",
                       "right": float(plain["min_factor"])}}},
        "jaggedness": {"type": "minecraft:cache", "input": {
            "type": "minecraft:lerp", "alpha": mask, "first": f"{TERRAIN}/jaggedness", "second": 0.0}},
    }


SPAWN_KEYS = {"temperature": "minecraft:overworld/temperature", "humidity": "minecraft:overworld/vegetation",
              "continentalness": "minecraft:overworld/continents", "erosion": "minecraft:overworld/erosion",
              "weirdness": "minecraft:overworld/ridges"}


def spawn_target(vanilla: list[dict], box: dict, preset: str, spawn: dict) -> list[dict]:
    """Spawn search ranges that keep the world spawn off karst towers.

    Entries are keyed by density function id: read with the vanilla ids, written with ours (CLIMATE_DF)."""
    if preset == "karst_only":
        lo, hi = spawn["karst_only_continentalness"]
        out = []
        for e in vanilla:
            e = {TERRAIN_MAP.get(k, k): v for k, v in e.items()}
            e[CLIMATE_DF["continentalness"]] = [float(lo), float(hi)]
            out.append(e)
        return out
    out = []
    for e in vanilla:
        params = {p: e.get(SPAWN_KEYS[p], [-LIMIT, LIMIT]) for p in CLIMATE}
        for piece in carve(params, box):
            out.append({CLIMATE_DF[p]: [float(piece[p][0]), float(piece[p][1])] for p in CLIMATE})
    return out


def dependents_of_shapers() -> list[str]:
    """Vanilla overworld density functions that (transitively) reference offset/factor/jaggedness."""
    base = VWG / "density_function"
    refs = {}
    for p in base.rglob("*.json"):
        name = "minecraft:" + p.relative_to(base).with_suffix("").as_posix()
        refs[name] = set(re.findall(r'"(minecraft:overworld/[a-z0-9_/]+)"', p.read_text(encoding="utf-8")))
    closure = {f"minecraft:overworld/{s}" for s in SHAPERS}
    changed = True
    while changed:
        changed = False
        for n, r in refs.items():
            if n not in closure and r & closure:
                closure.add(n)
                changed = True
    return sorted(n for n in closure if n.split("/", 1)[1] not in SHAPERS)


# ------------------------------------------------------------- materials ---

def blk(block: str) -> dict:
    return {"type": "minecraft:block", "result_state": block}


def cond(if_true, then_run) -> dict:
    return {"type": "minecraft:condition", "if_true": if_true, "then_run": then_run}


def y_above(y: int) -> dict:
    return {"type": "minecraft:y_above", "anchor": {"absolute": int(y)}, "surface_depth_multiplier": 0,
            "add_stone_depth": False}


def noise_above(noise: str, threshold: float, is_3d: bool = False) -> dict:
    c = {"type": "minecraft:noise_threshold", "noise": noise, "min_threshold": float(threshold),
         "max_threshold": 1000.0}
    if is_3d:
        c["is_3d"] = True
    return c


def noise_below(noise: str, threshold: float) -> dict:
    return {"type": "minecraft:noise_threshold", "noise": noise, "min_threshold": -1000.0,
            "max_threshold": float(threshold)}


STEEP = {"type": "minecraft:steep"}
UNDERWATER = {"type": "minecraft:not", "invert": "minecraft:not_underwater"}


def band_rule(m: dict) -> dict:
    """Horizontal limestone-like bands by absolute y (deterministic pattern from band_seed)."""
    import random
    rnd = random.Random(int(m["band_seed"]))
    blocks = [b for b, _ in m["bands"]]
    weights = [w for _, w in m["bands"]]
    starts, y = [], int(m["tower_y"]) - 24
    while y < int(m["band_top"]):
        starts.append(y)
        y += rnd.randint(int(m["band_min"]), int(m["band_max"]))
    # block shares follow the weights exactly (proportional pool, shuffled), not just in expectation
    total = sum(weights)
    pool = []
    for b, w in zip(blocks, weights):
        pool += [b] * round(len(starts) * w / total)
    pool += [blocks[0]] * (len(starts) - len(pool))
    rnd.shuffle(pool)
    bands = list(zip(starts, pool[:len(starts)]))

    # balanced binary tree of y_above tests: ~log2(n) checks per block instead of up to n
    def tree(lo: int, hi: int, below):
        """bands[lo:hi]; `below` is the rule for y under bands[lo]."""
        if lo >= hi:
            return below
        if hi - lo == 1:
            return {"type": "minecraft:sequence", "sequence": [cond(y_above(bands[lo][0]), blk(bands[lo][1])), below]}
        mid = (lo + hi) // 2
        return {"type": "minecraft:sequence", "sequence": [
            cond(y_above(bands[mid][0]), tree(mid, hi, blk(bands[mid][1]))),
            tree(lo, mid, below)]}

    return tree(0, len(bands), blk("minecraft:stone"))


def karst_surface_rule(m: dict) -> dict:
    bands = band_rule(m)
    moss = m["moss_noise"]
    tower = {"type": "minecraft:sequence", "sequence": [
        cond("minecraft:on_floor", {"type": "minecraft:sequence", "sequence": [
            cond(STEEP, bands),
            cond(noise_above(moss, m["moss_top_threshold"]), blk("minecraft:moss_block")),
            blk("minecraft:grass_block"),
        ]}),
        cond("minecraft:under_floor", {"type": "minecraft:sequence", "sequence": [
            cond(STEEP, bands),
            blk("minecraft:dirt"),
        ]}),
        # 2D noise (evaluated once per column, not per block): vertical moss streaks down the walls
        cond(noise_above(moss, m["moss_wall_threshold"]), blk("minecraft:moss_block")),
        bands,
    ]}
    rb = m["riverbed_noise"]
    riverbed = cond(UNDERWATER, {"type": "minecraft:sequence", "sequence": [
        cond("minecraft:on_floor", {"type": "minecraft:sequence", "sequence": [
            cond(noise_below(rb, -0.3), blk("minecraft:clay")),
            cond(noise_above(rb, 0.35), blk("minecraft:sand")),
            blk("minecraft:gravel"),
        ]}),
        cond("minecraft:under_floor", blk("minecraft:gravel")),
    ]})
    foot = cond(y_above(m["foot_y"]), cond("minecraft:on_floor", cond(STEEP, {"type": "minecraft:sequence", "sequence": [
        cond(noise_above(moss, 0.0), blk("minecraft:coarse_dirt")),
        blk("minecraft:gravel"),
    ]})))
    return {"type": "minecraft:sequence", "sequence": [cond(y_above(m["tower_y"]), tower), riverbed, foot]}


def overworld_material_rule() -> dict:
    """Vanilla overworld material rule with the karst surface rule tried first inside the karst biome."""
    rule = load(VWG / "material_rule" / "overworld.json")
    hits = 0

    def walk(node):
        nonlocal hits
        if isinstance(node, dict):
            if node.get("then_run") == "minecraft:overworld/surface":
                node["then_run"] = {"type": "minecraft:sequence", "sequence": [
                    cond({"type": "minecraft:biome", "biome_is": BIOME_ID}, f"{NS}:karst_surface"),
                    "minecraft:overworld/surface"]}
                hits += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(rule)
    if hits != 1:
        raise SystemExit("unexpected vanilla material_rule/overworld.json shape; update overworld_material_rule()")
    return rule


# ------------------------------------------------------------ vegetation ---

def apply_biome_extras(b: dict, params: dict, files: dict) -> None:
    bp, v = params["biome"], params["vegetation"]
    remove = set(bp.get("remove_features", []))
    b["features"] = [[f for f in step if f not in remove] for step in b["features"]]
    attrs = b.setdefault("attributes", {})
    attrs["minecraft:visual/fog_color"] = bp["fog_color"]
    attrs["minecraft:visual/sky_color"] = bp["sky_color"]
    attrs["minecraft:visual/water_fog_color"] = bp["water_fog_color"]
    b["effects"]["water_color"] = bp["water_color"]

    pf, fd = OUT_WG / "placed_feature", OUT_WG / "feature"
    in_biome = {"type": "minecraft:biome"}
    square = {"type": "minecraft:in_square"}
    survive_sapling = {"type": "minecraft:block_predicate_filter",
                       "predicate": {"type": "minecraft:would_survive", "state": "minecraft:oak_sapling"}}
    lake = load(VWG / "feature" / "lake_lava.json")
    lake["fluid"] = {"id": "minecraft:water", "properties": {"level": "0"}}
    lake["barrier"] = {"id": "minecraft:dirt"}
    files[fd / "pond.json"] = dumps(lake)
    placed = {
        "pond": {"feature": f"{NS}:pond", "placement": [
            {"type": "minecraft:rarity_filter", "chance": int(v["pond_rarity"])}, square,
            {"type": "minecraft:heightmap", "heightmap": "WORLD_SURFACE_WG"}, in_biome]},
        "tower_vines": {"feature": "minecraft:vines", "placement": [
            {"type": "minecraft:count", "count": int(v["vine_count"])}, square,
            {"type": "minecraft:height_range", "height": {"type": "minecraft:uniform",
                                                          "min_inclusive": {"absolute": 66},
                                                          "max_inclusive": {"absolute": 240}}}, in_biome]},
        "bamboo": {"feature": "minecraft:bamboo_no_podzol", "placement": [
            {"type": "minecraft:rarity_filter", "chance": int(v["bamboo_rarity"])}, square,
            {"type": "minecraft:heightmap", "heightmap": "MOTION_BLOCKING"}, in_biome]},
        "azalea_trees": {"feature": "minecraft:azalea_tree", "placement": [
            {"type": "minecraft:count", "count": int(v["azalea_count"])}, square,
            {"type": "minecraft:surface_water_depth_filter", "max_water_depth": 0},
            {"type": "minecraft:heightmap", "heightmap": "OCEAN_FLOOR"}, survive_sapling, in_biome]},
        "jungle_bushes": {"feature": "minecraft:jungle_bush", "placement": [
            {"type": "minecraft:count", "count": int(v["jungle_bush_count"])}, square,
            {"type": "minecraft:surface_water_depth_filter", "max_water_depth": 0},
            {"type": "minecraft:heightmap", "heightmap": "OCEAN_FLOOR"}, survive_sapling, in_biome]},
    }
    for name, obj in placed.items():
        files[pf / f"{name}.json"] = dumps(obj)
    # karstscape features are unique to this biome, so appending them cannot create a feature-order cycle
    b["features"][1].append(f"{NS}:pond")
    b["features"][9].extend([f"{NS}:azalea_trees", f"{NS}:jungle_bushes", f"{NS}:bamboo", f"{NS}:tower_vines"])


def add_towers(final_density: dict, towers_ref: str) -> dict:
    """vanilla: add(terrain, beardifier) -> add(max(terrain, towers), beardifier).

    The tower branch sits outside `interpolated`, so it is evaluated per block (vertical walls)."""
    fd = copy.deepcopy(final_density)
    if not (fd.get("type") == "minecraft:add" and fd.get("right") == {"type": "minecraft:beardifier"}):
        raise SystemExit("unexpected vanilla final_density shape; update add_towers()")
    fd["left"] = {"type": "minecraft:max", "left": fd["left"], "right": towers_ref}
    return fd


def rewrite_refs(obj, mapping: dict[str, str]):
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [rewrite_refs(v, mapping) for v in obj]
    if isinstance(obj, dict):
        return {k: rewrite_refs(v, mapping) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- build ----

def build(params: dict) -> dict[Path, str]:
    files: dict[Path, str] = {}
    WORLD.clear()
    WORLD.update(params["world"])
    plain = params["plain"]
    files[RES / "data" / NS / "dimension_type" / "overworld.json"] = dumps(dimension_type())
    for name, fn in terrain_functions(params["terrain"]).items():
        files[OUT_WG / "density_function" / "terrain" / f"{name}.json"] = dumps(fn)
    deps = dependents_of_shapers()

    # biome (copy of the template biome with our climate values; vegetation etc. come in M3)
    b = load(VWG / "biome" / f"{params['biome']['template']}.json")
    b["temperature"] = float(params["biome"]["temperature"])
    b["downfall"] = float(params["biome"]["downfall"])
    if not params["biome"].get("carvers", True):
        b["carvers"] = []  # vanilla cave/canyon carvers would bore holes and overhangs into the towers
    apply_biome_extras(b, params, files)
    files[OUT_WG / "material_rule" / "karst_surface.json"] = dumps(karst_surface_rule(params["material"]))
    files[OUT_WG / "material_rule" / "overworld.json"] = dumps(overworld_material_rule())
    files[OUT_WG / "biome" / "tower_karst.json"] = dumps(b)

    presets = params["presets"]
    normal_tag = []
    lang_en = {"biome.karstscape.tower_karst": "Tower Karst"}
    lang_zh = {"biome.karstscape.tower_karst": "峰林"}

    # classic: the vanilla default world, unchanged (comparison and benchmarks)
    files[OUT_WG / "world_preset" / "classic.json"] = dumps(load(VWG / "world_preset" / "normal.json"))
    normal_tag.append(f"{NS}:classic")
    lang_en[f"generator.{NS}.classic"] = params["world"]["classic_en"]
    lang_zh[f"generator.{NS}.classic"] = params["world"]["classic_zh"]

    for preset, cfg in presets.items():
        box = preset_box(cfg)
        edge = float(cfg["edge"])
        mapping = dict(TERRAIN_MAP)  # stretched climate everywhere ...
        mapping.update({f"minecraft:overworld/{s}": f"{NS}:{preset}/{s}" for s in SHAPERS})  # ... karst shapers
        mapping.update({d: f"{NS}:{preset}/{d.split('/', 1)[1]}" for d in deps})

        dfdir = OUT_WG / "density_function" / preset
        files[dfdir / "mask.json"] = dumps(mask_function(box, edge))
        for name, fn in shaper_functions(preset, plain, params["river"]).items():
            files[dfdir / f"{name}.json"] = dumps(fn)
        rv = params["river"]
        files[dfdir / "tower_mask.json"] = dumps({"type": "minecraft:cache", "input": df_mul(
            f"{NS}:{preset}/mask", abs_ridges_ramp(rv["tower_clear"], rv["tower_clear_ramp"]))})
        towers = {"type": f"{NS}:tower_field", "mask": f"{NS}:{preset}/tower_mask"}
        towers.update(params["towers"])
        files[dfdir / "towers.json"] = dumps(towers)
        counts: dict[str, int] = {}
        for d in deps:
            rel = d.split(":", 1)[1]  # overworld/...
            src = rewrite_refs(load(VWG / "density_function" / f"{rel}.json"), mapping)
            src = retarget_heights(src, counts)
            if rel == "overworld/final_density":
                src = add_towers(src, f"{NS}:{preset}/towers")
            files[dfdir / f"{rel.split('/', 1)[1]}.json"] = dumps(src)
        if counts != RETARGET_EXPECTED:
            raise SystemExit(f"unexpected vanilla height constants {counts}; update retarget_heights()")

        ns = load(VWG / "noise_settings" / "overworld.json")
        ns = rewrite_refs(ns, mapping)
        ns["noise"] = {"height": int(WORLD["height"]), "min_y": int(WORLD["min_y"])}
        ns["material_rule"] = f"{NS}:overworld"  # vanilla overworld rule + karst surface first in tower_karst
        ns["spawn_target"] = spawn_target(ns["spawn_target"], box, preset, params["spawn"])
        files[OUT_WG / "noise_settings" / f"{preset}.json"] = dumps(ns)

        wp = load(VWG / "world_preset" / "normal.json")
        ow = wp["dimensions"]["minecraft:overworld"]
        ow["type"] = f"{NS}:overworld"
        ow["generator"]["biome_source"] = {"type": "minecraft:multi_noise", "biomes": biome_entries(box)}
        ow["generator"]["settings"] = f"{NS}:{preset}"
        files[preset_path(cfg, preset)] = dumps(wp, compact=True)

        if cfg.get("world_preset"):
            continue  # replaces a vanilla preset: already in the tag, keeps its vanilla name
        normal_tag.append(f"{NS}:{preset}")
        lang_en[f"generator.{NS}.{preset}"] = cfg["display_en"]
        lang_zh[f"generator.{NS}.{preset}"] = cfg["display_zh"]

    # structures: only underground ones in the karst biome (surface ones would land on towers, and the
    # beardifier would carve their footprint into tower walls)
    for tag in params["structures"]["allow"]:
        files[RES / "data" / "minecraft" / "tags" / "worldgen" / "biome" / f"{tag}.json"] = \
            dumps({"replace": False, "values": [BIOME_ID]})
    files[RES / "data" / "minecraft" / "tags" / "worldgen" / "world_preset" / "normal.json"] = \
        dumps({"replace": False, "values": normal_tag})
    files[RES / "assets" / NS / "lang" / "en_us.json"] = dumps(lang_en)
    files[RES / "assets" / NS / "lang" / "zh_cn.json"] = dumps(lang_zh)
    return files


def preset_path(cfg: dict, preset: str) -> Path:
    """World preset file of a preset: its own id, or a vanilla id it replaces (world_preset = "minecraft:x")."""
    if cfg.get("world_preset"):
        ns, path = cfg["world_preset"].split(":", 1)
        return RES / "data" / ns / "worldgen" / "world_preset" / f"{path}.json"
    return OUT_WG / "world_preset" / f"{preset}.json"


def generated_roots() -> list[Path]:
    return [RES / "data", RES / "assets" / NS / "lang"]


def main() -> int:
    ks.setup_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only verify that generated files are current")
    args = ap.parse_args()
    if not (VANILLA / "MANIFEST.json").is_file():
        print("ERROR: reference/vanilla-26.3 missing; run python tools/export_vanilla_data.py", file=sys.stderr)
        return 2
    params = tomllib.loads(PARAMS.read_text(encoding="utf-8"))
    files = build(params)

    # stale generated files (e.g. a removed preset) inside the fully generated folders
    existing = {p for root in generated_roots() if root.is_dir() for p in root.rglob("*.json")}
    stale = sorted(existing - set(files))

    if args.check:
        bad = [p for p, text in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
        for p in bad + stale:
            print(f"stale: {p.relative_to(ks.ROOT)}")
        if bad or stale:
            print("ERROR: generated worldgen files are out of date; run python tools/gen_worldgen.py")
            return 1
        print(f"ok: {len(files)} generated files are current")
        return 0

    for p in stale:
        p.unlink()
    changed = 0
    for p, text in sorted(files.items()):
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.is_file() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8", newline="\n")
            changed += 1
    for preset, cfg in params["presets"].items():
        wp = preset_path(cfg, preset)
        n = len(json.loads(wp.read_text(encoding="utf-8"))["dimensions"]["minecraft:overworld"]
                ["generator"]["biome_source"]["biomes"])
        print(f"{preset}: {n} biome entries, world_preset {wp.stat().st_size // 1024} KiB")
    print(f"wrote {changed} changed / {len(files)} generated files; removed {len(stale)} stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
