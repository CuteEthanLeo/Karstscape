package dev.karstscape.debug;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.context.CommandContext;
import dev.karstscape.Karstscape;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;

import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Debug commands (OP level 2):
 * <pre>
 * /karstscape export &lt;radius&gt; [&lt;centerX&gt; &lt;centerZ&gt; [&lt;step&gt;]]
 * </pre>
 * The export runs on worker threads; a chat/console line reports when it is done.
 */
public final class KarstCommands {
	private static final AtomicBoolean EXPORT_RUNNING = new AtomicBoolean(false);

	private KarstCommands() {
	}

	public static void register() {
		CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) -> register(dispatcher));
	}

	private static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
		dispatcher.register(Commands.literal("karstscape")
				.requires(Commands.hasPermission(Commands.LEVEL_GAMEMASTERS))
				.then(Commands.literal("find")
						.executes(ctx -> find(ctx.getSource())))
				.then(Commands.literal("spawn")
						.executes(ctx -> spawn(ctx.getSource())))
				.then(Commands.literal("probe")
						.then(Commands.argument("x", IntegerArgumentType.integer())
								.then(Commands.argument("z", IntegerArgumentType.integer())
										.executes(ctx -> {
											var src = ctx.getSource();
											String r = WorldChecks.probe(src.getLevel(), new net.minecraft.core.BlockPos(
													IntegerArgumentType.getInteger(ctx, "x"), 0,
													IntegerArgumentType.getInteger(ctx, "z")), 64);
											src.sendSuccess(() -> Component.literal("Karstscape probe: " + r), false);
											return 1;
										}))))
				.then(Commands.literal("check")
						.then(Commands.argument("chunkRadius", IntegerArgumentType.integer(1, 32))
								.then(Commands.argument("x", IntegerArgumentType.integer())
										.then(Commands.argument("z", IntegerArgumentType.integer())
												.executes(KarstCommands::check)))))
				.then(Commands.literal("bench")
						.then(Commands.argument("chunks", IntegerArgumentType.integer(1, 10000))
								.then(Commands.argument("x", IntegerArgumentType.integer())
										.then(Commands.argument("z", IntegerArgumentType.integer())
												.executes(KarstCommands::bench)))))
				.then(Commands.literal("export")
						.then(Commands.argument("radius", IntegerArgumentType.integer(16, 65536))
								.executes(ctx -> export(ctx, false, false))
								.then(Commands.argument("centerX", IntegerArgumentType.integer())
										.then(Commands.argument("centerZ", IntegerArgumentType.integer())
												.executes(ctx -> export(ctx, true, false))
												.then(Commands.argument("step", IntegerArgumentType.integer(1, 4096))
														.executes(ctx -> export(ctx, true, true))))))));
	}

	private static int spawn(CommandSourceStack source) {
		String r = WorldChecks.spawnInfo(source.getLevel());
		source.sendSuccess(() -> Component.literal("Karstscape spawn: " + r), false);
		return 1;
	}

	private static int find(CommandSourceStack source) {
		String r = WorldChecks.find(source.getLevel(), (int) Math.floor(source.getPosition().x),
				(int) Math.floor(source.getPosition().z), 8192);
		if (r == null) {
			source.sendFailure(Component.literal("Karstscape find: no karst core within 8192 blocks (or not a Karstscape world)"));
			return 0;
		}
		source.sendSuccess(() -> Component.literal("Karstscape find: " + r), false);
		return 1;
	}

	private static int check(CommandContext<CommandSourceStack> ctx) {
		CommandSourceStack source = ctx.getSource();
		ServerLevel level = source.getLevel();
		int r = IntegerArgumentType.getInteger(ctx, "chunkRadius");
		int x = IntegerArgumentType.getInteger(ctx, "x");
		int z = IntegerArgumentType.getInteger(ctx, "z");
		Path out = level.getServer().getServerDirectory().resolve("karstscape_export")
				.resolve("check_seed%d_x%d_z%d_r%d.json".formatted(level.getSeed(), x, z, r));
		try {
			var o = WorldChecks.check(level, x, z, r, out);
			source.sendSuccess(() -> Component.literal("Karstscape check done: %s seams %s overhangs %s real_vs_noise %s sha %s"
					.formatted(out.getFileName(), o.get("seams"), o.get("overhangs"), o.get("real_vs_noise"),
							o.get("terrain_tops_sha256").getAsString().substring(0, 16))), false);
			return 1;
		} catch (Exception e) {
			Karstscape.LOGGER.error("Karstscape check failed", e);
			source.sendFailure(Component.literal("Karstscape check failed: " + e));
			return 0;
		}
	}

	private static int bench(CommandContext<CommandSourceStack> ctx) {
		CommandSourceStack source = ctx.getSource();
		int n = IntegerArgumentType.getInteger(ctx, "chunks");
		int x = IntegerArgumentType.getInteger(ctx, "x");
		int z = IntegerArgumentType.getInteger(ctx, "z");
		double ms = WorldChecks.bench(source.getLevel(), x, z, n);
		String settings = WorldChecks.settingsId(source.getLevel().getChunkSource().getGenerator());
		source.sendSuccess(() -> Component.literal("Karstscape bench done: settings=%s chunks=%d ms_per_chunk=%.3f"
				.formatted(settings, n, ms)), false);
		return 1;
	}

	private static int export(CommandContext<CommandSourceStack> ctx, boolean hasCenter, boolean hasStep) {
		CommandSourceStack source = ctx.getSource();
		ServerLevel level = source.getLevel();
		int radius = IntegerArgumentType.getInteger(ctx, "radius");
		int cx = hasCenter ? IntegerArgumentType.getInteger(ctx, "centerX") : (int) Math.floor(source.getPosition().x);
		int cz = hasCenter ? IntegerArgumentType.getInteger(ctx, "centerZ") : (int) Math.floor(source.getPosition().z);
		int step = hasStep ? IntegerArgumentType.getInteger(ctx, "step") : Math.max(1, (2 * radius + 1023) / 1024);
		if (!EXPORT_RUNNING.compareAndSet(false, true)) {
			source.sendFailure(Component.literal("Karstscape export already running"));
			return 0;
		}
		Path outDir = level.getServer().getServerDirectory().resolve("karstscape_export")
				.resolve("%s_seed%d_x%d_z%d_r%d_s%d".formatted(level.dimension().identifier().getPath(),
						level.getSeed(), cx, cz, radius, step));
		source.sendSuccess(() -> Component.literal("Karstscape export started: radius %d around (%d, %d), step %d -> %s"
				.formatted(radius, cx, cz, step, outDir)), false);
		TerrainExport export = new TerrainExport(level, cx, cz, radius, step, outDir);
		Thread worker = new Thread(() -> {
			try {
				String summary = export.run();
				level.getServer().execute(() -> source.sendSuccess(
						() -> Component.literal("Karstscape export done: " + summary), false));
			} catch (Throwable t) {
				Karstscape.LOGGER.error("Karstscape export failed", t);
				level.getServer().execute(() -> source.sendFailure(
						Component.literal("Karstscape export failed: " + t)));
			} finally {
				EXPORT_RUNNING.set(false);
			}
		}, "Karstscape export");
		worker.setDaemon(true);
		worker.start();
		return 1;
	}
}
