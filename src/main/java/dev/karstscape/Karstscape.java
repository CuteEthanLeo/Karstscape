package dev.karstscape;

import net.fabricmc.api.ModInitializer;
import net.minecraft.resources.Identifier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Karstscape entry point. Terrain and biomes live in data files under
 * {@code data/karstscape/worldgen}; Java only registers what data cannot express
 * (custom density functions, debug commands) — added in later milestones.
 */
public final class Karstscape implements ModInitializer {
	public static final String MOD_ID = "karstscape";
	public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

	@Override
	public void onInitialize() {
		LOGGER.info("Karstscape initialized");
	}

	public static Identifier id(String path) {
		return Identifier.fromNamespaceAndPath(MOD_ID, path);
	}
}
