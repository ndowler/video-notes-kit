import json, os, sys, tempfile, unittest, stat
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vnlib


def segs(n, step=5, text="word word"):
    return [{"start_s": i * step, "duration_s": step, "text": text + (" end." if i % 4 == 3 else "")} for i in range(n)]


class IdTests(unittest.TestCase):
    def test_urls(self):
        v = "O_zcc6Xl12g"
        for u in ["https://www.youtube.com/watch?v=%s" % v, "https://youtu.be/%s?si=abc" % v,
                  "https://www.youtube.com/shorts/%s" % v, "https://www.youtube.com/watch?v=%s&pp=ygUD&t=30s" % v,
                  "https://www.youtube.com/watch?feature=share&v=%s" % v, "youtube.com/embed/%s" % v,
                  "https://m.youtube.com/live/%s" % v, v]:
            self.assertEqual(vnlib.parse_video_id(u), v, u)

    def test_bad(self):
        for u in ["https://example.com/watch?v=O_zcc6Xl12g", "nonsense", "https://www.youtube.com/"]:
            with self.assertRaises(vnlib.VnError):
                vnlib.parse_video_id(u)

    def test_slug(self):
        self.assertEqual(vnlib.slugify("Why Tests Matter (Except Mine) — Jane Doe"), "why-tests-matter-except-mine-jane-doe")
        self.assertLessEqual(len(vnlib.slugify("a " * 100)), 60)
        self.assertEqual(vnlib.slugify("!!!"), "video")


class ParaTests(unittest.TestCase):
    def test_breaks_at_sentence_after_60s(self):
        p = vnlib.paragraphize(segs(60))  # 300s
        self.assertGreater(len(p), 2)
        for (s1, _), (s2, _) in zip(p, p[1:]):
            self.assertTrue(60 <= s2 - s1 <= 90, (s1, s2))
        for _, text in p[:-1]:
            self.assertTrue(text.endswith("."))

    def test_hard_break_without_punctuation(self):
        p = vnlib.paragraphize([{"start_s": i * 5, "text": "no punct"} for i in range(60)])
        self.assertTrue(all(b[0] - a[0] <= 90 for a, b in zip(p, p[1:])))

    def test_unescape_and_ts(self):
        p = vnlib.paragraphize([{"start_s": 3700, "text": "it&#39;s &gt;&gt; ok\nnow"}])
        self.assertEqual(p, [(3700, "it's >> ok now")])
        self.assertEqual(vnlib.fmt_ts(3700), "1:01:40")
        self.assertEqual(vnlib.fmt_ts(66), "1:06")

    def test_render_parse_roundtrip(self):
        md = vnlib.render_transcript("T", "O_zcc6Xl12g", "about 1:00", [(66, "hello"), (150, "bye")])
        title, paras = vnlib.parse_transcript(md)
        self.assertEqual(title, "T")
        self.assertEqual(paras[0], (66, "https://www.youtube.com/watch?v=O_zcc6Xl12g&t=66s", "hello"))
        self.assertEqual(len(paras), 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        vnlib.REPO = self.tmp.name
        # fake treg
        self.treg = os.path.join(self.tmp.name, "treg")
        self.setup_treg({"result": {"rows": [{"title": "Lint &amp; Rules Talk", "video_id": "AAAAAAAAAAA",
                         "is_auto_generated": True, "segments": segs(40, text="we love lint rules")}]}})
        os.environ["VN_TREG"] = self.treg

    def setup_treg(self, payload, code=0):
        with open(self.treg, "w") as f:
            f.write("#!/bin/sh\necho '%s'\nexit %d\n" % (json.dumps(payload), code))
        os.chmod(self.treg, 0o755)

    def test_add_search_attach_list(self):
        d = vnlib.add_video("https://youtu.be/AAAAAAAAAAA?si=x", tags=["a"], today="2026-01-02")
        self.assertTrue(d.endswith("2026-01-02-lint-rules-talk"))
        meta = json.load(open(os.path.join(d, "meta.json")))
        self.assertEqual((meta["video_id"], meta["caption_type"], meta["tags"]), ("AAAAAAAAAAA", "auto", ["a"]))
        self.assertEqual(meta["title"], "Lint & Rules Talk")
        self.assertFalse(os.path.exists(os.path.join(d, "takeaways.md")))
        res = vnlib.search("lint rule", "transcripts")
        self.assertTrue(res)
        self.assertIn("&t=", res[0]["url"])
        self.assertIn("**", res[0]["snippet"])
        self.assertEqual(vnlib.search("lint", "takeaways"), [])
        # attach takeaways, searched by sections
        kf = os.path.join(self.tmp.name, "k.md")
        open(kf, "w").write("# K\n\nintro\n\n## One\n\nAvoid zebra patterns.\n\n## Two\n\n```\n# not heading\n```\nother\n")
        vnlib.attach_takeaways("AAAAAAAAAAA", kf)
        r = vnlib.search("zebra", "all")
        self.assertEqual((len(r), r[0]["kind"], r[0]["heading"]), (1, "takeaways", "One"))
        self.assertEqual(len(vnlib.parse_takeaways(open(kf).read())), 3)
        self.assertIn("takeaways.md", open(os.path.join(self.tmp.name, "INDEX.md")).read())
        # slug attach + reindex from scratch
        vnlib.attach_takeaways("lint-rules", kf)
        n = vnlib.reindex()
        self.assertGreater(n, 3)
        os.remove(vnlib.paths()["db"])
        self.assertTrue(vnlib.search("zebra"))  # auto-rebuild

    def test_duplicate_and_missing_captions(self):
        vnlib.add_video("AAAAAAAAAAA")
        with self.assertRaises(vnlib.VnError):
            vnlib.add_video("AAAAAAAAAAA")
        self.setup_treg({"result": {"rows": [{"title": "x", "segments": []}]}})
        with self.assertRaises(vnlib.VnError):
            vnlib.add_video("BBBBBBBBBBB")
        self.setup_treg({"error": "nope"}, code=2)
        self.assertEqual(vnlib.main(["add", "BBBBBBBBBBB"]), 1)

    def test_import_and_missing_takeaways(self):
        md = vnlib.render_transcript("Imported Talk", "CCCCCCCCCCC", "about 2:00", [(0, "skills matter"), (70, "other")])
        p = os.path.join(self.tmp.name, "in.md")
        open(p, "w").write(md)
        d = vnlib.import_transcript(p, channel="Someone")
        self.assertEqual(json.load(open(os.path.join(d, "meta.json")))["channel"], "Someone")
        self.assertEqual(vnlib.search("skills")[0]["timestamp"], "0:00")
        self.assertEqual(vnlib.search("skill")[0]["kind"], "transcript")  # stemming


if __name__ == "__main__":
    unittest.main()
