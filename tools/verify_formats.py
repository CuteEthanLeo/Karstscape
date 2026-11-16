"""Verify SPEC §2.1 worldgen-format claims against the LOCAL vanilla 26.3 export.

Reads  reference/vanilla-26.3/ (required) and reference/vanilla-1.21.11-shipped/ (optional, old format).
Writes docs/vanilla-26.3-format-check.md. Exit code 1 if any claim is a mismatch.

  python tools/verify_formats.py [--sources PATH]

--sources: a Minecraft 26.3 sources jar or directory (Loom genSources output). Defaults to the
project's .gradle/loom-cache genSources jar if present; claim C4 then quotes the ConstantFunction codec.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ks_common as ks  # noqa: E402

NEW = ks.REFERENCE_DIR / "vanilla-26.3"
OLD = ks.REFERENCE_DIR / "vanilla-1.21.11-shipped"
OUT = ks.ROOT / "docs" / "vanilla-26.3-format-check.md"
WG = "data/minecraft/worldgen"

VERIFIED, MISMATCH, PARTIAL = "已核实", "不一致", "部分核实"
BINARY_DF = ["add", "sub", "mul", "div", "min", "max"]
UNARY_DF = ["abs", "square", "cube", "half_negative", "quarter_negative", "squeeze", "negate", "cache",
            "blend_density", "sqrt", "log", "sign", "reciprocal"]
EXPECTED_ROUTER = {"chunk_surface_level", "continents", "depth", "erosion", "final_density",
                   "ridges", "temperature", "vegetation"}
EXPECTED_AQUIFERS = {"barrier", "exclusion", "fluid_level_floodedness", "fluid_level_spread", "lava",
                     "surface_level"}


def rel(p: Path) -> str:
    return p.relative_to(ks.REFERENCE_DIR).as_posix()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def files(base: Path, sub: str) -> list[Path]:
    d = base / WG / sub
    return sorted(d.rglob("*.json")) if d.is_dir() else []


def res_path(base: Path, registry: str, ident: str) -> Path:
    ns, _, path = ident.partition(":")
    return base / "data" / ns / "worldgen" / registry / f"{path}.json"


def registry_entries(name: str) -> list[str]:
    reg = load(NEW / "reports" / "registries.json")
    return sorted(e.removeprefix("minecraft:") for e in reg.get(name, {}).get("entries", {}))


def walk(node, fn, path=()):
    fn(node, path)
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, fn, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, fn, path + (i,))


def code(s) -> str:
    return f"`{s}`"


def keys_str(keys) -> str:
    return ", ".join(code(k) for k in sorted(keys))


# ----------------------------------------------------------------- DF ----

def density_function_roots(base: Path):
    """Yield (source file, json root) for every density-function context in a data tree."""
    for p in files(base, "density_function"):
        yield p, load(p)
    for p in files(base, "noise_settings"):
        ns = load(p)
        yield p, ns.get("noise_router", {})
        if isinstance(ns.get("aquifers"), dict):
            yield p, ns["aquifers"]
        for d in ns.get("debug_functions", []) or []:
            if isinstance(d, dict):
                yield p, d.get("function")


def collect_df(base: Path):
    """type -> Counter(frozenset(arg keys)); also find any argument/argument1/argument2 keys."""
    by_type: dict[str, Counter] = defaultdict(Counter)
    bad_keys: Counter = Counter()
    constant_objects = 0
    bare_numbers = 0
    arg_keys = {"left", "right", "input", "alpha", "first", "second", "argument", "argument1", "argument2"}

    for _src, root in density_function_roots(base):
        def fn(node, path):
            nonlocal constant_objects, bare_numbers
            if isinstance(node, dict) and isinstance(node.get("type"), str):
                t = node["type"].removeprefix("minecraft:")
                ks_ = frozenset(k for k in node if k != "type")
                by_type[t][ks_] += 1
                if t == "constant":
                    constant_objects += 1
                for k in ("argument", "argument1", "argument2"):
                    if k in node:
                        bad_keys[k] += 1
                for k, v in node.items():
                    if k in arg_keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                        bare_numbers += 1
        walk(root, fn)
    return by_type, bad_keys, constant_objects, bare_numbers


# ------------------------------------------------------------- claims ----

def claim_c1():
    ev = []
    ok = True
    cf = NEW / WG / "configured_feature"
    feat = files(NEW, "feature")
    ok &= not cf.exists()
    ev.append(f"{code(rel(NEW / WG) + '/configured_feature/')} 不存在：{not cf.exists()}")
    ev.append(f"{code(rel(NEW / WG) + '/feature/')} 有 {len(feat)} 个文件")
    with_config = [p for p in feat if "config" in load(p)]
    without_type = [p for p in feat if "type" not in load(p)]
    ok &= not with_config and not without_type and len(feat) > 0
    ev.append(f"其中顶层带 `config` 的 {len(with_config)} 个，缺 `type` 的 {len(without_type)} 个")
    oak = NEW / WG / "feature" / "oak.json"
    if oak.is_file():
        ev.append(f"{code(rel(oak))} 顶层键：{keys_str(load(oak).keys())}")
    # placed_feature references
    placed = files(NEW, "placed_feature")
    resolved = unresolved = inline = 0
    for p in placed:
        f = load(p).get("feature")
        if isinstance(f, str):
            if res_path(NEW, "feature", f).is_file():
                resolved += 1
            else:
                unresolved += 1
        else:
            inline += 1
    ok &= unresolved == 0
    ev.append(f"`placed_feature/` {len(placed)} 个：引用 `feature/` 的 {resolved} 个全部可解析"
              f"（无法解析 {unresolved}，内联 {inline}）；`placed_feature` 结构仍是 `feature` + `placement`")
    carver_old = (NEW / WG / "configured_carver").exists()
    ev.append(f"顺带：`configured_carver/` 存在={carver_old}；`carver/` 有 {len(files(NEW, 'carver'))} 个文件")
    ft = registry_entries("minecraft:worldgen/feature_type")
    ev.append(f"`reports/registries.json` 里 feature 类型注册表名为 `minecraft:worldgen/feature_type`（{len(ft)} 项）")
    if OLD.is_dir():
        old_cf = files(OLD, "configured_feature")
        old_oak = OLD / WG / "configured_feature" / "oak.json"
        ev.append(f"对照 1.21.11：`configured_feature/` {len(old_cf)} 个文件"
                  + (f"，{code(rel(old_oak))} 顶层键 {keys_str(load(old_oak).keys())}" if old_oak.is_file() else ""))
    return ("C1", "`configured_feature` 注册表并入 `worldgen/feature`",
            VERIFIED if ok else MISMATCH, ev)


def material_types(base: Path):
    rule_t, cond_t = registry_entries("minecraft:worldgen/material_rule_type"), \
        registry_entries("minecraft:worldgen/material_condition_type")
    rule_keys: dict[str, set] = defaultdict(set)
    cond_keys: dict[str, set] = defaultdict(set)
    for sub in ("material_rule", "material_condition"):
        for p in files(base, sub):
            def fn(node, path):
                if isinstance(node, dict) and isinstance(node.get("type"), str):
                    t = node["type"].removeprefix("minecraft:")
                    target = rule_keys if t in rule_t else cond_keys if t in cond_t else None
                    if target is not None:
                        target[t] |= {k for k in node if k != "type"}
            walk(load(p), fn)
    return rule_t, cond_t, rule_keys, cond_keys


def claim_c2():
    ev, ok = [], True
    ns_files = files(NEW, "noise_settings")
    have_mr = [p for p in ns_files if "material_rule" in load(p)]
    have_sr = [p for p in ns_files if "surface_rule" in load(p)]
    ok &= len(have_mr) == len(ns_files) and not have_sr
    ev.append(f"`noise_settings/` {len(ns_files)} 个文件：含 `material_rule` 的 {len(have_mr)} 个，含 `surface_rule` 的 {len(have_sr)} 个")
    ow = load(NEW / WG / "noise_settings" / "overworld.json")["material_rule"]
    target = res_path(NEW, "material_rule", ow) if isinstance(ow, str) else None
    ev.append(f"overworld 的 `material_rule` 值为 {code(json.dumps(ow) if not isinstance(ow, str) else ow)}"
              + (f"，指向 {code(rel(target))}（存在={target.is_file()}）" if target else ""))
    ok &= target is not None and target.is_file()
    hits = 0
    for p in (NEW / "data").rglob("*.json"):
        hits += p.read_text(encoding="utf-8").count('"surface_rule"')
    ok &= hits == 0
    ev.append(f"整个 `data/` 导出中键名 `\"surface_rule\"` 出现 {hits} 次")
    ev.append(f"`material_rule/` {len(files(NEW, 'material_rule'))} 个文件，`material_condition/` {len(files(NEW, 'material_condition'))} 个文件")
    rule_t, cond_t, _, _ = material_types(NEW)
    ev.append(f"类型注册表：`material_rule_type` = {keys_str(rule_t)}；`material_condition_type` = {keys_str(cond_t)}")
    if OLD.is_dir():
        o = load(OLD / WG / "noise_settings" / "overworld.json")
        ev.append(f"对照 1.21.11：overworld 含 `surface_rule`={'surface_rule' in o}，含 `material_rule`={'material_rule' in o}")
    return ("C2", "`surface_rule` 改名为 `material_rule`", VERIFIED if ok else MISMATCH, ev)


def claim_c3():
    ev, ok = [], True
    rows = []
    for p in files(NEW, "noise_settings"):
        ns = load(p)
        router = set(ns.get("noise_router", {}))
        aq = ns.get("aquifers")
        ok &= router == EXPECTED_ROUTER
        ok &= "aquifers_enabled" not in ns and not (router & {"barrier", "fluid_level_floodedness", "fluid_level_spread", "lava"})
        rows.append(f"{p.stem}: aquifers={'有 ' + keys_str(aq.keys()) if isinstance(aq, dict) else '无'}")
    n_ns = len(files(NEW, "noise_settings"))
    ev.append(f"{n_ns} 个 `noise_settings` 的 `noise_router` 键集合都等于 {keys_str(EXPECTED_ROUTER)}：{ok}")
    ev.append("各文件 `aquifers`：" + "；".join(rows))
    ow = load(NEW / WG / "noise_settings" / "overworld.json")
    aq_ok = isinstance(ow.get("aquifers"), dict) and set(ow["aquifers"]) == EXPECTED_AQUIFERS
    ok &= aq_ok
    ev.append(f"overworld 的 `aquifers` 键集合等于 {keys_str(EXPECTED_AQUIFERS)}：{aq_ok}")
    ev.append(f"overworld 顶层键：{keys_str(ow.keys())}")
    ev.append(f"overworld `noise` = {code(json.dumps(ow['noise'], sort_keys=True))}（`size_horizontal`/`size_vertical` 已无，cell 尺寸改在 `interpolated` 上）")
    ev.append(f"overworld `aquifers.surface_level` = {code(ow['aquifers'].get('surface_level'))}")
    if OLD.is_dir():
        o = load(OLD / WG / "noise_settings" / "overworld.json")
        old_router = set(o["noise_router"])
        ev.append(f"对照 1.21.11：router 原有 {keys_str(old_router)}")
        ev.append(f"已从 router 移除：{keys_str(old_router - EXPECTED_ROUTER)}；新增：{keys_str(EXPECTED_ROUTER - old_router)}")
        ev.append(f"1.21.11 顶层的 {keys_str(set(o) - set(ow))} 在 26.3 已不存在")
    return ("C3", "含水层字段从 `noise_router` 挪进新的 `aquifers` 对象", VERIFIED if ok else MISMATCH, ev)


def default_sources_jar() -> Path | None:
    """The Loom genSources output for 26.3 inside this project, if it exists."""
    base = ks.ROOT / ".gradle" / "loom-cache" / "minecraftMaven" / "net" / "minecraft"
    found = sorted(base.glob("minecraft-merged-*/26.3/*-26.3-sources.jar")) if base.is_dir() else []
    return found[-1] if found else None


def find_constant_codec(sources: Path | None) -> str | None:
    if sources is None:
        sources = default_sources_jar()
    if sources is None:
        return None
    pat = re.compile(r"ConstantFunction\.java$")
    if sources.is_file():
        with zipfile.ZipFile(sources) as z:
            names = [n for n in z.namelist() if pat.search(n)]
            if not names:
                return None
            name, text = names[0], z.read(names[0]).decode("utf-8", errors="replace")
    else:
        found = [p for p in sources.rglob("ConstantFunction.java")]
        if not found:
            return None
        name, text = found[0].as_posix(), found[0].read_text(encoding="utf-8", errors="replace")
    for i, ln in enumerate(text.splitlines(), 1):
        if "fieldOf(" in ln:
            return f"`{name}:{i}`：`{ln.strip()}`"
    return None


def claim_c4(sources: Path | None):
    ev, ok = [], True
    by_type, bad, const_obj, bare = collect_df(NEW)
    ok &= not bad
    ev.append(f"所有密度函数上下文（`density_function/**` 与 noise_settings 的 router/aquifers/debug_functions）中 "
              f"`argument`/`argument1`/`argument2` 键出现 {sum(bad.values())} 次")
    for t in BINARY_DF:
        if t in by_type:
            sets = set(by_type[t])
            good = sets == {frozenset({"left", "right"})}
            ok &= good
            ev.append(f"`{t}`：{sum(by_type[t].values())} 处，参数键 {', '.join(keys_str(s) for s in sets)}")
    unary_seen = {t: set(by_type[t]) for t in UNARY_DF if t in by_type}
    unary_ok = all(s == {frozenset({"input"})} for s in unary_seen.values())
    ok &= unary_ok
    ev.append("一元函数 " + "、".join(f"`{t}`" for t in unary_seen) + f" 参数键都只有 `input`：{unary_ok}")
    ev.append(f"`constant` 以对象形式出现 {const_obj} 次；常量全部写成裸数字（在 left/right/input 等位置共 {bare} 处）")
    df_types = registry_entries("minecraft:worldgen/density_function_type")
    ev.append(f"`density_function_type` 注册表 {len(df_types)} 项：{keys_str(df_types)}")
    if OLD.is_dir():
        old_types, old_bad, _, _ = collect_df(OLD)
        removed = sorted(set(old_types) - set(df_types))
        ev.append(f"对照 1.21.11：旧数据使用的类型中 26.3 已没有的：{keys_str(removed)}")
        ev.append(f"1.21.11 中 `argument`/`argument1`/`argument2` 键出现 {sum(old_bad.values())} 次；"
                  f"旧 `add` 参数键 {', '.join(keys_str(s) for s in old_types.get('add', {}))}")
    codec = find_constant_codec(sources)
    if codec:
        ev.append(f"反编译源码（`gradlew genSources`）：{codec}")
        verdict = VERIFIED if ok and "\"value\"" in codec else MISMATCH if not ok else PARTIAL
    else:
        ev.append("`constant` 的对象写法（`value` 还是 `argument`）原版数据里没有出现，需反编译源码确认（`--sources`）")
        verdict = PARTIAL if ok else MISMATCH
    return ("C4", "`constant`/`add`/`mul` 等密度函数的参数改名", verdict, ev), by_type


# --------------------------------------------------------- other facts ----

def other_facts():
    out = []
    fd = NEW / WG / "density_function" / "overworld" / "final_density.json"
    out.append(f"`final_density` 末尾显式加 `beardifier`：{code(rel(fd))} 含 `minecraft:beardifier` = "
               f"{'minecraft:beardifier' in fd.read_text(encoding='utf-8')}")
    tag = NEW / "data" / "minecraft" / "tags" / "worldgen" / "world_preset" / "normal.json"
    out.append(f"{code(rel(tag))} = {code(json.dumps(load(tag)['values']))}（“世界类型”按钮读取这个标签）")
    wp = load(NEW / WG / "world_preset" / "normal.json")
    gen = wp["dimensions"]["minecraft:overworld"]["generator"]
    out.append(f"`world_preset/normal.json` 主世界 generator = {code(json.dumps(gen, sort_keys=True))}")
    biome_keys: Counter = Counter()
    attr_ids: Counter = Counter()
    for p in files(NEW, "biome"):
        b = load(p)
        biome_keys.update(b.keys())
        attr_ids.update((b.get("attributes") or {}).keys())
    out.append(f"biome 顶层键（出现次数 / {len(files(NEW, 'biome'))}）：" +
               ", ".join(f"`{k}`×{n}" for k, n in sorted(biome_keys.items())))
    out.append("biome 中用到的 attributes：" + ", ".join(f"`{k}`×{n}" for k, n in sorted(attr_ids.items())))
    env = registry_entries("minecraft:environment_attribute")
    out.append("`environment_attribute` 注册表中 fog/sky 相关：" +
               keys_str([e for e in env if "fog" in e or "sky" in e]))
    bp = NEW / "reports" / "biome_parameters" / "minecraft" / "overworld.json"
    if bp.is_file():
        bpj = load(bp)
        first = bpj["biomes"][0]
        out.append(f"{code(rel(bp))} 存在：{len(bpj['biomes'])} 条，参数键 {keys_str(first['parameters'].keys())}")
    pl = files(NEW, "multi_noise_biome_source_parameter_list")
    out.append("`multi_noise_biome_source_parameter_list/`：" +
               "；".join(f"{p.stem} = {code(json.dumps(load(p)))}" for p in pl))
    m = load(NEW / "MANIFEST.json")
    out.append(f"数据包版本：{code(json.dumps(load(NEW / 'version.json').get('pack_version')))}")
    return out, m


def appendix_df(by_type) -> list[str]:
    lines = ["| 类型 | 出现次数 | 参数键 |", "|---|---|---|"]
    for t in sorted(by_type):
        sets = sorted(by_type[t].items(), key=lambda kv: -kv[1])
        lines.append(f"| `{t}` | {sum(by_type[t].values())} | " +
                     "<br>".join(keys_str(s) if s else "（无）" for s, _ in sets) + " |")
    return lines


def appendix_material() -> list[str]:
    rule_t, cond_t, rule_keys, cond_keys = material_types(NEW)
    lines = ["| 种类 | 类型 | 原版数据中出现的参数键 |", "|---|---|---|"]
    for t in rule_t:
        lines.append(f"| rule | `{t}` | {keys_str(rule_keys.get(t, set())) or '（原版未用或无参数）'} |")
    for t in cond_t:
        lines.append(f"| condition | `{t}` | {keys_str(cond_keys.get(t, set())) or '（原版未用或无参数）'} |")
    return lines


def main() -> int:
    ks.setup_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", type=Path, help="Minecraft 26.3 sources jar or directory (genSources)")
    args = ap.parse_args()
    import export_vanilla_data as ev  # same folder; checks completeness, jar sha1 and tree hash
    if not ev.manifest_is_current(NEW, ks.SERVER_JARS[ev.VERSION]["sha1"]):
        print("ERROR: reference/vanilla-26.3 is missing, incomplete, stale or modified "
              "(manifest / jar sha1 / tree hash mismatch); run python tools/export_vanilla_data.py --force",
              file=sys.stderr)
        return 2

    c1, c2, c3 = claim_c1(), claim_c2(), claim_c3()
    c4, by_type = claim_c4(args.sources)
    claims = [c1, c2, c3, c4]
    facts, manifest = other_facts()

    md = ["# 原版 26.3 世界生成格式核实（本地导出）", "",
          "> 由 `python tools/verify_formats.py` 生成，请勿手改。证据路径相对于 `reference/`。", "",
          "## 数据来源", "",
          f"- 命令：`{manifest['command']}`",
          f"- 服务器 jar sha1：`{manifest['jar_sha1']}`",
          f"- 导出文件 {manifest['file_count']} 个，其中 worldgen {manifest['worldgen_total']} 个；"
          f"与 jar 内置 JSON 逐文件对比：相同 {manifest['comparison_with_jar']['identical']}，"
          f"不同 {manifest['comparison_with_jar']['different']}，"
          f"仅 jar 有 {manifest['comparison_with_jar']['only_in_jar']}，"
          f"仅导出有 {manifest['comparison_with_jar']['only_in_datagen']}",
          f"- 导出树哈希（运行前已重算核对）：`{manifest['tree_sha256']}`",
          f"- 1.21.11 对照：{'有（`vanilla-1.21.11-shipped/`，从 jar 内直接解出）' if OLD.is_dir() else '无'}",
          "", "数据生成器 `--help`：", "", "```", manifest["datagen_help"], "```", "",
          "## SPEC §2.1 的说法逐条核实", "",
          "| # | 说法 | 结论 | 证据 |", "|---|---|---|---|"]
    for cid, text, verdict, ev in claims:
        md.append(f"| {cid} | {text} | **{verdict}** | " + "<br>".join(e.replace("|", "\\|") for e in ev) + " |")
    md += ["", "## 其他记录", ""] + [f"- {f}" for f in facts]
    md += ["", "## 附录 A：密度函数类型与参数键（26.3 原版数据实际出现的）", ""] + appendix_df(by_type)
    md += ["", "## 附录 B：material rule / condition 类型与参数键", ""] + appendix_material()
    md.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md), encoding="utf-8", newline="\n")

    for cid, text, verdict, _ in claims:
        print(f"{cid} {verdict}: {text}")
    print(f"wrote {OUT}")
    return 1 if any(v == MISMATCH for _, _, v, _ in claims) else 0


if __name__ == "__main__":
    sys.exit(main())
