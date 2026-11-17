package dev.karstscape.debug;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import dev.karstscape.Karstscape;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.densityfunction.DensityFunction;
import net.minecraft.world.level.levelgen.densityfunction.DensitySampler;
import net.minecraft.world.level.levelgen.densityfunction.SamplerContext;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.concurrent.ForkJoinPool;
import java.util.stream.IntStream;

/**
 * Tuning aid: samples one registered density function (e.g. karstscape:terrain/mountains) on a square grid at
 * a fixed y and writes a gray image (min black .. max white) plus value statistics. No chunk generation.
 */
public final class LayerExport {
	private static final int MAX_GRID = 4097;

	private LayerExport() {
	}

	public static String run(ServerLevel level, String id, int cx, int cz, int radius, int step, int y) throws IOException {
		Identifier key = Identifier.tryParse(id);
		if (key == null) {
			throw new IllegalArgumentException("bad density function id: " + id);
		}
		DensityFunction fn = level.getServer().registryAccess().lookupOrThrow(Registries.DENSITY_FUNCTION).get(key)
				.map(Holder::value).orElseThrow(() -> new IllegalArgumentException("unknown density function: " + id));
		int n = 2 * (radius / step) + 1;
		if (n > MAX_GRID) {
			throw new IllegalArgumentException("grid %d > %d; use a larger step".formatted(n, MAX_GRID));
		}
		int x0 = cx - (n / 2) * step, z0 = cz - (n / 2) * step;
		RandomState rs = level.getChunkSource().randomState();
		DensitySampler sampler = rs.getSampler(fn);
		float[] v = new float[n * n];
		ForkJoinPool pool = new ForkJoinPool(Math.max(1, Runtime.getRuntime().availableProcessors() - 2));
		try {
			pool.submit(() -> IntStream.range(0, n).parallel().forEach(iz -> {
				for (int ix = 0; ix < n; ix++) {
					v[iz * n + ix] = sampler.sampleValue(SamplerContext.EMPTY_UNCACHED, x0 + ix * step, y, z0 + iz * step);
				}
			})).get();
		} catch (Exception e) {
			throw new IOException(e);
		} finally {
			pool.shutdown();
		}

		float[] sorted = v.clone();
		Arrays.sort(sorted);
		float min = sorted[0], max = sorted[sorted.length - 1];
		double sum = 0;
		int pos = 0, half = 0, full = 0;
		for (float f : v) {
			sum += f;
			if (f > 0) pos++;
			if (f >= 0.5f) half++;
			if (f >= 0.999f) full++;
		}
		BufferedImage img = new BufferedImage(n, n, BufferedImage.TYPE_INT_RGB);
		float span = max > min ? max - min : 1f;
		for (int i = 0; i < v.length; i++) {
			int g = Math.round((v[i] - min) / span * 255f);
			img.setRGB(i % n, i / n, (g << 16) | (g << 8) | g);
		}
		Path dir = level.getServer().getServerDirectory().resolve("karstscape_export");
		Files.createDirectories(dir);
		String name = "layer_%s_seed%d_x%d_z%d_r%d_s%d".formatted(key.getPath().replace('/', '.'), level.getSeed(),
				cx, cz, radius, step);
		ImageIO.write(img, "png", dir.resolve(name + ".png").toFile());

		JsonObject o = new JsonObject();
		o.addProperty("function", id);
		o.addProperty("y", y);
		o.addProperty("grid", n);
		o.addProperty("step", step);
		o.addProperty("min", min);
		o.addProperty("p5", sorted[(int) (0.05 * (sorted.length - 1))]);
		o.addProperty("p50", sorted[(int) (0.50 * (sorted.length - 1))]);
		o.addProperty("p95", sorted[(int) (0.95 * (sorted.length - 1))]);
		o.addProperty("max", max);
		o.addProperty("mean", sum / v.length);
		o.addProperty("fraction_gt_0", pos / (double) v.length);
		o.addProperty("fraction_ge_0_5", half / (double) v.length);
		o.addProperty("fraction_ge_0_999", full / (double) v.length);
		Gson gson = new GsonBuilder().setPrettyPrinting().create();
		try (Writer w = Files.newBufferedWriter(dir.resolve(name + ".json"), StandardCharsets.UTF_8)) {
			gson.toJson(o, w);
		}
		String summary = "%s: min %.3f p50 %.3f max %.3f mean %.3f, >0 %.1f%%, >=0.5 %.1f%% -> %s.png".formatted(id, min,
				sorted[sorted.length / 2], max, sum / v.length, 100.0 * pos / v.length, 100.0 * half / v.length, name);
		Karstscape.LOGGER.info("Karstscape layer done: {}", summary);
		return summary;
	}
}
