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
				.then(Commands.literal("export")
						.then(Commands.argument("radius", IntegerArgumentType.integer(16, 65536))
								.executes(ctx -> export(ctx, false, false))
								.then(Commands.argument("centerX", IntegerArgumentType.integer())
										.then(Commands.argument("centerZ", IntegerArgumentType.integer())
												.executes(ctx -> export(ctx, true, false))
												.then(Commands.argument("step", IntegerArgumentType.integer(1, 4096))
														.executes(ctx -> export(ctx, true, true))))))));
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
