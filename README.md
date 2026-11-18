# Karstscape

A large, realistic Overworld for Minecraft — wide continents, long mountain ranges, river gorges,
terraced hills and coastal cliffs — with tower karst landscapes: steep, green-topped limestone towers
rising from plains, rivers and lakes, the kind of scenery you know from Guilin or Ha Long. Built to be
explored, built on and flown over.

**Minecraft Java 26.3 · Fabric · server-side only · vanilla blocks only · MIT**

[中文说明](#中文说明)

## Features

- **Taller world** — build height y -64..447 (new worlds only, via its own dimension type).
- **Large-scale terrain** — continents about 2.5× larger than vanilla, deeper oceans, mountains up to
  about y 420.
- **Mountain ranges** — peaks line up along long, winding ridgelines instead of single round hills.
- **River valleys and gorges** — every river is cut down to the water level; through high ground it
  becomes a deep V-shaped gorge.
- **Terraces** — some hill regions form flat steps (about 20 blocks per step) with steep risers.
- **Coastal cliffs** — about half of the coastlines rise as a steep wall behind a low shore platform.
- **Tower karst** — hundreds of near-vertical towers per km², 40–150 blocks above the plain, 14–62 blocks
  across, sometimes fused into twins and clusters; banded stone/andesite/calcite/tuff/dripstone walls,
  moss streaks, grassy tops with azalea trees and jungle bushes, bamboo, ponds and vines. New biome
  `karstscape:tower_karst`.
- **Playable** — rivers crossing a karst region become wide, shallow, boatable valleys; you never
  spawn on a tower; only underground structures generate inside karst regions.
- **Deterministic** — everything comes from the world seed and coordinates; no global simulation.
  Chunk generation is about 25 % slower than vanilla.

## Installation

Server (or singleplayer) only — players can join with an unmodified vanilla client.

1. Install [Fabric Loader](https://fabricmc.net/use/) 0.20.1+ for Minecraft 26.3.
2. Put `karstscape-0.2.0.jar` and [Fabric API](https://modrinth.com/mod/fabric-api) (0.166.2+26.3 or
   newer for 26.3) into `mods/`.
3. Create a **new** world. The **Default** world type now uses the Karstscape terrain. Other world
   types:
   - **Classic (vanilla)** (`karstscape:classic`) — the unchanged vanilla Overworld;
   - **Karst Only (debug)** (`karstscape:karst_only`) — all land is karst (screenshots, aerial video).

   A dedicated server uses the new terrain with the default `level-type=minecraft\:normal`.

Existing worlds are not changed. In singleplayer, Minecraft marks the world as using "experimental
settings" and asks for confirmation when you create or open it — this is expected.

## Commands (operators)

| Command | What it does |
|---|---|
| `/karstscape info` | current noise settings, dimension type and height range |
| `/karstscape find` | nearest larger karst region and an aerial `/tp` from its edge |
| `/karstscape spawn` | checks that the world spawn is on the ground, not on a tower |
| `/karstscape probe <x> <z>` | column check at a position |
| `/karstscape export <radius> [<x> <z> [<step>]]` | height/biome/slope/mask PNGs + stats.json (no chunk generation) |
| `/karstscape layer <radius> <x> <z> <step> <density function>` | any density function as a grayscale PNG + stats |
| `/karstscape check <chunkRadius> <x> <z>` | generates chunks; seams, overhangs, block census |
| `/karstscape bench <chunks> <x> <z>` | chunk generation time |

## Known issues

- Work in progress: snow lines, alpine materials and the karst/terrain transition are not tuned yet;
  the share of karst varies between seeds (about 6–12 % of land).
- Only Minecraft 26.3 with Fabric. No NeoForge, no other versions, no data-pack-only version yet.
- Not compatible with other mods that replace the Overworld terrain (Tectonic, Terralith,
  Lithostitched …). Amplified, Large Biomes, the Nether and the End stay vanilla.
- No villages or other surface structures inside karst regions. They still generate everywhere else.
- Vines are single blocks (the vanilla vine feature), not long hanging curtains.
- Distant Horizons / Voxy have not been tested.

## Building

Java 25. `gradlew build` → `build/libs/karstscape-0.2.0.jar`. Worldgen JSON is generated from
`tools/worldgen/karst_params.toml` by `python tools/gen_worldgen.py` (needs the vanilla data export:
`python tools/export_vanilla_data.py`). See `CLAUDE.md` for the development tools.

---

## 中文说明

**写实大地形**的主世界：更大的大陆、连绵的山脉、河谷峡谷、台地和海岸悬崖；其中还有**喀斯特峰林**——平地上拔起一根根陡峭的绿顶石峰，峰间是能生存、建造、划船的平原和河流，适合航拍。

**Minecraft Java 26.3 · Fabric · 只需服务端安装 · 只用原版方块 · MIT**

### 功能
- **世界加高**：建筑范围 y -64..447（只影响新世界）。
- **大陆尺度**：大陆约为原版的 2.5 倍，海更深，最高峰约 y 420。
- **连绵山脉**：山峰沿着长而弯曲的脊线排成山脉，不再是一个个圆包。
- **河谷和峡谷**：所有河流都切到水面高度，流经高地时变成深深的 V 形峡谷。
- **台地**：部分丘陵区变成阶梯，每级约 20 格，台面平整、陡坎干净。
- **海岸悬崖**：约一半的海岸是一道陡壁，前面有一条低矮的岸边平台。
- **喀斯特峰林**：每平方公里数百根近乎垂直的石峰，高 40–150 格，直径 14–62 格；侧壁有石灰岩层理条带和苔藓，峰顶有杜鹃树和丛林灌木，另有竹子、水塘、藤蔓。群系 `karstscape:tower_karst`。
- **可玩性**：河流穿过峰林时变成宽而浅、能划船的河谷；不会出生在峰顶；峰林区内只生成地下结构。
- **确定性**：同一个种子总是生成同样的世界。区块生成比原版慢约 25%。

### 安装
1. 安装 Minecraft 26.3 的 Fabric Loader，版本 0.20.1 及以上。
2. 把 `karstscape-0.2.0.jar` 和 Fabric API 放进 `mods/`。Fabric API 需要 26.3 版，0.166.2+26.3 及以上。
3. **新建世界**，世界类型保持 **默认** 就是新地形。其他世界类型：
   - **Classic (vanilla)**：原版主世界，没有任何改动；
   - **Karst Only (debug)**：全部陆地都是峰林，用于截图和航拍。

专用服务器用默认的 `level-type=minecraft\:normal` 就是新地形。只需服务端安装，玩家用原版客户端就能进服。

已有的存档不受影响。单人模式新建或打开世界时会弹出“实验性设置”确认框，这是正常的，点继续即可。

### 已知问题
- 仍在开发中：雪线、高山材质、峰林与周围地形的过渡还没调好；峰林占陆地的比例随种子在 6–12% 之间浮动。
- 只支持 Minecraft 26.3 + Fabric。
- 与替换主世界地形的模组（Tectonic、Terralith、Lithostitched 等）不兼容；放大化、巨型生物群系、下界、末地保持原版。
- 峰林区内没有村庄等地上结构，其他地方照常生成。
- 藤蔓是原版的单格藤蔓，不会成串下垂。
- 没有测试过 Distant Horizons 和 Voxy。
