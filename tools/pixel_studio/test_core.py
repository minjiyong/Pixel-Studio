"""Small regression set: four images, shared palettes, alpha and normals."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PIL import Image
from .core import (Settings, new_project, add_pngs, generate_group, transform,
                   convert_asset, export_batch, save_project, load_project, save_png, palette_from_arrays)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = new_project()
        self.project["settings"].update(width=17, height=13, colors=8)
        for frame in range(4):
            y, x = np.indices((31, 47))
            rgba = np.zeros((31, 47, 4), dtype=np.uint8)
            rgba[..., 0] = (x*5+frame*3) % 256
            rgba[..., 1] = y*8
            rgba[..., 2] = 170
            rgba[3:29, 4:43, 3] = 255
            rgba[4:10, 5, :3] = [255, 0, 0]
            normal = np.full_like(rgba, [220, 128, 220, 255])
            normal[..., 3] = rgba[..., 3]
            save_png(self.root/f"frame{frame}.png", rgba)
            save_png(self.root/f"frame{frame}_normal.png", normal)
        add_pngs(self.project, [str(self.root/f"frame{i}.png") for i in range(4)], "actor")

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_palette_png_roundtrip_normals_and_no_source_mutation(self):
        sources = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.glob("*.png")}
        report = export_batch(self.project, self.root/"export")
        self.assertEqual(report["written"], 4)
        self.assertEqual(report["failed"], [])
        palette = self.project["groups"]["actor"]["palette"]
        self.assertLessEqual(len(palette), 8)
        for asset in self.project["assets"]:
            result = convert_asset(self.project, asset)
            self.assertEqual(result["color"].shape, (13, 17, 4))
            self.assertTrue(np.array_equal(result["color"][..., 3], result["normal"][..., 3]))
            valid = result["normal"][..., 3] > 0
            vectors = result["normal"][..., :3].astype(float)/255*2-1
            self.assertLess(np.max(np.abs(np.linalg.norm(vectors[valid], axis=1)-1)), .012)
            path = self.root/"roundtrip.png"
            save_png(path, result["color"], result["indices"], palette)
            with Image.open(path) as image:
                self.assertEqual(image.mode, "P")
                np.testing.assert_array_equal(np.array(image.convert("RGBA")), result["color"])
        for path, digest in sources.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_palette_locked_and_new_frames_do_not_change_final_palette(self):
        group = self.project["groups"]["actor"]
        group.update(palette=[[255, 0, 0], [0, 0, 255]], locked=[0])
        self.assertEqual(generate_group(self.project, "actor"), [[255, 0, 0], [0, 0, 255]])
        palette = generate_group(self.project, "actor", True)
        self.assertEqual(palette[0], [255, 0, 0])
        snapshot = list(palette)
        generate_group(self.project, "actor")
        self.assertEqual(group["palette"], snapshot)

    def test_incremental_failures_cancel_and_project_reload(self):
        report = export_batch(self.project, self.root/"export")
        self.assertEqual(report["written"], 4)
        report = export_batch(self.project, self.root/"export")
        self.assertEqual(report["skipped"], 4)
        self.project["assets"][0]["normal"] = str(self.root/"missing.png")
        report = export_batch(self.project, self.root/"export")
        self.assertEqual(len(report["failed"]), 1)
        self.assertEqual(report["skipped"], 3)
        report = export_batch(self.project, self.root/"export", cancelled=lambda: True)
        self.assertTrue(report["cancelled"])
        save_project(self.root/"test.pixel.json", self.project)
        self.assertEqual(load_project(self.root/"test.pixel.json"), self.project)

    def test_non_square_contain_center_sampling_and_alpha(self):
        src = np.zeros((2, 4, 4), dtype=np.uint8)
        src[:] = [255, 0, 0, 100]
        src[:, 2:] = [0, 255, 0, 255]
        result = transform(src, None, Settings(width=4, height=4, colors=2), [[255,0,0],[0,255,0]])
        self.assertEqual(result["layout"]["content"], [0,1,4,2])
        self.assertEqual(int(result["color"][...,3].sum()), 4*255)
        settings = Settings(mode="step", pixel_step=2, alpha_mode="preserve")
        result = transform(src, None, settings, [[255,0,0],[0,255,0]])
        self.assertEqual(result["color"].shape, (1,2,4))
        self.assertEqual(result["color"][0,0,3], 100)
        with self.assertRaises(ValueError):
            transform(src, np.zeros((1,4,4), dtype=np.uint8), settings, [[0,0,0]])

    def test_palette_samples_both_axes_and_godot_numeric_settings(self):
        rgba = np.full((128, 128, 4), [255, 0, 0, 255], dtype=np.uint8)
        rgba[:, 64:] = [0, 255, 0, 255]
        self.assertEqual({tuple(c) for c in palette_from_arrays([rgba], 2)},
                         {(255, 0, 0), (0, 255, 0)})
        self.assertEqual(Settings.from_dict({"width": 100.0}).width, 100)
        normal = np.full((2, 2, 4), [220, 128, 220, 255], dtype=np.uint8)
        color = np.full_like(normal, 255)
        result = transform(color, normal, Settings(width=2, height=2, normal_flip_x=True), [[255,255,255]])
        self.assertTrue(np.all(result["normal"][..., 0] < 128))


if __name__ == "__main__":
    unittest.main()
