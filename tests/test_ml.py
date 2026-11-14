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


class WindowsCheckoutTests(unittest.TestCase):
    def test_model_with_crlf_line_endings_loads(self):
        # git on Windows may convert the model text file to CRLF; LightGBM's own file
        # parser then aborts the process, so the loader must normalize line endings.
        from ml.model import META_FILE, MODEL_FILE

        if not MODEL_FILE.exists():
            self.skipTest("models/classifier.txt not present")
        with tempfile.TemporaryDirectory() as d:
            crlf = Path(d) / "classifier.txt"
            crlf.write_bytes(MODEL_FILE.read_bytes().replace(b"\n", b"\r\n"))
            clf = Classifier.load(crlf, META_FILE)
        self.assertEqual(clf.kind, "lightgbm")
        self.assertEqual(clf.predict(sim.measure("dns_slow", 1.0))[0], "dns_slow")


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


class AnomalyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np

        from ml.anomaly import AnomalyDetector

        rows = [sim.measure(None) for _ in range(300)]
        cls.det = AnomalyDetector.fit(np.array([vectorize(r) for r in rows]))

    def test_normal_rows_mostly_pass(self):
        flagged = sum(self.det.check(sim.measure(None))["unusual"] for _ in range(200))
        self.assertLess(flagged, 15)

    def test_known_faults_are_flagged_with_the_right_feature(self):
        out = self.det.check(sim.measure("bandwidth_limit"))
        self.assertTrue(out["unusual"])
        self.assertEqual(out["feature"], "throughput_mbps")
        out = self.det.check(sim.measure("dns_failure"))
        self.assertTrue(out["unusual"])

    def test_unseen_fault_is_flagged(self):
        # Loss at the gateway never happens in training data (faults are injected past it).
        row = sim.measure(None)
        row.update(gw_loss_pct=30.0, gw_rtt_ms=12.0)
        out = self.det.check(row)
        self.assertTrue(out["unusual"])
        self.assertIn(out["feature"], {"gw_loss_pct", "gw_rtt_ms"})

    def test_save_load_roundtrip_and_range_only_fallback(self):
        from ml.anomaly import AnomalyDetector

        with tempfile.TemporaryDirectory() as d:
            j, f = Path(d) / "a.json", Path(d) / "a.pkl"
            self.det.save(j, f)
            loaded = AnomalyDetector.load(j, f)
            row = sim.measure("dns_slow")
            self.assertEqual(loaded.check(row), self.det.check(row))
            f.write_bytes(b"not a pickle")  # e.g. incompatible scikit-learn version
            range_only = AnomalyDetector.load(j, f)
            self.assertIsNone(range_only.forest)
            self.assertTrue(range_only.check(sim.measure("bandwidth_limit"))["unusual"])


class ContributionTests(unittest.TestCase):
    def test_contributions_point_at_the_cause(self):
        clf = Classifier.load()
        if clf.kind != "lightgbm":
            self.skipTest("models/classifier.txt not present")
        row = sim.measure("dns_failure")
        label, _ = clf.predict(row)
        contribs = clf.contributions(row, label)
        self.assertTrue(contribs)
        self.assertEqual(contribs[0]["feature"], "dns_primary_only_fail")
        self.assertGreater(contribs[0]["contribution"], 0)
        self.assertEqual(Classifier().contributions(row, label), [])  # dummy has none
