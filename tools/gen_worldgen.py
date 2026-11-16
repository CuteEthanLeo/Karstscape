"""Generate Karstscape worldgen JSON from tools/worldgen/karst_params.toml + the vanilla 26.3 export.

  python tools/gen_worldgen.py           # write generated files
  python tools/gen_worldgen.py --check   # exit 1 if any generated file is missing or differs

Generated (do not edit by hand; edit karst_params.toml and rerun):
  data/karstscape/worldgen/biome/tower_karst.json
  data/karstscape/worldgen/density_function/<preset>/{mask,offset,factor,jaggedness,...}.json
  data/karstscape/worldgen/noise_settings/<preset>.json
  data/karstscape/worldgen/world_preset/<preset>.json
  data/minecraft/tags/worldgen/world_preset/normal.json   (adds our presets to the World Type button)
  assets/karstscape/lang/{en_us,zh_cn}.json

How the region works (one parameter box per preset, used twice):
  * biome: the box is carved out of every vanilla multi-noise entry (reports/biome_parameters) and filled
    with karstscape:tower_karst, so the biome is exactly the box;
  * terrain: a 2D mask (1 inside the box minus `edge`, 0 at its boundary) blends vanilla `offset`,
    `factor` and `jaggedness` towards a flat plain. Every vanilla density function that depends on them
    (depth, sloped_cheese, final_density, preliminary_surface_level, chunk_surface_level) is copied into
    karstscape:<preset>/ with references rewritten, so biome depth, aquifers and surface rules all see the
    same flattened terrain. Outside the mask the terrain is bit-for-bit vanilla.
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
BIOME_ID = f"{NS}:tower_karst"
CLIMATE = ["temperature", "humidity", "continentalness", "erosion", "weirdness"]
# router density function behind each climate parameter (NoiseRouter.createClimateSampler)
CLIMATE_DF = {
    "temperature": "minecraft:overworld/temperature",
    "humidity": "minecraft:overworld/vegetation",
    "continentalness": "minecraft:overworld/continents",
    "erosion": "minecraft:overworld/erosion",
    "weirdness": "minecraft:overworld/ridges",
}
SHAPERS = ["offset", "factor", "jaggedness"]
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


def y_to_offset(y: float) -> float:
    """Inverse of overworld/depth: depth = gradient(y: -64 -> 1.5, 320 -> -1.5) + offset; surface at depth 0."""
    return (y + 64.0) / 128.0 - 1.5


def shaper_functions(preset: str, plain: dict) -> dict[str, dict]:
    mask = f"{NS}:{preset}/mask"
    cap = df_add(y_to_offset(plain["cap_y"]),
                 df_mul({"type": "minecraft:noise", "noise": plain["cap_noise"],
                         "xz_scale": plain["cap_noise_xz_scale"], "y_scale": 0.0},
                        plain["cap_noise_blocks"] / 128.0))
    return {
        "offset": {"type": "minecraft:cache", "input": {
            "type": "minecraft:lerp", "alpha": mask, "first": "minecraft:overworld/offset",
            "second": {"type": "minecraft:min", "left": "minecraft:overworld/offset", "right": cap}}},
        "factor": {"type": "minecraft:cache", "input": {
            "type": "minecraft:lerp", "alpha": mask, "first": "minecraft:overworld/factor",
            "second": {"type": "minecraft:max", "left": "minecraft:overworld/factor",
                       "right": float(plain["min_factor"])}}},
        "jaggedness": {"type": "minecraft:cache", "input": {
            "type": "minecraft:lerp", "alpha": mask, "first": "minecraft:overworld/jaggedness", "second": 0.0}},
    }


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
    seq = [cond(y_above(y0), blk(b)) for y0, b in reversed(bands)]
    seq.append(blk("minecraft:stone"))
    return {"type": "minecraft:sequence", "sequence": seq}


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
        cond(noise_above(moss, m["moss_wall_threshold"], is_3d=True), blk("minecraft:moss_block")),
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
    plain = params["plain"]
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
    for preset, cfg in presets.items():
        box = preset_box(cfg)
        edge = float(cfg["edge"])
        mapping = {f"minecraft:overworld/{s}": f"{NS}:{preset}/{s}" for s in SHAPERS}
        mapping.update({d: f"{NS}:{preset}/{d.split('/', 1)[1]}" for d in deps})

        dfdir = OUT_WG / "density_function" / preset
        files[dfdir / "mask.json"] = dumps(mask_function(box, edge))
        for name, fn in shaper_functions(preset, plain).items():
            files[dfdir / f"{name}.json"] = dumps(fn)
        towers = {"type": f"{NS}:tower_field", "mask": f"{NS}:{preset}/mask"}
        towers.update(params["towers"])
        files[dfdir / "towers.json"] = dumps(towers)
        for d in deps:
            rel = d.split(":", 1)[1]  # overworld/...
            src = rewrite_refs(load(VWG / "density_function" / f"{rel}.json"), mapping)
            if rel == "overworld/final_density":
                src = add_towers(src, f"{NS}:{preset}/towers")
            files[dfdir / f"{rel.split('/', 1)[1]}.json"] = dumps(src)

        ns = load(VWG / "noise_settings" / "overworld.json")
        ns = rewrite_refs(ns, mapping)
        ns["material_rule"] = f"{NS}:overworld"  # vanilla overworld rule + karst surface first in tower_karst
        files[OUT_WG / "noise_settings" / f"{preset}.json"] = dumps(ns)

        wp = load(VWG / "world_preset" / "normal.json")
        ow = wp["dimensions"]["minecraft:overworld"]
        ow["generator"]["biome_source"] = {"type": "minecraft:multi_noise", "biomes": biome_entries(box)}
        ow["generator"]["settings"] = f"{NS}:{preset}"
        files[OUT_WG / "world_preset" / f"{preset}.json"] = dumps(wp, compact=True)

        normal_tag.append(f"{NS}:{preset}")
        lang_en[f"generator.{NS}.{preset}"] = cfg["display_en"]
        lang_zh[f"generator.{NS}.{preset}"] = cfg["display_zh"]

    files[RES / "data" / "minecraft" / "tags" / "worldgen" / "world_preset" / "normal.json"] = \
        dumps({"replace": False, "values": normal_tag})
    files[RES / "assets" / NS / "lang" / "en_us.json"] = dumps(lang_en)
    files[RES / "assets" / NS / "lang" / "zh_cn.json"] = dumps(lang_zh)
    return files


def generated_roots() -> list[Path]:
    return [OUT_WG]


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

    # stale generated files (e.g. a removed preset) inside the fully generated folder
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
    for preset in params["presets"]:
        wp = OUT_WG / "world_preset" / f"{preset}.json"
        n = len(json.loads(wp.read_text(encoding="utf-8"))["dimensions"]["minecraft:overworld"]
                ["generator"]["biome_source"]["biomes"])
        print(f"{preset}: {n} biome entries, world_preset {wp.stat().st_size // 1024} KiB")
    print(f"wrote {changed} changed / {len(files)} generated files; removed {len(stale)} stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
