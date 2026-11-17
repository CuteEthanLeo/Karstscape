package dev.karstscape.worldgen;

import com.mojang.serialization.Codec;
import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;
import dev.karstscape.Karstscape;
import net.minecraft.util.Interval;
import net.minecraft.util.RandomSource;
import net.minecraft.world.level.levelgen.densityfunction.DensityBuffer;
import net.minecraft.world.level.levelgen.densityfunction.DensityFunction;
import net.minecraft.world.level.levelgen.densityfunction.DensitySampler;
import net.minecraft.world.level.levelgen.densityfunction.DensityVolume;
import net.minecraft.world.level.levelgen.densityfunction.DfRewriteRule;
import net.minecraft.world.level.levelgen.densityfunction.SamplerContext;

/**
 * {@code karstscape:tower_field} — a field of steep, round-topped karst towers.
 *
 * <p>Height field: the XZ plane is cut into {@code cell_size} cells; each cell may hold one tower whose
 * centre, radius, height and shape are hashed from the world seed and the cell coordinates (seed via
 * {@link CompileContext#createRandom}, the same mechanism vanilla uses for old_blended_noise). The value is
 * {@code clamp((top(x,z) - y) * steepness, -1, 1)}: positive (solid) below the tower top, -1 where there is
 * no tower. It is meant to be combined with terrain via {@code max} OUTSIDE the {@code interpolated} node so
 * walls are evaluated per block. The 2D part is computed once per column in {@link Sampler#sampleVolume}.
 *
 * <p>Profile at normalised distance u = d / R(θ): vertical-ish wall for u in [1 - taper, 1]
 * (height rises linearly from 0 to H - dome), round dome above. R(θ) is perturbed by three seeded
 * harmonics so towers are not perfect cylinders. Towers fade with {@code mask} sampled at the tower
 * centre: they get rarer and lower towards the region edge and vanish below {@code mask_min}.
 */
public record TowerField(
		DensityFunction mask,
		int cellSize,
		float probability,
		float maskMin,
		float maskFull,
		float baseY,
		float heightMin,
		float heightMax,
		float tallChance,
		float tallExtra,
		float radiusMin,
		float radiusMax,
		float taper,
		float dome,
		float wobble,
		float twinChance
) implements DensityFunction {
	/** Density per block below the tower top; only the sign change matters (clamped to ±1). */
	public static final float STEEPNESS = 0.5F;

	public static final MapCodec<TowerField> CODEC = RecordCodecBuilder.mapCodec(i -> i.group(
			DensityFunction.CODEC.fieldOf("mask").forGetter(TowerField::mask),
			Codec.intRange(16, 512).fieldOf("cell_size").forGetter(TowerField::cellSize),
			Codec.floatRange(0, 1).fieldOf("probability").forGetter(TowerField::probability),
			Codec.floatRange(0, 1).fieldOf("mask_min").forGetter(TowerField::maskMin),
			Codec.floatRange(0, 1).fieldOf("mask_full").forGetter(TowerField::maskFull),
			Codec.floatRange(-64, 512).fieldOf("base_y").forGetter(TowerField::baseY),
			Codec.floatRange(0, 512).fieldOf("height_min").forGetter(TowerField::heightMin),
			Codec.floatRange(0, 512).fieldOf("height_max").forGetter(TowerField::heightMax),
			Codec.floatRange(0, 1).fieldOf("tall_chance").forGetter(TowerField::tallChance),
			Codec.floatRange(0, 128).fieldOf("tall_extra").forGetter(TowerField::tallExtra),
			Codec.floatRange(1, 128).fieldOf("radius_min").forGetter(TowerField::radiusMin),
			Codec.floatRange(1, 128).fieldOf("radius_max").forGetter(TowerField::radiusMax),
			Codec.floatRange(0.01f, 0.9f).fieldOf("taper").forGetter(TowerField::taper),
			Codec.floatRange(0, 0.9f).fieldOf("dome").forGetter(TowerField::dome),
			Codec.floatRange(0, 0.5f).fieldOf("wobble").forGetter(TowerField::wobble),
			Codec.floatRange(0, 1).fieldOf("twin_chance").forGetter(TowerField::twinChance)
	).apply(i, TowerField::new));

	public static final net.minecraft.resources.Identifier SEED_ID = Karstscape.id("tower_field");

	@Override
	public DensitySampler compileSampler(CompileContext context) {
		RandomSource random = context.createRandom(SEED_ID);
		long seed = random.nextLong();
		return new Sampler(this, seed, this.mask.compileSampler(context));
	}

	@Override
	public DensityFunction rewriteChildren(DfRewriteRule rule) {
		DensityFunction m = rule.rewrite(this.mask);
		return m == this.mask ? this : new TowerField(m, cellSize, probability, maskMin, maskFull, baseY, heightMin,
				heightMax, tallChance, tallExtra, radiusMin, radiusMax, taper, dome, wobble, twinChance);
	}

	@Override
	public Interval range() {
		return Interval.of(-1.0F, 1.0F);
	}

	@Override
	public @Axes int domainAxes() {
		return ALL_AXES;
	}

	@Override
	public MapCodec<TowerField> codec() {
		return CODEC;
	}

	/** Largest horizontal reach of a tower (incl. twin offset and wobble) — how far to look for cells. */
	float maxReach() {
		return radiusMax * (1 + wobble) * 1.6f + 2;
	}

	// ------------------------------------------------------------------ towers ---

	/** One tower (or the absence of one): centre, radius, height and shape. */
	record Tower(float cx, float cz, float radius, float height, float a1, float p1, float a2, float p2, float a3,
				 float p3) {
		static final Tower NONE = new Tower(0, 0, 0, 0, 0, 0, 0, 0, 0, 0);

		boolean exists() {
			return radius > 0;
		}
	}

	private static long mix(long z) {
		z = (z ^ (z >>> 30)) * 0xBF58476D1CE4E5B9L;
		z = (z ^ (z >>> 27)) * 0x94D049BB133111EBL;
		return z ^ (z >>> 31);
	}

	/** Deterministic stream of floats in [0,1) for (seed, cell, salt). */
	private static final class Hash {
		private long state;

		Hash(long seed, int cellX, int cellZ, int salt) {
			this.state = mix(seed ^ mix(cellX * 0x9E3779B97F4A7C15L + cellZ * 0xC2B2AE3D27D4EB4FL + salt * 0x165667B19E3779F9L));
		}

		float next() {
			state += 0x9E3779B97F4A7C15L;
			return (mix(state) >>> 40) / (float) (1L << 24);
		}
	}

	private static float smoothstep(float e0, float e1, float x) {
		if (e1 <= e0) return x >= e1 ? 1 : 0;
		float t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0)));
		return t * t * (3 - 2 * t);
	}

	/** Builds up to two towers (main + optional twin) for a cell; returns how many were written. */
	int towersForCell(long seed, int cellX, int cellZ, DensitySampler maskSampler, SamplerContext ctx, Tower[] out, int at) {
		Hash h = new Hash(seed, cellX, cellZ, 1);
		float margin = Math.min(radiusMax * 0.35f, cellSize * 0.25f);
		float cx = cellX * cellSize + margin + h.next() * (cellSize - 2 * margin);
		float cz = cellZ * cellSize + margin + h.next() * (cellSize - 2 * margin);
		float m = maskSampler.sampleValue(ctx, (int) Math.floor(cx), (int) baseY, (int) Math.floor(cz));
		float s = smoothstep(maskMin, maskFull, m);
		float roll = h.next();
		if (s <= 0 || roll >= probability * s) {
			return 0;
		}
		// radius uniform, height mostly mid-range with a tall tail
		float ur = h.next();
		float radius = radiusMin + (radiusMax - radiusMin) * ur;
		float uh = h.next();
		float height = heightMin + (heightMax - heightMin) * (float) Math.pow(uh, 1.3);
		if (h.next() < tallChance) {
			height += tallExtra * h.next();
		}
		// slightly lower and thinner towards the region edge
		float edge = 0.55f + 0.45f * s;
		height *= edge;
		radius *= 0.8f + 0.2f * s;
		float w = wobble;
		out[at] = new Tower(cx, cz, radius, height,
				w * (0.4f + 0.6f * h.next()), h.next() * 6.2832f,
				w * 0.6f * h.next(), h.next() * 6.2832f,
				w * 0.4f * h.next(), h.next() * 6.2832f);
		int n = 1;
		if (h.next() < twinChance) {
			float ang = h.next() * 6.2832f;
			float dist = radius * (0.9f + 0.5f * h.next());
			float r2 = radius * (0.5f + 0.35f * h.next());
			float h2 = height * (0.5f + 0.4f * h.next());
			out[at + 1] = new Tower(cx + (float) Math.cos(ang) * dist, cz + (float) Math.sin(ang) * dist, r2, h2,
					w * (0.4f + 0.6f * h.next()), h.next() * 6.2832f,
					w * 0.6f * h.next(), h.next() * 6.2832f,
					w * 0.4f * h.next(), h.next() * 6.2832f);
			n = 2;
		}
		return n;
	}

	/** Top surface y of one tower at (x, z), or -infinity if outside. */
	float towerTop(Tower t, float x, float z) {
		float dx = x - t.cx(), dz = z - t.cz();
		float d2 = dx * dx + dz * dz;
		float reach = t.radius() * (1 + wobble) + 1;
		if (d2 >= reach * reach) {
			return Float.NEGATIVE_INFINITY;
		}
		float d = (float) Math.sqrt(d2);
		float theta = (float) Math.atan2(dz, dx);
		float r = t.radius() * (1 + t.a1() * (float) Math.sin(theta + t.p1())
				+ t.a2() * (float) Math.sin(2 * theta + t.p2())
				+ t.a3() * (float) Math.sin(3 * theta + t.p3()));
		float u = d / r;
		if (u >= 1) {
			return Float.NEGATIVE_INFINITY;
		}
		float domeH = Math.min(t.height() * 0.45f, dome * r * (1 - taper));
		float wallH = t.height() - domeH;
		float inner = 1 - taper;
		float h;
		if (u >= inner) {
			h = wallH * (1 - u) / taper;
		} else {
			float v = u / inner;
			h = wallH + domeH * (float) Math.sqrt(Math.max(0, 1 - v * v));
		}
		return baseY + h;
	}

	// ----------------------------------------------------------------- sampler ---

	record Sampler(TowerField f, long seed, DensitySampler maskSampler) implements DensitySampler {
		@Override
		public void sampleVolume(SamplerContext context, DensityBuffer out, DensityVolume volume) {
			float reach = f.maxReach();
			int minCellX = Math.floorDiv((int) Math.floor(volume.minBlockX() - reach), f.cellSize());
			int maxCellX = Math.floorDiv((int) Math.ceil(volume.maxBlockX() + reach), f.cellSize());
			int minCellZ = Math.floorDiv((int) Math.floor(volume.minBlockZ() - reach), f.cellSize());
			int maxCellZ = Math.floorDiv((int) Math.ceil(volume.maxBlockZ() + reach), f.cellSize());
			int cells = (maxCellX - minCellX + 1) * (maxCellZ - minCellZ + 1);
			Tower[] towers = new Tower[cells * 2];
			int n = 0;
			for (int cz = minCellZ; cz <= maxCellZ; cz++) {
				for (int cx = minCellX; cx <= maxCellX; cx++) {
					n += f.towersForCell(seed, cx, cz, maskSampler, context, towers, n);
				}
			}
			for (int iz = 0; iz < volume.sizeZ(); iz++) {
				int bz = volume.blockZ(iz);
				for (int ix = 0; ix < volume.sizeX(); ix++) {
					int bx = volume.blockX(ix);
					float top = Float.NEGATIVE_INFINITY;
					for (int k = 0; k < n; k++) {
						top = Math.max(top, f.towerTop(towers[k], bx + 0.5f, bz + 0.5f));
					}
					int base = volume.indexUnchecked(ix, 0, iz);
					if (top == Float.NEGATIVE_INFINITY) {
						out.setRange(base, volume.sizeY(), -1.0F);
						continue;
					}
					for (int iy = 0; iy < volume.sizeY(); iy++) {
						out.set(base + iy, density(top, volume.blockY(iy)));
					}
				}
			}
		}

		private float density(float top, int y) {
			return Math.max(-1.0F, Math.min(1.0F, (top - y) * STEEPNESS));
		}

		@Override
		public float sampleValue(SamplerContext context, int x, int y, int z) {
			float reach = f.maxReach();
			int minCellX = Math.floorDiv((int) Math.floor(x - reach), f.cellSize());
			int maxCellX = Math.floorDiv((int) Math.ceil(x + reach), f.cellSize());
			int minCellZ = Math.floorDiv((int) Math.floor(z - reach), f.cellSize());
			int maxCellZ = Math.floorDiv((int) Math.ceil(z + reach), f.cellSize());
			Tower[] buf = new Tower[2];
			float top = Float.NEGATIVE_INFINITY;
			for (int cz = minCellZ; cz <= maxCellZ; cz++) {
				for (int cx = minCellX; cx <= maxCellX; cx++) {
					int c = f.towersForCell(seed, cx, cz, maskSampler, context, buf, 0);
					for (int k = 0; k < c; k++) {
						top = Math.max(top, f.towerTop(buf[k], x + 0.5f, z + 0.5f));
					}
				}
			}
			return top == Float.NEGATIVE_INFINITY ? -1.0F : density(top, y);
		}
	}
}
