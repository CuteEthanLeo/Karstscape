# 26.3 世界生成数据格式笔记（已核实）

所有事实都来自本地导出 `reference/vanilla-26.3/`（官方数据生成器 `--server --reports` 的输出，与 jar 内置数据逐文件一致）。下文路径省略前缀 `reference/vanilla-26.3/data/minecraft/worldgen/`，`reports/` 指 `reference/vanilla-26.3/reports/`。
自动生成的逐条核实与完整键表见 [vanilla-26.3-format-check.md](vanilla-26.3-format-check.md)。旧格式对照在 `reference/vanilla-1.21.11-shipped/`。

> 写 JSON 前仍要打开对应原版文件照抄结构。这里只是索引，不是替代品。

## 1. 目录（数据注册表）变化

| 26.3 目录 | 旧名 / 说明 | 例子 |
|---|---|---|
| `feature/`（240） | 旧 `configured_feature/`。配置**内联到顶层**，没有 `config` 字段 | `feature/oak.json`：`type`, `trunk_provider`, `trunk_placer`, `foliage_provider`, … |
| `placed_feature/`（273） | 不变：`{"feature": <id 或内联>, "placement": [...]}` | `placed_feature/flower_plains.json` |
| `carver/`（4） | 旧 `configured_carver/`；carver 类型只剩 `cave`、`canyon` | `carver/cave.json` |
| `material_rule/`（42） | 旧 noise_settings 内联的 `surface_rule` 拆成可引用的数据 | `material_rule/overworld.json` |
| `material_condition/`（8） | 新：可复用的条件 | `material_condition/not_underwater.json` |
| `block_state_provider/`（8） | 新：可引用的方块状态提供器 | `block_state_provider/flower_plain.json` |

类型注册表名（`reports/registries.json`）：`worldgen/feature_type`（58）、`worldgen/material_rule_type`（5）、`worldgen/material_condition_type`（11）、`worldgen/block_state_provider_type`（10）、`worldgen/density_function_type`（44）、`worldgen/placement_modifier_type`（18）。

## 2. noise_settings（`noise_settings/overworld.json`）

顶层键：`aquifers`, `debug_functions`, `default_block`, `default_fluid`, `disable_mob_generation`, `legacy_random_source`, `material_rule`, `noise`, `noise_router`, `sea_level`, `spawn_target`。

- `noise` 只剩 `{"min_y": -64, "height": 384}`；`size_horizontal`/`size_vertical` 没了，cell 尺寸写在 `interpolated` 上。
- `material_rule`（旧 `surface_rule`）：值是 id，例如 `"minecraft:overworld"` 指向 `material_rule/overworld.json`；也可以内联对象。
- `noise_router` 正好 8 个键：`chunk_surface_level`, `continents`, `depth`, `erosion`, `final_density`, `ridges`, `temperature`, `vegetation`（值都是 `density_function/overworld/*` 的 id）。
- `aquifers`（可选；不写 = 无含水层，caves/end/nether/floating_islands 就没有）：`barrier`, `exclusion`, `fluid_level_floodedness`, `fluid_level_spread`, `lava`, `surface_level`。旧 router 里的 `preliminary_surface_level` 现在是 `aquifers.surface_level`。
- 已删除：`aquifers_enabled`, `ore_veins_enabled`，router 的 `barrier`/`fluid_level_*`/`lava`/`vein_*`。矿脉改成 material rule `ore_vein`（见 `material_rule/overworld/iron_ore_vein.json`），**自定义 material_rule 时要保留原版序列里的矿脉条目**。
- `spawn_target`：列表，每项以密度函数 id 为键、`[min, max]` 为值。

## 3. 密度函数（`density_function/**`）

| 写法 | 参数键 |
|---|---|
| `add` `sub` `mul` `div` `min` `max` | `left`, `right`（旧 `argument1`/`argument2`） |
| `abs` `square` `cube` `half_negative` `quarter_negative` `squeeze` `negate` `cache` `blend_density` | `input`（旧 `argument`） |
| 常量 | 原版**只写裸数字**（如 `"right": -0.225`）；`constant` 对象形式见 §7 |
| `clamp` | `input`, `min`, `max` |
| `lerp` | `alpha`, `first`, `second` |
| `gradient`（旧 `y_clamped_gradient`） | `axis`, `from_coordinate`, `from_value`, `to_coordinate`, `to_value` |
| `noise`（吸收了旧 `shifted_noise`） | `noise`, `xz_scale`, `y_scale`，可选 `shift_x`, `shift_z` |
| `shift_a` / `shift_b` | `noise` |
| `interpolated` | `input`, `cell_size_xz`, `cell_size_y`（主世界 4 / 8） |
| `cache`（取代 `flat_cache`/`cache_2d`/`cache_once`/`cache_all_in_cell`） | `input` |
| `interval_select`（取代 `weird_scaled_sampler`） | `input`, `thresholds`, `functions` |
| `find_top_surface` | `density`, `upper_bound`, `lower_bound`, `cell_height` |
| `range_choice` | `input`, `min_inclusive`, `max_exclusive`, `when_in_range`, `when_out_of_range` |
| `slice` | `axis`, `coordinate`, `input` |
| `distance_to_point` | `metric`, `point` |
| `spline` | `spline: {coordinate, points: [{location, derivative, value}]}` |

- 注册表全部 44 种：见 format-check 的 C4 证据。已删除：`y_clamped_gradient`, `shifted_noise`, `flat_cache`, `cache_2d`, `cache_once`, `cache_all_in_cell`, `weird_scaled_sampler`, `invert`, `end_islands`。
- `final_density` 不再隐式叠加结构地形适配，原版在末尾显式写 `{"type": "minecraft:beardifier"}`（`density_function/overworld/final_density.json`）。**自定义 final_density 必须保留它**，否则村庄等结构周围不会被整平。
- 网上资料提到 26.3 起密度函数改为单精度求值（wiki，未在本地数据中可验证）。
- **对 M2 的影响**：SPEC §3.2 里的 `cache_2d` / `flat_cache` 在 26.3 已不存在，统一为 `cache`；Java 侧 API 的变化见 §7。

## 4. material rule / condition（旧 surface rule）

- rule 类型：`sequence {sequence}`、`condition {if_true, then_run}`、`block {result_state}`、`bandlands {}`、`ore_vein {density, filler_block, filler_gap, ore_block, raw_ore_block, raw_ore_chance, richness}`。
- condition 类型：`stone_depth`、`y_above`、`water`、`vertical_gradient`、`noise_threshold`（新增可选 `is_3d`）、`biome {biome_is}`、`not {invert}`、`steep`、`hole`、`above_preliminary_surface`、`temperature`。
- `result_state` 现在是**纯字符串**：`"minecraft:stone"`。
- `then_run` / `if_true` 可以写 id 引用，例如 `"if_true": "minecraft:not_underwater"`。
- 陡坡：`material_rule/overworld/biome_surface/jagged_peaks.json` 用 `{"type": "minecraft:steep"}` 判断陡壁并放石头，M3 的峭壁材质可参考。
- 主世界入口 `material_rule/overworld.json` 的结构：`sequence[bedrock_floor, copper_ore_vein, iron_ore_vein, condition(above_preliminary_surface → overworld/surface), overworld/underground]`。

## 5. 群系（`biome/plains.json`）

- 顶层键：`attributes`, `carvers`, `downfall`, `effects`, `features`, `has_precipitation`, `temperature`，可选 `temperature_modifier`。
- `effects`：`water_color`（必填），可选 `foliage_color`, `grass_color`, `dry_foliage_color`, `grass_color_modifier`。
- 生物生成**挪进了 attributes**：`"minecraft:gameplay/natural_mob_spawns": {"argument": {"spawn_costs": {}, "spawns_by_category": {...}}, "modifier": "overlay"}`；`count` 是整数或 int provider（`{"type": "minecraft:uniform", ...}`）。旧 `spawners` / `spawn_costs` 顶层键已删除。
- 雾和天空：`minecraft:visual/sky_color`（plains 写 `"#78a7ff"`）、`visual/fog_color`、`visual/fog_start_distance`、`visual/fog_end_distance`、`visual/sky_fog_end_distance`、`visual/cloud_fog_end_distance`、`visual/water_fog_*`。值可以是直接值，也可以是 `{"argument": …, "modifier": "multiply"}` 这类修饰形式（见 `biome/mangrove_swamp.json`）。原版群系里只用到了 `fog_color`、`sky_color`、`water_fog_*`；雾距离目前只在 nether 的 dimension_type 里出现，**M3 要实测群系里写雾距离是否生效**。
- `carvers` 是列表，`features` 是按生成阶段分组的列表的列表（与旧版相同）。

## 6. 世界预设与群系源

- `world_preset/normal.json`：`dimensions → minecraft:overworld → {type, generator: {type: "minecraft:noise", biome_source: {type: "minecraft:multi_noise", preset: "minecraft:overworld"}, settings: "minecraft:overworld"}}`。
- 要出现在建世界界面的“世界类型”按钮里，必须写进 **`data/minecraft/tags/worldgen/world_preset/normal.json`**（不是 `data/karstscape/...`）。原版内容：`normal, flat, large_biomes, amplified, single_biome_surface`。显示名的翻译键是 `generator.<命名空间>.<名字>`。
- `multi_noise_biome_source_parameter_list/overworld.json` 只有 `{"preset": "minecraft:overworld"}`。展开后的完整参数表在 `reports/biome_parameters/minecraft/overworld.json`（7594 条，参数键 `temperature, humidity, continentalness, erosion, weirdness, depth, offset`）。**M1 自定义 biome source 时以它为底稿。**
- `server.properties` 的 `level-type` 接受预设 id（`minecraft\:normal`）；模组预设 id 能否在专用服务器用，M1 实测。

## 7. 其他

- 方块状态：字符串，或 `{"id": ..., "properties": {...}}`（旧 `Name`/`Properties` 已删除）。例：`feature/oak.json` 的 `trunk_provider`。
- 方块状态提供器类型去掉了 `_provider` / `_state_provider` 后缀：`simple`, `weighted`, `noise`, `dual_noise`, `noise_threshold`, `randomized_int`, `rotated`, `rule_based`, `copy_properties`, `random_block`。
- 放置修饰器：`offset {x, y, z}` 取代 `random_offset`；新增 `cuboid`, `random_chance`, `randomly_selected`。
- 花草：`random_patch` / `flower` feature 类型已删（26.1）。原版花丛是 `feature/flower_plain.json = {"type": "minecraft:simple_block", "to_place": "minecraft:flower_plain"}`（引用 `block_state_provider/flower_plain.json`），数量和散布交给 `placed_feature/flower_plains.json` 里的 `count` + `offset` + `block_predicate_filter`。
- 藤蔓：`feature/vines.json = {"type": "minecraft:vines"}`，`placed_feature/vines.json` 用 `count` + `height_range`。
- 数据包版本 data 121.0 / resource 97.1（`version.json`）。

## 8. 26.3 Java API（反编译源码）

见 [26.3-worldgen-java-api.md](26.3-worldgen-java-api.md)：自定义密度函数的写法与注册、种子绑定（`CompileContext.createRandom`）、插值外逐方块求值、2D 自动按列缓存、气候参数与 router 的对应、`level-type`、命令权限、注册表同步。
