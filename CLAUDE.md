# Karstscape — 给 AI 助手的开工须知

**每次开工先读 [SPEC.md](SPEC.md)**（尤其 §2 铁律、§6 里程碑、§8 汇报格式）。本文件只补充规格之外、已核实的事实。

## 铁律速记
- 写任何世界生成 JSON 前，先在 `reference/vanilla-26.3/` 找到同类原版文件，照它的字段名写。不准凭记忆。
- `reference/` 不进 git。缺失或过期时运行：`python tools/export_vanilla_data.py`（加 `--baseline 1.21.11` 可同时解出旧版对照）。
- 已核实的 26.3 字段名：[docs/worldgen-26.3-notes.md](docs/worldgen-26.3-notes.md)（人工整理）、[docs/vanilla-26.3-format-check.md](docs/vanilla-26.3-format-check.md)（`tools/verify_formats.py` 生成）。
- 只用原版方块；确定性（随机只来自种子 + 坐标哈希）；不做全局模拟。
- 小步提交，每步都要验证（编译 → 启动服务器 → 新建世界 → 无报错 → 调试导出）。

## 固定版本（2026-11-16 核实，比 SPEC §2.2 的参考值新，以此为准）
| 项 | 值 |
|---|---|
| Minecraft | 26.3（Java 25，不混淆，Mojang 官方类名） |
| Gradle | 9.8.2（wrapper） |
| Loom | `net.fabricmc.fabric-loom` 1.19.4（不重映射；无 mappings 行；`implementation` 而非 `modImplementation`；`jar` 而非 `remapJar`） |
| Fabric Loader | 0.20.1 |
| Fabric API | 0.166.2+26.3 |
| Java | 25（`options.release = 25`） |

## 项目约定
- mod id / 命名空间 `karstscape`，Java 包 `dev.karstscape`，作者 `FloorGod_MC BILI`，MIT。
- `fabric.mod.json` 的 `environment` 保持 `"*"`（写 `"server"` 会让单人模式不加载本模组）。服务端装即可，原版客户端可进服。
- 地形 / 群系尽量写成数据（JSON），Java 只做数据做不到的事。

## 工具（Python 3.12，仅标准库，在项目根目录运行）
- `tools/export_vanilla_data.py`：用 26.3 官方数据生成器导出原版数据到 `reference/vanilla-26.3/`（幂等，`--force` 重导）。
- `tools/verify_formats.py`：对照 SPEC §2.1 的格式说法核实本地导出，生成 `docs/vanilla-26.3-format-check.md`。
- `tools/headless_test.py --profile vanilla --fresh`：无界面启动专用服务器（`gradlew runHeadlessServer`，目录 `run-headless/`），固定种子新建世界、执行命令、关服，检查 ERROR / 新增 WARN / 崩溃。profile 在 `tools/headless/profiles.toml`；WARN 基线 `tools/headless/baselines/`（`--update-baseline` 只从通过的运行合并），ERROR 白名单与 WARN 忽略表各有注释说明理由。同一时间只能跑一个（锁文件）。结果在 `build/headless/`（`gradlew clean` 会清掉）。退出码：0 通过 / 1 构建或环境 / 2 超时 / 3 ERROR 或崩溃 / 4 新增或刷屏 WARN / 5 命令无预期输出 / 6 `--repeat` 结果不一致。
- `tools/client_smoke.py`：限时启动 `gradlew runSmokeClient`（目录 `run-client-smoke/`），确认资源加载完成后发关闭消息正常退出。
- 单元测试：`python -m unittest discover -s tools/tests`。
- 注意：26.3 专用服务器在 `Done (` 之前收到的控制台命令都会失败（命令源在读入时创建，世界还不存在）；未知 `level-type` 只记 WARN 并回落到 `minecraft:normal`。

## Windows 注意
- 手动跑 `gradlew runServer` 时用 `stop` 关服，不要 Ctrl+C（会留下孤儿 JVM）。
- 脚本一律 `--no-daemon`，否则游戏 JVM 挂在守护进程下，杀不干净进程树。
- 首次 `build` / `downloadAssets` / `genSources` 很慢，放后台或给足超时。
- 客户端和 headless 服务器不要同时跑（本机 15.7 GB 内存）。

## 26.3 与旧版的关键差异（详见 notes）
- `configured_feature` → `worldgen/feature`（配置内联，无 `config` 字段）；`configured_carver` → `carver`。
- noise_settings 的 `surface_rule` → `material_rule`（引用 `worldgen/material_rule/` 的 id）。
- 含水层字段进了 noise_settings 的 `aquifers` 对象；`noise_router` 只剩 8 个键。
- 密度函数：二元 `left`/`right`，一元 `input`，`constant` 用 `value`；`flat_cache`/`cache_2d`/`y_clamped_gradient`/`shifted_noise` 已删除；单精度求值；`final_density` 要显式加 `beardifier`。
- world preset 要出现在“世界类型”按钮里，需写 `data/minecraft/tags/worldgen/world_preset/normal.json`。
- 自定义群系必须用世界预设里内联的 multi_noise `biomes` 列表（参数表注册表只认原版两个硬编码预设）。
- Java 侧密度函数 API 已重写，见 [docs/26.3-worldgen-java-api.md](docs/26.3-worldgen-java-api.md)。

## 汇报
- 每个里程碑的汇报存 `docs/reports/M<n>.md`，格式按 SPEC §8。
