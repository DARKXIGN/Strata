"""Feature vector, model wrapper and server tick, without the collected dataset."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from collector import sim
from collector.features import empty_row
from faults import LABELS
from ml.features import MODEL_FEATURES, vectorize
from ml.model import Classifier


class VectorizeTests(unittest.TestCase):
    def test_length_and_comparisons(self):
        row = sim.measure(None)
        row.update(gw_rtt_ms=0.05, ext_rtt_ms=33.55, dns_primary_ms=700.0, dns_alt_ms=710.0)
        vec = dict(zip(MODEL_FEATURES, vectorize(row)))
        self.assertEqual(len(vectorize(row)), len(MODEL_FEATURES))
        self.assertAlmostEqual(vec["ext_minus_gw_ms"], 33.5)
        self.assertAlmostEqual(vec["dns_max_ms"], 710.0)
        self.assertAlmostEqual(vec["dns_over_rtt_ms"], 700.0 - 33.55)
        self.assertEqual(vec["dns_fail_count"], 0.0)

    def test_primary_only_failure(self):
        row = sim.measure("dns_failure")
        vec = dict(zip(MODEL_FEATURES, vectorize(row)))
        self.assertEqual(vec["dns_primary_only_fail"], 1.0)
        self.assertEqual(vec["dns_fail_count"], 1.0)
        self.assertTrue(math.isnan(vec["dns_primary_ms"]))

    def test_csv_strings_and_missing(self):
        vec = vectorize({"ext_rtt_ms": "33.5", "gw_rtt_ms": "", "dns_primary_ok": "None"})
        self.assertTrue(all(isinstance(v, float) for v in vec))
        self.assertTrue(math.isnan(vec[MODEL_FEATURES.index("ext_minus_gw_ms")]))
        self.assertTrue(all(math.isnan(v) for v in vectorize(empty_row())))


class ClassifierTests(unittest.TestCase):
    def test_missing_model_falls_back_to_dummy(self):
        with tempfile.TemporaryDirectory() as d:
            clf = Classifier.load(Path(d) / "none.txt", Path(d) / "none.json")
        self.assertEqual(clf.kind, "dummy")
        label, probs = clf.predict(sim.measure("dns_failure"))
        self.assertEqual(set(probs), set(LABELS))
        self.assertEqual(label, "dns_failure")

    def test_trained_model_if_present(self):
        clf = Classifier.load()
        if clf.kind != "lightgbm":
            self.skipTest("models/classifier.txt not present")
        label, probs = clf.predict(sim.measure("dns_slow", 1.0))
        self.assertEqual(set(probs), set(LABELS))
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=2)
        self.assertEqual(label, "dns_slow")


class ServerTickTests(unittest.TestCase):
    def test_sim_tick_and_detection(self):
        from server import main

        self.assertEqual(main.mode, "sim")
        main.faults.inject("dns_failure")
        main.hub.detected_at = None
        tick = main.build_tick(main.measure(), 0.0)
        self.assertEqual(tick["diagnosis"]["label"], "dns_failure")
        self.assertEqual(tick["diagnosis"]["layers"]["dns"], "problem")
        self.assertEqual(tick["diagnosis"]["layers"]["wifi"], "unmeasured")
        self.assertIsNotNone(tick["fault"]["detected_at"])
        main.faults.reset()


if __name__ == "__main__":
    unittest.main()
