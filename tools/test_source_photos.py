"""Offline checks for source_photos.py. Run: python3 -m unittest tools/test_source_photos.py"""

import io
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_photos as sp  # noqa: E402

NAMES = ["Psilostrophe cooperi", "Paper Flower"]


class MetadataScore(unittest.TestCase):
    def test_close_up_words_score_negative(self):
        s, hits = sp.metadata_score("File:Larrea tridentata flower close-up.jpg", "", [], ["Larrea tridentata"])
        self.assertLess(s, -5)
        self.assertIn("flower", hits)
        self.assertIn("close", hits)

    def test_habit_words_score_positive(self):
        s, hits = sp.metadata_score("File:Larrea tridentata habit, Anza-Borrego Desert.jpg", "", [],
                                    ["Larrea tridentata"])
        self.assertGreater(s, 5)
        self.assertIn("habit", hits)

    def test_species_name_words_are_not_keywords(self):
        s, hits = sp.metadata_score("File:Paper Flower (Psilostrophe cooperi) 2.jpg", "", [], NAMES)
        self.assertEqual(s, 0)
        self.assertEqual(hits, [])

    def test_category_hints(self):
        s, hits = sp.metadata_score("File:Ferocactus 1.jpg", "", ["Flowers of Ferocactus cylindraceus"],
                                    ["Ferocactus cylindraceus"])
        self.assertIn("cat:flowers", hits)
        self.assertLess(s, 0)

    def test_licences(self):
        for ok in ("CC0", "CC BY 2.0", "CC BY-SA 4.0", "Public domain", "PD-USGov"):
            self.assertTrue(sp.OK_LICENCES.search(ok), ok)
        for bad in ("CC BY-NC 2.0", "CC BY-ND 4.0", "GFDL", "Fair use", ""):
            self.assertFalse(sp.OK_LICENCES.search(bad), bad)


class Classify(unittest.TestCase):
    def test_uploader_label_wins(self):
        self.assertEqual(sp.classify(["flower"], 0.97), "close-up")

    def test_soft_background_is_close_up(self):
        self.assertEqual(sp.classify([], 0.3), "close-up")

    def test_sharp_everywhere_is_habit(self):
        self.assertEqual(sp.classify(["desert"], 0.9), "habit")

    def test_middle_is_unclear(self):
        self.assertEqual(sp.classify([], 0.5), "unclear")


class ContentFeatures(unittest.TestCase):
    def _img(self, draw):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (400, 300), (120, 110, 90))
        draw(ImageDraw.Draw(im))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    def test_detail_everywhere_reads_as_habit(self):
        import random
        rnd = random.Random(1)

        def draw(d):
            for _ in range(3000):
                x, y = rnd.randrange(400), rnd.randrange(300)
                d.line([x, y, x + rnd.randrange(-6, 6), y + rnd.randrange(-6, 6)], fill=(30, 60, 20))
        spread, sky, sat = sp.content_features(self._img(draw))
        self.assertGreater(spread, 0.8)

    def test_single_sharp_subject_reads_as_close_up(self):
        def draw(d):
            for r in range(10, 60, 3):
                d.ellipse([200 - r, 150 - r, 200 + r, 150 + r], outline=(230, 200, 40))
        spread, sky, sat = sp.content_features(self._img(draw))
        self.assertLess(spread, 0.45)

    def test_sky_is_detected(self):
        def draw(d):
            d.rectangle([0, 0, 400, 120], fill=(120, 170, 240))
        spread, sky, sat = sp.content_features(self._img(draw))
        self.assertGreater(sky, 0.8)


class ManifestRoundTrip(unittest.TestCase):
    def test_patch_index_rewrites_only_the_photos_line(self):
        tmp = tempfile.mkdtemp()
        idx = os.path.join(tmp, "index.html")
        body = 'before\nconst PHOTOS={"x":[{"t":"/images/x_0.webp","p":"https://commons.wikimedia.org/wiki/File:X.jpg","c":"A \\u00b7 CC0"}]};\nafter\n'
        open(idx, "w").write(body)
        real_index, real_manifest = sp.INDEX, sp.MANIFEST
        sp.INDEX, sp.MANIFEST = idx, os.path.join(tmp, "missing.json")
        try:
            m = sp.load_manifest()
            self.assertEqual(m["x"][0]["title"], "File:X.jpg")
            m["x"].insert(0, {"file": "/images/x_1.webp", "page": "https://commons.wikimedia.org/wiki/File:Y.jpg",
                              "credit": "B · CC BY 2.0"})
            sp.patch_index(m)
            out = open(idx).read()
            self.assertTrue(out.startswith("before\n") and out.endswith("\nafter\n"))
            line = re.search(r"^const PHOTOS=(.*);$", out, re.M).group(1)
            photos = json.loads(line)
            self.assertEqual([e["t"] for e in photos["x"]], ["/images/x_1.webp", "/images/x_0.webp"])
            self.assertNotIn("·", line)      # ensure_ascii, like the original constant
        finally:
            sp.INDEX, sp.MANIFEST = real_index, real_manifest

    def test_canon_title(self):
        self.assertEqual(sp.canon_title("Larrea_tridentata_1.jpg"), "File:Larrea tridentata 1.jpg")
        self.assertEqual(sp.canon_title("File:Larrea tridentata 1.jpg"), "File:Larrea tridentata 1.jpg")


if __name__ == "__main__":
    unittest.main()
