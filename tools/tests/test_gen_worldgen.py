"""Unit tests for tools/gen_worldgen.py (stdlib unittest; needs reference/vanilla-26.3 for the build test).

  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import json
import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gen_worldgen as gw  # noqa: E402

VANILLA_WORLD = {"min_y": -64, "height": 384, "cloud_height": 192.33, "depth_zero_y": 128, "depth_blocks": 128,
                 "top_slide": [240, 256]}


def with_world(world: dict):
    gw.WORLD.clear()
    gw.WORLD.update(world)


class HeightTests(unittest.TestCase):
    def test_y_to_offset_matches_vanilla_depth(self):
        with_world(VANILLA_WORLD)
        # vanilla depth gradient: -64 -> 1.5, 320 -> -1.5; the surface (depth 0) is where offset = -gradient
        self.assertAlmostEqual(gw.y_to_offset(-64), -1.5)
        self.assertAlmostEqual(gw.y_to_offset(320), 1.5)
        self.assertAlmostEqual(gw.y_to_offset(128), 0.0)

    def test_vanilla_world_keeps_vanilla_gradient(self):
        with_world(VANILLA_WORLD)
        g = gw.depth_gradient()
        self.assertEqual((g["from_coordinate"], g["from_value"], g["to_coordinate"], g["to_value"]),
                         (-64, 1.5, 320, -1.5))

    def test_taller_world_extends_gradient_linearly(self):
        with_world(dict(VANILLA_WORLD, height=512))
        g = gw.depth_gradient()
        self.assertEqual((g["from_coordinate"], g["to_coordinate"]), (-64, 448))
        slope = (g["to_value"] - g["from_value"]) / (g["to_coordinate"] - g["from_coordinate"])
        self.assertAlmostEqual(slope, -1.0 / 128)  # same terrain shape inside the vanilla range
        self.assertAlmostEqual(g["from_value"], 1.5)

    def test_retarget_counts_and_values(self):
        with_world(dict(VANILLA_WORLD, height=512, top_slide=[400, 420]))
        src = {"type": "minecraft:add", "left": {"type": "minecraft:gradient", "axis": "y", "from_coordinate": -64,
                                                 "from_value": 1.5, "to_coordinate": 320, "to_value": -1.5},
               "right": {"type": "minecraft:lerp",
                         "alpha": {"type": "minecraft:gradient", "axis": "y", "from_coordinate": 240,
                                   "from_value": 1.0, "to_coordinate": 256, "to_value": 0.0},
                         "first": 0.0,
                         "second": {"type": "minecraft:clamp", "input": 0.0, "min": -40.0, "max": 320.0}}}
        counts: dict = {}
        out = gw.retarget_heights(src, counts)
        self.assertEqual(counts, {"depth": 1, "top_slide": 1, "surface_search": 1})
        self.assertEqual(out["left"]["to_coordinate"], 448)
        self.assertEqual((out["right"]["alpha"]["from_coordinate"], out["right"]["alpha"]["to_coordinate"]), (400, 420))
        self.assertEqual(out["right"]["second"]["max"], 448.0)
        self.assertEqual(src["left"]["to_coordinate"], 320)  # input not mutated


class SplineTests(unittest.TestCase):
    def test_piecewise_interpolates_and_extrapolates(self):
        f, df = gw.piecewise([[0, 0], [10, 20], [20, 25]])
        self.assertAlmostEqual(f(5), 10)
        self.assertAlmostEqual(f(15), 22.5)
        self.assertAlmostEqual(f(-5), -10)  # first slope
        self.assertAlmostEqual(f(30), 30)   # last slope
        self.assertAlmostEqual(df(12), 0.5)

    def test_piecewise_rejects_unsorted(self):
        with self.assertRaises(SystemExit):
            gw.piecewise([[0, 0], [0, 1]])

    def test_map_spline_nested(self):
        sp = {"coordinate": "c", "points": [
            {"location": 0.0, "derivative": 1.0, "value": 1.0},
            {"location": 1.0, "derivative": 2.0, "value": {"coordinate": "d", "points": [
                {"location": 0.0, "derivative": 0.5, "value": 2.0}]}}]}
        gw.map_spline(sp, lambda v: v * 3, lambda v: 3.0)
        self.assertEqual(sp["points"][0]["value"], 3.0)
        self.assertEqual(sp["points"][0]["derivative"], 3.0)
        self.assertEqual(sp["points"][1]["derivative"], 6.0)
        self.assertEqual(sp["points"][1]["value"]["points"][0]["value"], 6.0)
        self.assertEqual(sp["points"][1]["value"]["points"][0]["derivative"], 1.5)


@unittest.skipUnless((gw.VANILLA / "MANIFEST.json").is_file(), "reference/vanilla-26.3 missing")
class TerrainParamTests(unittest.TestCase):
    """The configured [terrain] curve keeps the terrain inside the world."""

    @classmethod
    def setUpClass(cls):
        cls.params = tomllib.loads(gw.PARAMS.read_text(encoding="utf-8"))
        with_world(cls.params["world"])

    def test_offset_curve_monotone_and_sea_fixed(self):
        f, _ = gw.offset_curve(self.params["terrain"])
        xs = [x / 100 for x in range(-40, 160)]
        ys = [f(x) for x in xs]
        self.assertTrue(all(b > a for a, b in zip(ys, ys[1:])))
        self.assertAlmostEqual(f(0.0), 0.0)  # the coast stays at sea level

    def test_highest_offset_below_top_slide(self):
        offset = gw.terrain_functions(self.params["terrain"])["offset"]
        top = max(gw.spline_leaves(gw.the_spline(offset)))
        y = float(gw.WORLD["depth_zero_y"]) + (top + gw.OFFSET_SEA) * float(gw.WORLD["depth_blocks"])
        self.assertLess(y, self.params["world"]["top_slide"][0])


@unittest.skipUnless((gw.VANILLA / "MANIFEST.json").is_file(), "reference/vanilla-26.3 missing")
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.params = tomllib.loads(gw.PARAMS.read_text(encoding="utf-8"))
        cls.files = gw.build(cls.params)

    def get(self, rel: str):
        return json.loads(self.files[gw.RES / rel])

    def test_default_world_preset_is_replaced(self):
        wp = self.get("data/minecraft/worldgen/world_preset/normal.json")
        ow = wp["dimensions"]["minecraft:overworld"]
        self.assertEqual(ow["type"], "karstscape:overworld")
        self.assertEqual(ow["generator"]["settings"], "karstscape:overworld")
        self.assertIn("biomes", ow["generator"]["biome_source"])
        # nether and end stay vanilla
        vanilla = json.loads((gw.VWG / "world_preset" / "normal.json").read_text(encoding="utf-8"))
        for dim in ("minecraft:the_nether", "minecraft:the_end"):
            self.assertEqual(wp["dimensions"][dim], vanilla["dimensions"][dim])

    def test_classic_is_vanilla(self):
        vanilla = json.loads((gw.VWG / "world_preset" / "normal.json").read_text(encoding="utf-8"))
        self.assertEqual(self.get("data/karstscape/worldgen/world_preset/classic.json"), vanilla)

    def test_dimension_type_and_noise_height_agree(self):
        dt = self.get("data/karstscape/dimension_type/overworld.json")
        ns = self.get("data/karstscape/worldgen/noise_settings/overworld.json")
        self.assertEqual((dt["min_y"], dt["height"]), (ns["noise"]["min_y"], ns["noise"]["height"]))
        self.assertEqual(dt["logical_height"], dt["height"])
        self.assertEqual(dt["height"] % 16, 0)

    def test_button_tag_lists_extra_presets_only(self):
        tag = self.get("data/minecraft/tags/worldgen/world_preset/normal.json")
        self.assertFalse(tag["replace"])
        self.assertIn("karstscape:classic", tag["values"])
        self.assertNotIn("minecraft:normal", tag["values"])  # already in the vanilla tag

    def test_every_preset_has_a_name(self):
        lang = self.get("assets/karstscape/lang/zh_cn.json")
        for v in self.get("data/minecraft/tags/worldgen/world_preset/normal.json")["values"]:
            ns, path = v.split(":", 1)
            self.assertIn(f"generator.{ns}.{path}", lang)


if __name__ == "__main__":
    unittest.main()
