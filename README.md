# Karstscape

Tower karst landscapes for the Minecraft Overworld: steep, green-topped limestone towers rising from
plains, rivers and lakes — the kind of scenery you know from Guilin or Ha Long, built to be explored,
built on and flown over.

**Minecraft Java 26.3 · Fabric · server-side only · vanilla blocks only · MIT**

[中文说明](#中文说明)

## Features

- **Tower field** — hundreds of near-vertical towers per km², 40–150 blocks above the plain, 14–62 blocks
  across, sometimes fused into twins and clusters; round domed tops, slightly irregular outlines.
  Towers get lower and sparser towards the edge of a karst region.
- **Karst regions** in a normal Overworld (~8–9 % of land, warm inland climates), with a new biome
  `karstscape:tower_karst`. Outside these regions the world is exactly vanilla.
- **Materials** — horizontal limestone-like bands of stone, andesite, calcite, tuff and dripstone on the
  walls, moss streaks, grassy and mossy tops, gravel and coarse dirt at tower feet, clay/sand/gravel
  river beds.
- **Vegetation** — azalea trees and jungle bushes on the tops, bamboo, ponds, vines on the walls, plus
  the usual plains grass, flowers and trees. Light white-blue haze.
- **Playable** — rivers crossing a karst region become wide, shallow, boatable valleys that towers keep
  out of; you never spawn on a tower; only underground structures (mineshafts, strongholds, trial
  chambers) generate inside karst regions.
- **Deterministic and fast** — everything comes from the world seed and coordinates; no global
  simulation. Chunk generation is ~10 % slower than vanilla in the worst case (a world that is all
  karst).

## Installation

Server (or singleplayer) only — players can join with an unmodified vanilla client.

1. Install [Fabric Loader](https://fabricmc.net/use/) 0.20.1+ for Minecraft 26.3.
2. Put `karstscape-0.1.0.jar` and [Fabric API](https://modrinth.com/mod/fabric-api) (0.166.2+26.3 or
   newer for 26.3) into `mods/`.
3. Create a **new** world with one of the world types:
   - **Karstscape** (`karstscape:karstscape`) — normal Overworld with karst regions;
   - **Karst Only (debug)** (`karstscape:karst_only`) — all land is karst (screenshots, aerial video).

   On a dedicated server set `level-type=karstscape\:karstscape` (or `karstscape\:karst_only`) in
   `server.properties` before the world is created.

The default world type is not changed; existing worlds are not affected.

## Commands (operators)

| Command | What it does |
|---|---|
| `/karstscape find` | nearest larger karst region and an aerial `/tp` from its edge |
| `/karstscape spawn` | checks that the world spawn is on the ground, not on a tower |
| `/karstscape probe <x> <z>` | column check at a position |
| `/karstscape export <radius> [<x> <z> [<step>]]` | height/biome/slope/mask PNGs + stats.json (no chunk generation) |
| `/karstscape check <chunkRadius> <x> <z>` | generates chunks; seams, overhangs, block census |
| `/karstscape bench <chunks> <x> <z>` | chunk generation time |

## Known issues

- Only Minecraft 26.3 with Fabric. No NeoForge, no other versions, no data-pack-only version yet.
- No villages or other surface structures inside karst regions (they would land on towers or be carved
  into tower walls). They still generate everywhere else.
- Rivers are only widened where vanilla already has a river; karst regions have no extra streams.
- Vines are single blocks (the vanilla vine feature), not long hanging curtains.
- Fog distance is not changed (only fog and sky colours).
- Not compatible with other mods that replace the Overworld noise settings or biome source of the same
  world (Karstscape uses its own world types, so plain biome mods usually work in other world types).
- Distant Horizons / Voxy have not been tested.

## Building

Java 25. `gradlew build` → `build/libs/karstscape-0.1.0.jar`. Worldgen JSON is generated from
`tools/worldgen/karst_params.toml` by `python tools/gen_worldgen.py` (needs the vanilla data export:
`python tools/export_vanilla_data.py`). See `CLAUDE.md` for the development tools.

---

## 中文说明

在主世界里生成**喀斯特峰林**：平地上拔起一根根陡峭的绿顶石峰，峰间是能生存、建造、划船的平原和河流，适合航拍。

**Minecraft Java 26.3 · Fabric · 只需服务端安装 · 只用原版方块 · MIT**

### 功能
- **石峰**：近乎垂直的侧壁，圆钝的峰顶。相对平原高 40–150 格，直径 14–62 格，偶尔有双峰连体或峰丛；靠近区域边缘的峰会逐渐变矮、变稀。
- **喀斯特区**：在正常主世界里出现，约占陆地的 8–9%，位于偏暖的内陆。新群系为 `karstscape:tower_karst`，区外地形与原版完全一样。
- **材质**：侧壁有类似石灰岩层理的水平条带（石头、安山岩、方解石、凝灰岩、滴水石块），挂着苔藓条纹；峰顶是草和苔藓；峰脚是碎石和粗泥；河床是黏土、沙子和碎石。
- **植被**：峰顶有杜鹃树和丛林灌木，另有竹子、水塘和挂在侧壁上的藤蔓；平原上原版的草、花、树都保留。雾为淡白蓝色。
- **可玩性**：
  - 原版河流穿过峰林时，会变成宽而浅、能划船的河谷，河道里不长峰；
  - 不会出生在峰顶；
  - 峰林区内只生成地下结构（废弃矿井、要塞、试炼密室）。
- **确定性**：同一个种子总是生成同样的世界。
- **性能**：最坏情况（整个世界都是峰林）下，区块生成比原版慢约 10%。

### 安装
1. 安装 Minecraft 26.3 的 Fabric Loader，版本 0.20.1 及以上。
2. 把 `karstscape-0.1.0.jar` 和 Fabric API 放进 `mods/`。Fabric API 需要 26.3 版，0.166.2+26.3 及以上。
3. **新建世界**，世界类型选：
   - **Karstscape**：正常主世界，带喀斯特区；
   - **Karst Only (debug)**：全部陆地都是峰林，用于截图和航拍。

专用服务器要在世界生成之前，在 `server.properties` 里写 `level-type=karstscape\:karstscape`。

只需服务端安装，玩家用原版客户端就能进服。默认世界类型不变，已有的存档不受影响。

### 已知问题
- 只支持 Minecraft 26.3 + Fabric。
- 峰林区内没有村庄等地上结构，其他地方照常生成。
- 只有原版本来就有河的地方才会变成宽河谷。
- 藤蔓是原版的单格藤蔓，不会成串下垂。
- 只改了雾和天空的颜色，没有改雾的距离。
- 与替换主世界地形或群系源的模组不兼容。
- 没有测试过 Distant Horizons 和 Voxy。
