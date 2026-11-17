package dev.karstscape.debug;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import dev.karstscape.Karstscape;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.NoiseColumn;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.densityfunction.DensityFunction;

import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.function.Predicate;

/** Real-chunk checks (seams, overhangs, determinism hash), chunk-generation bench and region finder. */
public final class WorldChecks {
	private WorldChecks() {
	}

	private static final Predicate<BlockState> MOTION = Heightmap.Types.OCEAN_FLOOR_WG.isOpaque();

	/** Terrain block: blocks motion, not part of a tree. */
	static boolean terrain(BlockState s) {
		return MOTION.test(s) && !s.is(BlockTags.LEAVES) && !s.is(BlockTags.LOGS);
	}

	static int terrainTop(LevelChunk chunk, int x, int z, int minY, int maxY) {
		BlockPos.MutableBlockPos p = new BlockPos.MutableBlockPos();
		for (int y = maxY; y >= minY; y--) {
			if (terrain(chunk.getBlockState(p.set(x, y, z)))) return y;
		}
		return minY - 1;
	}

	/**
	 * Generates (2r+1)^2 chunks around (cx, cz) to FULL status and checks, per column:
	 * terrain top vs the noise-only base column (features may legitimately differ a little),
	 * height steps across chunk borders vs inside chunks (seams), and overhangs: air below terrain
	 * above y=minOverhangY (towers are height fields, so this should be ~0). Also returns a sha256 of all
	 * terrain tops (determinism across runs).
	 */
	public static JsonObject check(ServerLevel level, int cx, int cz, int r, Path out) throws Exception {
		long t0 = System.nanoTime();
		ChunkGenerator gen = level.getChunkSource().getGenerator();
		RandomState rs = level.getChunkSource().randomState();
		int minY = level.getMinY(), maxY = level.getMaxY();
		int ccx = Math.floorDiv(cx, 16), ccz = Math.floorDiv(cz, 16);
		int span = (2 * r + 1) * 16;
		int[] top = new int[span * span];
		int x0 = (ccx - r) * 16, z0 = (ccz - r) * 16;
		long mismatch = 0, mismatchBig = 0, overhangCols = 0, overhangBlocks = 0;
		com.google.gson.JsonArray examples = new com.google.gson.JsonArray();
		java.util.Map<String, Integer> wallBlocks = new java.util.TreeMap<>(), topBlocks = new java.util.TreeMap<>(),
				plants = new java.util.TreeMap<>();
		final int minOverhangY = 86;
		MessageDigest sha = MessageDigest.getInstance("SHA-256");
		BlockPos.MutableBlockPos p = new BlockPos.MutableBlockPos();
		for (int dz = -r; dz <= r; dz++) {
			for (int dx = -r; dx <= r; dx++) {
				LevelChunk chunk = level.getChunk(ccx + dx, ccz + dz);
				for (int lz = 0; lz < 16; lz++) {
					for (int lx = 0; lx < 16; lx++) {
						int wx = chunk.getPos().getMinBlockX() + lx, wz = chunk.getPos().getMinBlockZ() + lz;
						int t = terrainTop(chunk, lx, lz, minY, maxY);
						top[(wz - z0) * span + (wx - x0)] = t;
						census(chunk, lx, lz, t, wallBlocks, topBlocks, plants);
						boolean gap = false;
						int colGaps = 0;
						for (int y = t - 1; y > minOverhangY; y--) {
							BlockState s = chunk.getBlockState(p.set(lx, y, lz));
							if (s.isAir()) {
								if (!gap && examples.size() < 12) {
									examples.add("%d %d %d top %d mask %.2f biome %s".formatted(wx, y, wz, t,
											mask(level) == null ? 0f : rs.sampleBlockValueUncached(mask(level), wx, 64, wz),
											level.getBiome(p.set(wx, t, wz)).unwrapKey().map(k -> k.identifier().getPath()).orElse("?")));
									p.set(lx, y, lz);
								}
								gap = true;
								colGaps++;
							}
						}
						if (gap) {
							overhangCols++;
							overhangBlocks += colGaps;
						}
					}
				}
			}
		}
		// noise-only comparison on a subsample (every 4th column) to keep it quick
		for (int z = 0; z < span; z += 4) {
			for (int x = 0; x < span; x += 4) {
				NoiseColumn col = gen.getBaseColumn(x0 + x, z0 + z, level, rs);
				int base = minY - 1;
				for (int y = maxY; y >= minY; y--) {
					if (MOTION.test(col.getBlock(y))) {
						base = y;
						break;
					}
				}
				int d = Math.abs(top[z * span + x] - base);
				if (d > 0) mismatch++;
				if (d > 3) mismatchBig++;
			}
		}
		long crossSum = 0, crossN = 0, innerSum = 0, innerN = 0;
		int crossMax = 0, innerMax = 0;
		for (int z = 0; z < span; z++) {
			for (int x = 0; x < span; x++) {
				int i = z * span + x;
				sha.update((byte) top[i]);
				sha.update((byte) (top[i] >> 8));
				for (int[] nb : new int[][]{{1, 0}, {0, 1}}) {
					int nx = x + nb[0], nz = z + nb[1];
					if (nx >= span || nz >= span) continue;
					int d = Math.abs(top[i] - top[nz * span + nx]);
					boolean cross = nb[0] == 1 ? (x % 16 == 15) : (z % 16 == 15);
					if (cross) {
						crossSum += d;
						crossN++;
						crossMax = Math.max(crossMax, d);
					} else {
						innerSum += d;
						innerN++;
						innerMax = Math.max(innerMax, d);
					}
				}
			}
		}
		JsonObject o = new JsonObject();
		o.addProperty("noise_settings", settingsId(gen));
		o.addProperty("seed", level.getSeed());
		o.addProperty("center_chunk", ccx + "," + ccz);
		o.addProperty("chunks", (2 * r + 1) * (2 * r + 1));
		o.addProperty("columns", span * span);
		o.addProperty("terrain_tops_sha256", HexFormat.of().formatHex(sha.digest()));
		JsonObject seams = new JsonObject();
		seams.addProperty("mean_step_across_chunk_border", round3((double) crossSum / crossN));
		seams.addProperty("mean_step_inside_chunk", round3((double) innerSum / innerN));
		seams.addProperty("max_step_across_chunk_border", crossMax);
		seams.addProperty("max_step_inside_chunk", innerMax);
		o.add("seams", seams);
		JsonObject vsNoise = new JsonObject();
		vsNoise.addProperty("columns_compared", (span / 4) * (span / 4));
		vsNoise.addProperty("differ", mismatch);
		vsNoise.addProperty("differ_by_more_than_3", mismatchBig);
		vsNoise.addProperty("note", "real chunk terrain top (no leaves/logs) vs noise-only base column; surface features may differ slightly");
		o.add("real_vs_noise", vsNoise);
		JsonObject over = new JsonObject();
		over.addProperty("above_y", minOverhangY);
		over.addProperty("columns_with_air_below_terrain_top", overhangCols);
		over.addProperty("air_blocks_below_terrain_top", overhangBlocks);
		over.add("examples", examples);
		o.add("overhangs", over);
		o.add("tower_wall_blocks", topN(wallBlocks, 12));
		o.add("tower_top_blocks", topN(topBlocks, 8));
		o.add("plants_above_y60", topN(plants, 16));
		Files.createDirectories(out.getParent());
		try (Writer w = Files.newBufferedWriter(out, StandardCharsets.UTF_8)) {
			new GsonBuilder().setPrettyPrinting().create().toJson(o, w);
		}
		Karstscape.LOGGER.info("Karstscape check took {} s", (System.nanoTime() - t0) / 1e9);
		return o;
	}

	private static final int TOWER_Y = 92;

	/**
	 * World spawn safety: the spawn column, whether it stands on tower rock (y >= TOWER_Y) or inside
	 * terrain, the highest terrain within 16 blocks and the biome. "OK" means on the ground, not on a tower.
	 */
	public static String spawnInfo(ServerLevel level) {
		return probe(level, level.getRespawnData().globalPos().pos(), 16);
	}

	/**
	 * Column check at a position: terrain top, highest terrain within `radius`, biome, block at pos.
	 * BAD = standing inside terrain, or on tower rock (karst biome and terrain top >= TOWER_Y).
	 */
	public static String probe(ServerLevel level, BlockPos pos, int radius) {
		LevelChunk chunk = level.getChunk(pos.getX() >> 4, pos.getZ() >> 4);
		int top = terrainTop(chunk, pos.getX() & 15, pos.getZ() & 15, level.getMinY(), level.getMaxY());
		int maxNear = Integer.MIN_VALUE;
		int towersNear = 0;
		for (int dz = -radius; dz <= radius; dz += 4) {
			for (int dx = -radius; dx <= radius; dx += 4) {
				int x = pos.getX() + dx, z = pos.getZ() + dz;
				LevelChunk c = level.getChunk(x >> 4, z >> 4);
				int t = terrainTop(c, x & 15, z & 15, level.getMinY(), level.getMaxY());
				maxNear = Math.max(maxNear, t);
				if (t >= TOWER_Y && isKarst(level, new BlockPos(x, t, z))) towersNear++;
			}
		}
		BlockPos at = new BlockPos(pos.getX(), pos.getY() == 0 ? top + 1 : pos.getY(), pos.getZ());
		BlockState state = level.getBlockState(at);
		boolean inside = terrain(state);
		boolean onTower = top >= TOWER_Y && isKarst(level, new BlockPos(pos.getX(), top, pos.getZ()));
		String biome = level.getBiome(at).unwrapKey().map(k -> k.identifier().toString()).orElse("?");
		return "%s pos %d %d %d terrain_top %d max_terrain_within_%d %d tower_samples_within_%d %d biome %s block %s"
				.formatted(onTower || inside ? "BAD" : "OK", at.getX(), at.getY(), at.getZ(), top, radius, maxNear,
						radius, towersNear, biome, name(state));
	}

	private static boolean isKarst(ServerLevel level, BlockPos pos) {
		return level.getBiome(pos).unwrapKey().map(k -> k.identifier().toString().equals(TerrainExport.KARST_BIOME))
				.orElse(false);
	}

	private static String name(BlockState s) {
		return s.getBlock().builtInRegistryHolder().unwrapKey().map(k -> k.identifier().getPath()).orElse("?");
	}

	/** Blocks on exposed tower wall faces (y >= TOWER_Y, air beside), tower top blocks, plants. */
	private static void census(LevelChunk chunk, int lx, int lz, int t, java.util.Map<String, Integer> walls,
							   java.util.Map<String, Integer> tops, java.util.Map<String, Integer> plants) {
		BlockPos.MutableBlockPos p = new BlockPos.MutableBlockPos();
		if (t >= TOWER_Y) tops.merge(name(chunk.getBlockState(p.set(lx, t, lz))), 1, Integer::sum);
		for (int y = Math.min(t + 40, chunk.getMaxY()); y > 60; y--) {
			BlockState s = chunk.getBlockState(p.set(lx, y, lz));
			if (s.isAir()) continue;
			if (!terrain(s)) {
				plants.merge(name(s), 1, Integer::sum);
				continue;
			}
			if (y < TOWER_Y || y >= t || lx == 0 || lz == 0 || lx == 15 || lz == 15) continue;
			boolean exposed = false;
			for (int[] d : new int[][]{{1, 0}, {-1, 0}, {0, 1}, {0, -1}}) {
				BlockState n = chunk.getBlockState(p.set(lx + d[0], y, lz + d[1]));
				if (n.isAir() || !terrain(n)) exposed = true;
			}
			p.set(lx, y, lz);
			if (exposed) walls.merge(name(s), 1, Integer::sum);
		}
	}

	private static JsonObject topN(java.util.Map<String, Integer> m, int n) {
		JsonObject o = new JsonObject();
		long total = m.values().stream().mapToLong(Integer::longValue).sum();
		m.entrySet().stream().sorted((a, b) -> b.getValue() - a.getValue()).limit(n)
				.forEach(e -> o.addProperty(e.getKey(), Math.round(1000.0 * e.getValue() / Math.max(1, total)) / 10.0 + "% (" + e.getValue() + ")"));
		return o;
	}

	/** Generates `chunks` chunks (warm-up area first) synchronously and returns ms per chunk. */
	public static double bench(ServerLevel level, int x, int z, int chunks) {
		int side = (int) Math.ceil(Math.sqrt(chunks));
		int cx = Math.floorDiv(x, 16), cz = Math.floorDiv(z, 16);
		// warm-up: a quarter of the chunks, 64 chunks away
		int warm = Math.max(1, side / 2);
		for (int i = 0; i < warm * warm; i++) {
			level.getChunk(cx + 64 + i % warm, cz + i / warm);
		}
		long t0 = System.nanoTime();
		int done = 0;
		for (int i = 0; i < side * side && done < chunks; i++, done++) {
			level.getChunk(cx - side / 2 + i % side, cz - side / 2 + i / side);
		}
		return (System.nanoTime() - t0) / 1e6 / done;
	}

	/** Mask density function of the active karstscape noise settings, or null. */
	static DensityFunction mask(ServerLevel level) {
		String id = settingsId(level.getChunkSource().getGenerator());
		Identifier s = Identifier.tryParse(id);
		if (s == null || !s.getNamespace().equals(Karstscape.MOD_ID)) return null;
		return level.getServer().registryAccess().lookupOrThrow(Registries.DENSITY_FUNCTION)
				.get(Karstscape.id(s.getPath() + "/mask")).map(Holder::value).orElse(null);
	}

	static String settingsId(ChunkGenerator gen) {
		return gen instanceof NoiseBasedChunkGenerator noise
				? noise.generatorSettings().unwrapKey().map(k -> k.identifier().toString()).orElse("inline")
				: gen.getClass().getSimpleName();
	}

	/**
	 * Nearest karst core (mask = 1, at least 0.5 km2) around (x, z) on a 32-block grid, its centroid,
	 * and an aerial viewpoint: the core edge cell nearest to the start, 140 blocks above the plain,
	 * looking at the centroid. Returns null if no core within `radius`.
	 */
	public static String find(ServerLevel level, int x, int z, int radius) {
		DensityFunction mask = mask(level);
		if (mask == null) return null;
		RandomState rs = level.getChunkSource().randomState();
		int step = 32;
		java.util.Set<Long> done = new java.util.HashSet<>();
		for (int ring = 0; ring * step <= radius; ring++) {
			for (int i = -ring; i <= ring; i++) {
				for (int[] d : new int[][]{{i, -ring}, {i, ring}, {-ring, i}, {ring, i}}) {
					int px = x + d[0] * step, pz = z + d[1] * step;
					if (done.contains(key(px, pz)) || rs.sampleBlockValueUncached(mask, px, 64, pz) < 0.999f) continue;
					String r = core(rs, mask, px, pz, x, z, step, done);
					if (r != null) return r;
				}
			}
		}
		return null;
	}

	private static long key(int x, int z) {
		return ((long) x << 32) ^ (z & 0xFFFFFFFFL);
	}

	/** Flood the mask=1 core containing (fx, fz) on the grid; returns the report if it is >= 0.5 km2. */
	private static String core(RandomState rs, DensityFunction mask, int fx, int fz, int x, int z, int step,
							   java.util.Set<Long> done) {
		int lim = 1536 / step;
		java.util.ArrayDeque<int[]> queue = new java.util.ArrayDeque<>();
		queue.add(new int[]{fx, fz});
		done.add(key(fx, fz));
		long sx = 0, sz = 0, cells = 0;
		int ex = fx, ez = fz;
		double eBest = Double.MAX_VALUE;
		while (!queue.isEmpty()) {
			int[] c = queue.poll();
			cells++;
			sx += c[0];
			sz += c[1];
			boolean edge = false;
			for (int[] d : new int[][]{{1, 0}, {-1, 0}, {0, 1}, {0, -1}}) {
				int nx = c[0] + d[0] * step, nz = c[1] + d[1] * step;
				if (Math.abs(nx - fx) > lim * step || Math.abs(nz - fz) > lim * step) continue;
				if (done.contains(key(nx, nz))) continue;
				if (rs.sampleBlockValueUncached(mask, nx, 64, nz) >= 0.999f) {
					done.add(key(nx, nz));
					queue.add(new int[]{nx, nz});
				} else {
					edge = true;
				}
			}
			if (edge) {
				double dd = (double) (c[0] - x) * (c[0] - x) + (double) (c[1] - z) * (c[1] - z);
				if (dd < eBest) {
					eBest = dd;
					ex = c[0];
					ez = c[1];
				}
			}
		}
		double area = cells * step * step / 1e6;
		if (area < 0.5) return null;
		int ccx = (int) (sx / cells), ccz = (int) (sz / cells);
		float yaw = (float) Math.toDegrees(Math.atan2(-(ccx - ex), ccz - ez));
		return "karst core ~%.2f km2 centred at %d %d; aerial view from its edge: /tp @s %d 210 %d %.0f 25"
				.formatted(area, ccx, ccz, ex, ez, yaw);
	}

	private static double round3(double v) {
		return Math.round(v * 1000) / 1000.0;
	}
}
