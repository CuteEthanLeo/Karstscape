# 原版 26.3 世界生成格式核实（本地导出）

> 由 `python tools/verify_formats.py` 生成，请勿手改。证据路径相对于 `reference/`。

## 数据来源

- 命令：`java -DbundlerMainClass=net.minecraft.data.Main -jar server.jar --server --reports --output <dir>`
- 服务器 jar sha1：`33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c`
- 导出文件 10039 个，其中 worldgen 1144 个；与 jar 内置数据逐文件对比：相同 8369，不同 0
- 导出树哈希：`e371e175aa37c0d7e1000baf0a52cb94de8958103d77d57d103d8c276d34d4e9`
- 1.21.11 对照：有（`vanilla-1.21.11-shipped/`，从 jar 内直接解出）

数据生成器 `--help`：

```
Option             Description
------             -----------
--all              Include all generators
--dev              Include development tools
--help             Show the help menu
--input <String>   Input folder
--output <String>  Output folder (default: generated)
--reports          Include data reports
--server           Include server generators
--validate         Validate inputs
```

## SPEC §2.1 的说法逐条核实

| # | 说法 | 结论 | 证据 |
|---|---|---|---|
| C1 | `configured_feature` 注册表并入 `worldgen/feature` | **已核实** | `vanilla-26.3/data/minecraft/worldgen/configured_feature/` 不存在：True<br>`vanilla-26.3/data/minecraft/worldgen/feature/` 有 240 个文件<br>其中顶层带 `config` 的 0 个，缺 `type` 的 0 个<br>`vanilla-26.3/data/minecraft/worldgen/feature/oak.json` 顶层键：`below_trunk_provider`, `decorators`, `foliage_placer`, `foliage_provider`, `ignore_vines`, `minimum_size`, `trunk_placer`, `trunk_provider`, `type`<br>`placed_feature/` 273 个：引用 `feature/` 的 273 个全部可解析（无法解析 0，内联 0）；`placed_feature` 结构仍是 `feature` + `placement`<br>顺带：`configured_carver/` 存在=False；`carver/` 有 4 个文件<br>`reports/registries.json` 里 feature 类型注册表名为 `minecraft:worldgen/feature_type`（58 项）<br>对照 1.21.11：`configured_feature/` 224 个文件，`vanilla-1.21.11-shipped/data/minecraft/worldgen/configured_feature/oak.json` 顶层键 `config`, `type` |
| C2 | `surface_rule` 改名为 `material_rule` | **已核实** | `noise_settings/` 7 个文件：含 `material_rule` 的 7 个，含 `surface_rule` 的 0 个<br>overworld 的 `material_rule` 值为 `minecraft:overworld`，指向 `vanilla-26.3/data/minecraft/worldgen/material_rule/overworld.json`（存在=True）<br>整个 `data/` 导出中键名 `"surface_rule"` 出现 0 次<br>`material_rule/` 42 个文件，`material_condition/` 8 个文件<br>类型注册表：`material_rule_type` = `bandlands`, `block`, `condition`, `ore_vein`, `sequence`；`material_condition_type` = `above_preliminary_surface`, `biome`, `hole`, `noise_threshold`, `not`, `steep`, `stone_depth`, `temperature`, `vertical_gradient`, `water`, `y_above`<br>对照 1.21.11：overworld 含 `surface_rule`=True，含 `material_rule`=False |
| C3 | 含水层字段从 `noise_router` 挪进新的 `aquifers` 对象 | **已核实** | 7 个 `noise_settings` 的 `noise_router` 键集合都等于 `chunk_surface_level`, `continents`, `depth`, `erosion`, `final_density`, `ridges`, `temperature`, `vegetation`：True<br>各文件 `aquifers`：amplified: aquifers=有 `barrier`, `exclusion`, `fluid_level_floodedness`, `fluid_level_spread`, `lava`, `surface_level`；caves: aquifers=无；end: aquifers=无；floating_islands: aquifers=无；large_biomes: aquifers=有 `barrier`, `exclusion`, `fluid_level_floodedness`, `fluid_level_spread`, `lava`, `surface_level`；nether: aquifers=无；overworld: aquifers=有 `barrier`, `exclusion`, `fluid_level_floodedness`, `fluid_level_spread`, `lava`, `surface_level`<br>overworld 顶层键：`aquifers`, `debug_functions`, `default_block`, `default_fluid`, `disable_mob_generation`, `legacy_random_source`, `material_rule`, `noise`, `noise_router`, `sea_level`, `spawn_target`<br>overworld `noise` = `{"height": 384, "min_y": -64}`（`size_horizontal`/`size_vertical` 已无，cell 尺寸改在 `interpolated` 上）<br>overworld `aquifers.surface_level` = `minecraft:overworld/preliminary_surface_level`<br>对照 1.21.11：router 原有 `barrier`, `continents`, `depth`, `erosion`, `final_density`, `fluid_level_floodedness`, `fluid_level_spread`, `lava`, `preliminary_surface_level`, `ridges`, `temperature`, `vegetation`, `vein_gap`, `vein_ridged`, `vein_toggle`<br>已从 router 移除：`barrier`, `fluid_level_floodedness`, `fluid_level_spread`, `lava`, `preliminary_surface_level`, `vein_gap`, `vein_ridged`, `vein_toggle`；新增：`chunk_surface_level`<br>1.21.11 顶层的 `aquifers_enabled`, `ore_veins_enabled`, `surface_rule` 在 26.3 已不存在 |
| C4 | `constant`/`add`/`mul` 等密度函数的参数改名 | **部分核实** | 所有密度函数上下文（`density_function/**` 与 noise_settings 的 router/aquifers/debug_functions）中 `argument`/`argument1`/`argument2` 键出现 0 次<br>`add`：80 处，参数键 `left`, `right`<br>`sub`：19 处，参数键 `left`, `right`<br>`mul`：71 处，参数键 `left`, `right`<br>`div`：3 处，参数键 `left`, `right`<br>`min`：18 处，参数键 `left`, `right`<br>`max`：11 处，参数键 `left`, `right`<br>一元函数 `abs`、`square`、`cube`、`half_negative`、`quarter_negative`、`squeeze`、`negate`、`cache`、`blend_density` 参数键都只有 `input`：True<br>`constant` 以对象形式出现 0 次；常量全部写成裸数字（在 left/right/input 等位置共 153 处）<br>`density_function_type` 注册表 44 项：`abs`, `add`, `beardifier`, `blend_alpha`, `blend_density`, `blend_offset`, `cache`, `ceil`, `clamp`, `constant`, `cube`, `distance_to_point`, `div`, `end_outer_islands`, `find_top_surface`, `floor`, `gradient`, `half_negative`, `interpolated`, `interval_select`, `lerp`, `log`, `max`, `min`, `mul`, `negate`, `noise`, `old_blended_noise`, `pow`, `quarter_negative`, `range_choice`, `reciprocal`, `round`, `shift`, `shift_a`, `shift_b`, `sign`, `slice`, `spline`, `sqrt`, `square`, `squeeze`, `sub`, `truncate`<br>对照 1.21.11：旧数据使用的类型中 26.3 已没有的：`cache_2d`, `cache_once`, `end_islands`, `flat_cache`, `invert`, `shifted_noise`, `weird_scaled_sampler`, `y_clamped_gradient`<br>1.21.11 中 `argument`/`argument1`/`argument2` 键出现 583 次；旧 `add` 参数键 `argument1`, `argument2`<br>`constant` 的对象写法（`value` 还是 `argument`）原版数据里没有出现，需反编译源码确认（`--sources`） |

## 其他记录

- `final_density` 末尾显式加 `beardifier`：`vanilla-26.3/data/minecraft/worldgen/density_function/overworld/final_density.json` 含 `minecraft:beardifier` = True
- `vanilla-26.3/data/minecraft/tags/worldgen/world_preset/normal.json` = `["minecraft:normal", "minecraft:flat", "minecraft:large_biomes", "minecraft:amplified", "minecraft:single_biome_surface"]`（“世界类型”按钮读取这个标签）
- `world_preset/normal.json` 主世界 generator = `{"biome_source": {"preset": "minecraft:overworld", "type": "minecraft:multi_noise"}, "settings": "minecraft:overworld", "type": "minecraft:noise"}`
- biome 顶层键（出现次数 / 67）：`attributes`×67, `carvers`×67, `downfall`×67, `effects`×67, `features`×67, `has_precipitation`×67, `temperature`×67, `temperature_modifier`×2
- biome 中用到的 attributes：`minecraft:audio/ambient_sounds`×5, `minecraft:audio/background_music`×43, `minecraft:audio/music_volume`×1, `minecraft:gameplay/can_pillager_patrol_spawn`×1, `minecraft:gameplay/creature_world_gen_spawn_probability`×5, `minecraft:gameplay/increased_fire_burnout`×8, `minecraft:gameplay/natural_mob_spawns`×67, `minecraft:gameplay/snow_golem_melts`×7, `minecraft:visual/ambient_particles`×4, `minecraft:visual/fog_color`×9, `minecraft:visual/sky_color`×57, `minecraft:visual/water_fog_color`×9, `minecraft:visual/water_fog_end_distance`×2
- `environment_attribute` 注册表中 fog/sky 相关：`gameplay/sky_light_level`, `visual/cloud_fog_end_distance`, `visual/fog_color`, `visual/fog_end_distance`, `visual/fog_start_distance`, `visual/sky_color`, `visual/sky_fog_end_distance`, `visual/sky_light_color`, `visual/sky_light_factor`, `visual/water_fog_color`, `visual/water_fog_end_distance`, `visual/water_fog_start_distance`
- `vanilla-26.3/reports/biome_parameters/minecraft/overworld.json` 存在：7594 条，参数键 `continentalness`, `depth`, `erosion`, `humidity`, `offset`, `temperature`, `weirdness`
- `multi_noise_biome_source_parameter_list/`：nether = `{"preset": "minecraft:nether"}`；overworld = `{"preset": "minecraft:overworld"}`
- 数据包版本：`{"resource_major": 97, "resource_minor": 1, "data_major": 121, "data_minor": 0}`

## 附录 A：密度函数类型与参数键（26.3 原版数据实际出现的）

| 类型 | 出现次数 | 参数键 |
|---|---|---|
| `abs` | 18 | `input` |
| `add` | 80 | `left`, `right` |
| `beardifier` | 11 | （无） |
| `blend_alpha` | 9 | （无） |
| `blend_density` | 11 | `input` |
| `blend_offset` | 3 | （无） |
| `cache` | 32 | `input` |
| `clamp` | 18 | `input`, `max`, `min` |
| `cube` | 2 | `input` |
| `distance_to_point` | 1 | `metric`, `point` |
| `div` | 3 | `left`, `right` |
| `end_outer_islands` | 1 | （无） |
| `find_top_surface` | 3 | `cell_height`, `density`, `lower_bound`, `upper_bound` |
| `gradient` | 37 | `axis`, `from_coordinate`, `from_value`, `to_coordinate`, `to_value` |
| `half_negative` | 3 | `input` |
| `interpolated` | 21 | `cell_size_xz`, `cell_size_y`, `input` |
| `interval_select` | 3 | `functions`, `input`, `thresholds` |
| `lerp` | 37 | `alpha`, `first`, `second` |
| `max` | 11 | `left`, `right` |
| `min` | 18 | `left`, `right` |
| `mul` | 71 | `left`, `right` |
| `negate` | 1 | `input` |
| `noise` | 67 | `noise`, `xz_scale`, `y_scale`<br>`noise`, `shift_x`, `shift_z`, `xz_scale`, `y_scale` |
| `old_blended_noise` | 3 | `smear_scale_multiplier`, `xz_factor`, `xz_scale`, `y_factor`, `y_scale` |
| `quarter_negative` | 6 | `input` |
| `range_choice` | 21 | `input`, `max_exclusive`, `min_inclusive`, `when_in_range`, `when_out_of_range` |
| `shift_a` | 1 | `noise` |
| `shift_b` | 1 | `noise` |
| `slice` | 1 | `axis`, `coordinate`, `input` |
| `spline` | 9 | `spline` |
| `square` | 3 | `input` |
| `squeeze` | 11 | `input` |
| `sub` | 19 | `left`, `right` |

## 附录 B：material rule / condition 类型与参数键

| 种类 | 类型 | 原版数据中出现的参数键 |
|---|---|---|
| rule | `bandlands` | （原版未用或无参数） |
| rule | `block` | `result_state` |
| rule | `condition` | `if_true`, `then_run` |
| rule | `ore_vein` | `density`, `filler_block`, `filler_gap`, `ore_block`, `raw_ore_block`, `raw_ore_chance`, `richness` |
| rule | `sequence` | `sequence` |
| condition | `above_preliminary_surface` | （原版未用或无参数） |
| condition | `biome` | `biome_is` |
| condition | `hole` | （原版未用或无参数） |
| condition | `noise_threshold` | `is_3d`, `max_threshold`, `min_threshold`, `noise` |
| condition | `not` | `invert` |
| condition | `steep` | （原版未用或无参数） |
| condition | `stone_depth` | `add_surface_depth`, `offset`, `secondary_depth_range`, `surface_type` |
| condition | `temperature` | （原版未用或无参数） |
| condition | `vertical_gradient` | `false_at_and_above`, `random_name`, `true_at_and_below` |
| condition | `water` | `add_stone_depth`, `offset`, `surface_depth_multiplier` |
| condition | `y_above` | `add_stone_depth`, `anchor`, `surface_depth_multiplier` |
