package dev.karstscape.debug;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import dev.karstscape.Karstscape;
import net.minecraft.core.Holder;
import net.minecraft.core.QuartPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.NoiseColumn;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.biome.BiomeResolver;
import net.minecraft.world.level.biome.Climate;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.densityfunction.DensityFunction;
import net.minecraft.world.level.levelgen.densityfunction.SamplerContext;

import javax.imageio.ImageIO;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Font;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ForkJoinPool;
import java.util.function.Predicate;
import java.util.stream.IntStream;

/**
 * Samples terrain without generating chunks (ChunkGenerator#getBaseColumn: noise terrain + aquifers, no
 * surface rules or features) on a square grid and writes height.png, biome.png, slope.png, mask.png and
 * stats.json. Everything except timing.json is deterministic for a given seed and arguments.
 */
public final class TerrainExport {
	public static final String KARST_BIOME = "karstscape:tower_karst";
	private static final int MAX_GRID = 4097;
	private static final int LEGEND_W = 230;
	private static final int H_MIN = 48;   // 1 gray level per half block between H_MIN and H_MAX
	private static final int H_MAX = 176;  // higher land is drawn white
	private static final float SLOPE_STEEP_DEG = 70f;

	private final ServerLevel level;
	private final int cx;
	private final int cz;
	private final int radius;
	private final int step;
	private final Path outDir;

	public TerrainExport(ServerLevel level, int cx, int cz, int radius, int step, Path outDir) {
		this.level = level;
		this.cx = cx;
		this.cz = cz;
		this.radius = radius;
		this.step = step;
		this.outDir = outDir;
	}

	/** Runs the export (blocking, worker threads) and returns a one-line summary. */
	public String run() throws Exception {
		long t0 = System.nanoTime();
		System.setProperty("java.awt.headless", System.getProperty("java.awt.headless", "true"));
		int n = 2 * (radius / step) + 1;
		if (n > MAX_GRID) {
			throw new IllegalArgumentException("grid %d > %d; use a larger step".formatted(n, MAX_GRID));
		}
		int x0 = cx - (n / 2) * step;
		int z0 = cz - (n / 2) * step;

		ChunkGenerator generator = level.getChunkSource().getGenerator();
		RandomState randomState = level.getChunkSource().randomState();
		String settingsId = generator instanceof NoiseBasedChunkGenerator noise
				? noise.generatorSettings().unwrapKey().map(k -> k.identifier().toString()).orElse("inline")
				: generator.getClass().getSimpleName();
		DensityFunction mask = findMask(settingsId);
		int seaLevel = generator.getSeaLevel();
		Predicate<BlockState> notAir = Heightmap.Types.WORLD_SURFACE_WG.isOpaque();
		Predicate<BlockState> solid = Heightmap.Types.OCEAN_FLOOR_WG.isOpaque();

		int size = n * n;
		int[] surface = new int[size];
		int[] floor = new int[size];
		float[] slope = new float[size];
		float[] maskValue = new float[size];
		int[] biome = new int[size];
		Map<String, Integer> biomeIds = new ConcurrentHashMap<>();
		List<String> biomeNames = java.util.Collections.synchronizedList(new ArrayList<>());

		int threads = Math.max(1, Runtime.getRuntime().availableProcessors() - 2);
		ForkJoinPool pool = new ForkJoinPool(threads);
		try {
			pool.submit(() -> IntStream.range(0, n).parallel().forEach(iz -> {
				Climate.Sampler climate = randomState.createClimateSampler(SamplerContext.EMPTY_UNCACHED);
				BiomeResolver resolver = generator.getBiomeSource().createResolver(climate);
				for (int ix = 0; ix < n; ix++) {
					int i = iz * n + ix;
					int x = x0 + ix * step;
					int z = z0 + iz * step;
					NoiseColumn column = generator.getBaseColumn(x, z, level, randomState);
					int top = topY(column, notAir);
					int ground = topY(column, solid);
					surface[i] = top;
					floor[i] = ground;
					int gx = generator.getBaseHeight(x + 1, z, Heightmap.Types.OCEAN_FLOOR_WG, level, randomState) - 1;
					int gz = generator.getBaseHeight(x, z + 1, Heightmap.Types.OCEAN_FLOOR_WG, level, randomState) - 1;
					double grad = Math.hypot(gx - ground, gz - ground);
					slope[i] = (float) Math.toDegrees(Math.atan(grad));
					maskValue[i] = mask == null ? 0f
							: randomState.sampleBlockValueUncached(mask, x, Math.max(top, seaLevel), z);
					Holder<Biome> b = resolver.getNoiseBiome(QuartPos.fromBlock(x), QuartPos.fromBlock(top), QuartPos.fromBlock(z));
					String name = b.unwrapKey().map(k -> k.identifier().toString()).orElse("?");
					biome[i] = biomeIds.computeIfAbsent(name, key -> {
						synchronized (biomeNames) {
							biomeNames.add(key);
							return biomeNames.size() - 1;
						}
					});
				}
			})).get();
		} finally {
			pool.shutdown();
		}
		long tSample = System.nanoTime();

		// Biome ids depend on thread timing; remap to sorted names so outputs are deterministic.
		String[] sortedNames = biomeNames.toArray(new String[0]);
		Arrays.sort(sortedNames);
		Map<String, Integer> sortedIndex = new HashMap<>();
		for (int k = 0; k < sortedNames.length; k++) {
			sortedIndex.put(sortedNames[k], k);
		}
		int[] remap = new int[biomeNames.size()];
		for (int k = 0; k < biomeNames.size(); k++) {
			remap[k] = sortedIndex.get(biomeNames.get(k));
		}
		for (int i = 0; i < size; i++) {
			biome[i] = remap[biome[i]];
		}

		Files.createDirectories(outDir);
		Grid g = new Grid(n, step, x0, z0, seaLevel, surface, floor, slope, maskValue, biome, sortedNames);
		writeHeight(g);
		writeBiome(g);
		writeSlope(g);
		writeMask(g);
		JsonObject stats = stats(g, settingsId, mask != null);
		Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
		try (Writer w = Files.newBufferedWriter(outDir.resolve("stats.json"), StandardCharsets.UTF_8)) {
			gson.toJson(stats, w);
		}
		JsonObject timing = new JsonObject();
		timing.addProperty("sample_seconds", (tSample - t0) / 1e9);
		timing.addProperty("total_seconds", (System.nanoTime() - t0) / 1e9);
		timing.addProperty("threads", threads);
		try (Writer w = Files.newBufferedWriter(outDir.resolve("timing.json"), StandardCharsets.UTF_8)) {
			gson.toJson(timing, w);
		}
		JsonObject karst = stats.getAsJsonObject("karst_biome");
		String summary = "%s (%dx%d samples, karst %.2f%% of land, %.1fs)".formatted(
				outDir, n, n, 100 * karst.get("fraction_of_land").getAsDouble(), (System.nanoTime() - t0) / 1e9);
		Karstscape.LOGGER.info("Karstscape export done: {}", summary);
		return summary;
	}

	private DensityFunction findMask(String settingsId) {
		Identifier settings = Identifier.tryParse(settingsId);
		if (settings == null || !settings.getNamespace().equals(Karstscape.MOD_ID)) {
			return null;
		}
		Identifier id = Identifier.fromNamespaceAndPath(Karstscape.MOD_ID, settings.getPath() + "/mask");
		Optional<Holder.Reference<DensityFunction>> ref = level.getServer().registryAccess()
				.lookupOrThrow(Registries.DENSITY_FUNCTION).get(id);
		return ref.map(Holder::value).orElse(null);
	}

	private static int topY(NoiseColumn column, Predicate<BlockState> test) {
		for (int y = 400; y >= -128; y--) {
			if (test.test(column.getBlock(y))) {
				return y;
			}
		}
		return -129;
	}

	private record Grid(int n, int step, int x0, int z0, int seaLevel, int[] surface, int[] floor, float[] slope,
						float[] mask, int[] biome, String[] biomeNames) {
		boolean water(int i) {
			return surface[i] > floor[i];
		}

		boolean land(int i) {
			return !water(i);
		}
	}

	// ------------------------------------------------------------- images ---

	private BufferedImage canvas(Grid g) {
		BufferedImage img = new BufferedImage(g.n() + LEGEND_W, Math.max(g.n(), 360), BufferedImage.TYPE_INT_RGB);
		Graphics2D gr = img.createGraphics();
		gr.setColor(new Color(24, 24, 24));
		gr.fillRect(0, 0, img.getWidth(), img.getHeight());
		gr.dispose();
		return img;
	}

	private Graphics2D legend(BufferedImage img, Grid g, String title) {
		Graphics2D gr = img.createGraphics();
		gr.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
		gr.setFont(new Font(Font.SANS_SERIF, Font.PLAIN, 12));
		gr.setColor(Color.WHITE);
		int lx = g.n() + 10;
		gr.drawString(title, lx, 16);
		gr.drawString("center %d, %d".formatted(cx, cz), lx, 32);
		gr.drawString("%d blocks/px, %dx%d px".formatted(g.step(), g.n(), g.n()), lx, 48);
		gr.drawString("N up, x right", lx, 64);
		return gr;
	}

	private static int gray(int v) {
		v = Math.max(0, Math.min(255, v));
		return (v << 16) | (v << 8) | v;
	}

	private static int heightGray(int y) {
		return gray((int) Math.round((y - H_MIN) * 255.0 / (H_MAX - H_MIN)));
	}

	private static int waterColor(Grid g, int i) {
		int depth = g.surface()[i] - g.floor()[i];
		int b = Math.max(70, 200 - depth * 4);
		return (20 << 16) | (40 << 8) | b;
	}

	private void writeHeight(Grid g) throws IOException {
		BufferedImage img = canvas(g);
		for (int i = 0; i < g.n() * g.n(); i++) {
			img.setRGB(i % g.n(), i / g.n(), g.water(i) ? waterColor(g, i) : heightGray(g.surface()[i]));
		}
		Graphics2D gr = legend(img, g, "surface height (land, gray; >= y" + H_MAX + " white)");
		int lx = g.n() + 10;
		for (int y = H_MAX; y >= H_MIN; y -= 2) {
			int py = 90 + (H_MAX - y);
			gr.setColor(new Color(heightGray(y)));
			gr.fillRect(lx, py, 24, 2);
		}
		gr.setColor(Color.WHITE);
		for (int y = H_MIN; y <= H_MAX; y += 16) {
			gr.drawString("y " + y, lx + 30, 90 + (H_MAX - y) + 4);
		}
		gr.setColor(new Color(waterColor(g, 0) | 0x0000C8));
		gr.fillRect(lx, 90 + (H_MAX - H_MIN) + 14, 24, 10);
		gr.setColor(Color.WHITE);
		gr.drawString("water (darker = deeper)", lx + 30, 90 + (H_MAX - H_MIN) + 23);
		gr.dispose();
		ImageIO.write(img, "png", outDir.resolve("height.png").toFile());
	}

	private static int biomeColor(String name) {
		if (name.equals(KARST_BIOME)) {
			return 0xFF00FF;
		}
		if (name.startsWith("karstscape:")) {
			return 0xFF80FF;
		}
		String path = name.substring(name.indexOf(':') + 1);
		if (path.contains("deep") && path.contains("ocean")) return 0x102A6B;
		if (path.contains("ocean")) return 0x2050B0;
		if (path.contains("river")) return 0x40A0F0;
		if (path.contains("beach") || path.contains("shore")) return 0xE8D898;
		if (path.contains("desert")) return 0xD8C060;
		if (path.contains("badlands")) return 0xC06030;
		if (path.contains("savanna")) return 0xA0A040;
		if (path.contains("jungle")) return 0x208020;
		if (path.contains("swamp")) return 0x4A6038;
		if (path.contains("snow") || path.contains("frozen") || path.contains("ice")) return 0xE0F0F8;
		if (path.contains("taiga")) return 0x305848;
		if (path.contains("forest") || path.contains("grove")) return 0x307830;
		if (path.contains("plains") || path.contains("meadow")) return 0x80B050;
		if (path.contains("peaks") || path.contains("slopes") || path.contains("windswept")) return 0x909090;
		int h = name.hashCode();
		return ((h & 0x7F) + 64) << 16 | (((h >> 8) & 0x7F) + 64) << 8 | (((h >> 16) & 0x7F) + 64);
	}

	private void writeBiome(Grid g) throws IOException {
		BufferedImage img = canvas(g);
		int[] colors = new int[g.biomeNames().length];
		int[] counts = new int[g.biomeNames().length];
		for (int k = 0; k < colors.length; k++) {
			colors[k] = biomeColor(g.biomeNames()[k]);
		}
		for (int i = 0; i < g.n() * g.n(); i++) {
			img.setRGB(i % g.n(), i / g.n(), colors[g.biome()[i]]);
			counts[g.biome()[i]]++;
		}
		Graphics2D gr = legend(img, g, "biome at surface (karst = magenta)");
		Integer[] order = new Integer[colors.length];
		for (int k = 0; k < order.length; k++) order[k] = k;
		Arrays.sort(order, (a, b) -> counts[b] != counts[a] ? counts[b] - counts[a] : a - b);
		int py = 84;
		for (int k : order) {
			if (py > img.getHeight() - 10) break;
			gr.setColor(new Color(colors[k]));
			gr.fillRect(g.n() + 10, py - 9, 12, 10);
			gr.setColor(Color.WHITE);
			gr.drawString("%s %.1f%%".formatted(g.biomeNames()[k].replace("minecraft:", ""),
					100.0 * counts[k] / (g.n() * g.n())), g.n() + 28, py);
			py += 14;
		}
		gr.dispose();
		ImageIO.write(img, "png", outDir.resolve("biome.png").toFile());
	}

	private void writeSlope(Grid g) throws IOException {
		BufferedImage img = canvas(g);
		for (int i = 0; i < g.n() * g.n(); i++) {
			int rgb;
			if (g.water(i)) {
				rgb = 0x203060;
			} else if (g.slope()[i] >= SLOPE_STEEP_DEG) {
				rgb = 0xFF2020;
			} else {
				rgb = gray((int) (g.slope()[i] / SLOPE_STEEP_DEG * 230));
			}
			img.setRGB(i % g.n(), i / g.n(), rgb);
		}
		Graphics2D gr = legend(img, g, "slope (per-block neighbours)");
		int lx = g.n() + 10;
		for (int d = 0; d < 70; d++) {
			gr.setColor(new Color(gray((int) (d / SLOPE_STEEP_DEG * 230))));
			gr.fillRect(lx, 90 + (70 - d) * 2, 24, 2);
		}
		gr.setColor(new Color(0xFF2020));
		gr.fillRect(lx, 80, 24, 10);
		gr.setColor(Color.WHITE);
		gr.drawString(">= 70 deg", lx + 30, 89);
		gr.drawString("40 deg", lx + 30, 90 + 30 * 2 + 4);
		gr.drawString("0 deg", lx + 30, 90 + 70 * 2 + 4);
		gr.dispose();
		ImageIO.write(img, "png", outDir.resolve("slope.png").toFile());
	}

	private void writeMask(Grid g) throws IOException {
		BufferedImage img = canvas(g);
		int karst = Arrays.asList(g.biomeNames()).indexOf(KARST_BIOME);
		int n = g.n();
		for (int i = 0; i < n * n; i++) {
			int v = (int) (Math.max(0f, Math.min(1f, g.mask()[i])) * 255);
			int rgb = g.water(i) ? (v / 3) << 16 | (v / 3) << 8 | 90 : gray(v);
			int x = i % n;
			int z = i / n;
			boolean edge = karst >= 0 && ((x + 1 < n && (g.biome()[i] == karst) != (g.biome()[i + 1] == karst))
					|| (z + 1 < n && (g.biome()[i] == karst) != (g.biome()[i + n] == karst)));
			img.setRGB(x, z, edge ? 0xFF00FF : rgb);
		}
		Graphics2D gr = legend(img, g, "terrain mask (white = 1)");
		gr.setColor(new Color(0xFF00FF));
		gr.setStroke(new BasicStroke(2));
		gr.drawLine(n + 10, 90, n + 34, 90);
		gr.setColor(Color.WHITE);
		gr.drawString("tower_karst biome border", n + 40, 94);
		gr.dispose();
		ImageIO.write(img, "png", outDir.resolve("mask.png").toFile());
	}

	// -------------------------------------------------------------- stats ---

	private JsonObject stats(Grid g, String settingsId, boolean hasMask) {
		int n = g.n();
		int size = n * n;
		int karst = Arrays.asList(g.biomeNames()).indexOf(KARST_BIOME);
		JsonObject o = new JsonObject();
		o.addProperty("dimension", level.dimension().identifier().toString());
		o.addProperty("noise_settings", settingsId);
		o.addProperty("seed", level.getSeed());
		o.addProperty("center_x", cx);
		o.addProperty("center_z", cz);
		o.addProperty("radius", radius);
		o.addProperty("step", g.step());
		o.addProperty("grid", n);
		o.addProperty("source", "ChunkGenerator#getBaseColumn (noise terrain + aquifers; no surface rules, features, structures)");

		int land = 0, karstLand = 0, karstAll = 0, maskPos = 0, maskOne = 0;
		List<Integer> hAll = new ArrayList<>(), hCore = new ArrayList<>(), hEdge = new ArrayList<>(), hOut = new ArrayList<>();
		List<Float> sCore = new ArrayList<>(), sEdge = new ArrayList<>(), sLand = new ArrayList<>();
		int steep = 0;
		for (int i = 0; i < size; i++) {
			boolean isKarst = g.biome()[i] == karst;
			if (isKarst) karstAll++;
			if (!g.land(i)) continue;
			land++;
			float m = g.mask()[i];
			int h = g.surface()[i];
			hAll.add(h);
			sLand.add(g.slope()[i]);
			if (g.slope()[i] >= SLOPE_STEEP_DEG) steep++;
			if (isKarst) karstLand++;
			if (m > 0f) maskPos++;
			if (m >= 0.999f) {
				maskOne++;
				hCore.add(h);
				sCore.add(g.slope()[i]);
			} else if (m > 0f) {
				hEdge.add(h);
				sEdge.add(g.slope()[i]);
			} else {
				hOut.add(h);
			}
		}
		o.addProperty("samples", size);
		o.addProperty("land_samples", land);
		o.addProperty("land_fraction", ratio(land, size));

		JsonObject kb = new JsonObject();
		kb.addProperty("samples", karstAll);
		kb.addProperty("land_samples", karstLand);
		kb.addProperty("fraction_of_land", ratio(karstLand, land));
		kb.addProperty("fraction_of_all", ratio(karstAll, size));
		o.add("karst_biome", kb);

		JsonObject mk = new JsonObject();
		mk.addProperty("present", hasMask);
		mk.addProperty("positive_fraction_of_land", ratio(maskPos, land));
		mk.addProperty("full_fraction_of_land", ratio(maskOne, land));
		mk.addProperty("positive_outside_karst_biome", countMaskOutsideBiome(g, karst));
		o.add("mask", mk);

		JsonObject heights = new JsonObject();
		heights.add("land", percentiles(hAll));
		heights.add("karst_core_mask_1", percentiles(hCore));
		heights.add("karst_edge_mask_0_1", percentiles(hEdge));
		heights.add("outside_mask", percentiles(hOut));
		o.add("surface_height", heights);

		JsonObject slopes = new JsonObject();
		slopes.add("land_deg", percentilesF(sLand));
		slopes.add("karst_core_deg", percentilesF(sCore));
		slopes.add("karst_edge_deg", percentilesF(sEdge));
		slopes.addProperty("land_fraction_ge_70deg", ratio(steep, land));
		o.add("slope", slopes);

		o.add("karst_regions", regions(g, i -> g.biome()[i] == karst));
		o.add("karst_cores_mask_1", regions(g, i -> g.mask()[i] >= 0.999f));
		o.add("edge_transition", edgeTransition(g));
		o.add("seams", seams(g));

		int[] counts = new int[g.biomeNames().length];
		for (int i = 0; i < size; i++) counts[g.biome()[i]]++;
		JsonObject biomes = new JsonObject();
		Integer[] order = new Integer[counts.length];
		for (int k = 0; k < order.length; k++) order[k] = k;
		Arrays.sort(order, (a, b) -> counts[b] != counts[a] ? counts[b] - counts[a] : a - b);
		for (int k : order) biomes.addProperty(g.biomeNames()[k], ratio(counts[k], size));
		o.add("biome_fractions", biomes);
		return o;
	}

	private static int countMaskOutsideBiome(Grid g, int karst) {
		int c = 0;
		for (int i = 0; i < g.n() * g.n(); i++) {
			if (g.mask()[i] > 0f && g.biome()[i] != karst && g.land(i)) c++;
		}
		return c;
	}

	/** 4-connected components of the grid cells matching `in`: count, area distribution, largest centroids. */
	private static JsonObject regions(Grid g, java.util.function.IntPredicate in) {
		int n = g.n();
		int[] label = new int[n * n];
		int[] stack = new int[n * n];
		List<long[]> comps = new ArrayList<>(); // {cells, sumX, sumZ}
		for (int start = 0; start < n * n; start++) {
			if (label[start] != 0 || !in.test(start)) continue;
			int id = comps.size() + 1;
			long cells = 0, sx = 0, sz = 0;
			int top = 0;
			stack[top++] = start;
			label[start] = id;
			while (top > 0) {
				int i = stack[--top];
				int x = i % n, z = i / n;
				cells++;
				sx += x;
				sz += z;
				int[] nb = {x > 0 ? i - 1 : -1, x + 1 < n ? i + 1 : -1, z > 0 ? i - n : -1, z + 1 < n ? i + n : -1};
				for (int j : nb) {
					if (j >= 0 && label[j] == 0 && in.test(j)) {
						label[j] = id;
						stack[top++] = j;
					}
				}
			}
			comps.add(new long[]{cells, sx, sz});
		}
		double cellKm2 = (double) g.step() * g.step() / 1e6;
		comps.sort((a, b) -> a[0] != b[0] ? Long.compare(b[0], a[0]) : Long.compare(a[1], b[1]));
		JsonObject o = new JsonObject();
		o.addProperty("count", comps.size());
		long ge1km2 = comps.stream().filter(c -> c[0] * cellKm2 >= 1.0).count();
		o.addProperty("count_ge_1km2", ge1km2);
		if (!comps.isEmpty()) {
			double[] areas = comps.stream().mapToDouble(c -> c[0] * cellKm2).sorted().toArray();
			o.addProperty("area_km2_p50", Math.round(areas[(areas.length - 1) / 2] * 1000) / 1000.0);
			o.addProperty("area_km2_max", Math.round(areas[areas.length - 1] * 1000) / 1000.0);
			com.google.gson.JsonArray largest = new com.google.gson.JsonArray();
			for (int k = 0; k < Math.min(5, comps.size()); k++) {
				long[] c = comps.get(k);
				JsonObject e = new JsonObject();
				e.addProperty("area_km2", Math.round(c[0] * cellKm2 * 1000) / 1000.0);
				e.addProperty("centroid_x", g.x0() + Math.round((double) c[1] / c[0] * g.step()));
				e.addProperty("centroid_z", g.z0() + Math.round((double) c[2] / c[0] * g.step()));
				largest.add(e);
			}
			o.add("largest", largest);
		}
		return o;
	}

	/** Height steps between neighbouring land samples: in the mask ramp vs elsewhere (per sample step). */
	private static JsonObject edgeTransition(Grid g) {
		int n = g.n();
		List<Integer> ramp = new ArrayList<>(), rest = new ArrayList<>();
		for (int z = 0; z < n; z++) {
			for (int x = 0; x < n; x++) {
				int i = z * n + x;
				for (int j : new int[]{x + 1 < n ? i + 1 : -1, z + 1 < n ? i + n : -1}) {
					if (j < 0 || !g.land(i) || !g.land(j)) continue;
					int d = Math.abs(g.surface()[i] - g.surface()[j]);
					boolean inRamp = (g.mask()[i] > 0f && g.mask()[i] < 0.999f) || (g.mask()[j] > 0f && g.mask()[j] < 0.999f);
					(inRamp ? ramp : rest).add(d);
				}
			}
		}
		JsonObject o = new JsonObject();
		o.addProperty("note", "absolute surface height difference between neighbouring samples (blocks per " + g.step() + " blocks)");
		o.add("in_mask_ramp", percentiles(ramp));
		o.add("elsewhere", percentiles(rest));
		return o;
	}

	/** Chunk-border seam check: only meaningful at step 1 (otherwise reported as skipped). */
	private static JsonObject seams(Grid g) {
		JsonObject o = new JsonObject();
		if (g.step() != 1) {
			o.addProperty("skipped", "needs step 1");
			return o;
		}
		int n = g.n();
		long crossSum = 0, innerSum = 0, crossN = 0, innerN = 0;
		int crossMax = 0;
		for (int z = 0; z < n; z++) {
			for (int x = 0; x + 1 < n; x++) {
				int i = z * n + x;
				int d = Math.abs(g.floor()[i] - g.floor()[i + 1]);
				if (Math.floorMod(g.x0() + x, 16) == 15) {
					crossSum += d;
					crossN++;
					crossMax = Math.max(crossMax, d);
				} else {
					innerSum += d;
					innerN++;
				}
			}
		}
		o.addProperty("source", "base columns (no chunk generation); real-chunk seams are checked from generated chunks in M2");
		o.addProperty("mean_step_across_chunk_border", crossN == 0 ? 0 : (double) crossSum / crossN);
		o.addProperty("mean_step_inside_chunk", innerN == 0 ? 0 : (double) innerSum / innerN);
		o.addProperty("max_step_across_chunk_border", crossMax);
		return o;
	}

	private static double ratio(long a, long b) {
		return b == 0 ? 0 : Math.round(1e6 * a / (double) b) / 1e6;
	}

	private static JsonObject percentiles(List<Integer> values) {
		JsonObject o = new JsonObject();
		o.addProperty("count", values.size());
		if (values.isEmpty()) return o;
		int[] v = values.stream().mapToInt(Integer::intValue).sorted().toArray();
		Map<String, Integer> p = new LinkedHashMap<>();
		p.put("min", v[0]);
		p.put("p5", v[(int) (0.05 * (v.length - 1))]);
		p.put("p50", v[(int) (0.50 * (v.length - 1))]);
		p.put("p95", v[(int) (0.95 * (v.length - 1))]);
		p.put("p99", v[(int) (0.99 * (v.length - 1))]);
		p.put("max", v[v.length - 1]);
		p.forEach(o::addProperty);
		return o;
	}

	private static JsonObject percentilesF(List<Float> values) {
		JsonObject o = new JsonObject();
		o.addProperty("count", values.size());
		if (values.isEmpty()) return o;
		float[] v = new float[values.size()];
		for (int k = 0; k < v.length; k++) v[k] = values.get(k);
		Arrays.sort(v);
		o.addProperty("p50", Math.round(v[(int) (0.50 * (v.length - 1))] * 10) / 10.0);
		o.addProperty("p95", Math.round(v[(int) (0.95 * (v.length - 1))] * 10) / 10.0);
		o.addProperty("p99", Math.round(v[(int) (0.99 * (v.length - 1))] * 10) / 10.0);
		o.addProperty("max", Math.round(v[v.length - 1] * 10) / 10.0);
		return o;
	}
}
