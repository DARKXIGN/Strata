"""Anomaly detection trained on normal rows only: "this does not look like usual".

The classifier must always pick one of the known labels; this flags rows that fit
none of them well, such as a fault that was never in the training data.

Two parts, either one flags a row:
  * IsolationForest on robust-scaled features catches several features drifting together.
  * A range check catches one feature far outside its usual band. IsolationForest alone
    misses that: on this host most features are constant in normal traffic (loss is
    always 0, DNS always answers), and a constant feature gives the forest nothing to split.
Scaling is (x - median) / max(MAD, floor), with a per-feature floor so near-constant
features still get a sensible tolerance.
"""

from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path

import numpy as np

from ml.features import MODEL_FEATURES, vectorize

log = logging.getLogger("strata.anomaly")

RANGE_LIMIT = 8.0  # robust z beyond which one feature alone marks the row unusual
FOREST_PERCENTILE = 1.0  # forest threshold: 1st percentile of normal training scores
NOISE = 0.5  # jitter added to scaled training rows (in units of the tolerance)


def tolerance_floor(name: str) -> float:
    if name.endswith("_ok") or name in ("dns_fail_count", "dns_primary_only_fail"):
        return 0.05
    if "pct" in name or "loss" in name:
        return 1.0
    if name in ("trace_hops", "trace_jump_hop"):
        return 0.5
    if name == "throughput_mbps":
        return 5.0
    return 1.0  # milliseconds


FLOORS = np.array([tolerance_floor(n) for n in MODEL_FEATURES])


class AnomalyDetector:
    def __init__(self, median: np.ndarray, scale: np.ndarray, forest=None, forest_threshold: float | None = None):
        self.median = median
        self.scale = scale
        self.forest = forest
        self.forest_threshold = forest_threshold

    # -- fitting ------------------------------------------------------------------

    @classmethod
    def fit(cls, X: np.ndarray, seed: int = 0) -> "AnomalyDetector":
        from sklearn.ensemble import IsolationForest

        median = np.nanmedian(X, axis=0)
        median = np.where(np.isnan(median), 0.0, median)
        mad = np.nanmedian(np.abs(X - median), axis=0) * 1.4826
        scale = np.maximum(np.nan_to_num(mad), FLOORS)
        det = cls(median, scale)
        Z = det._z(X)
        rng = np.random.default_rng(seed)
        augmented = np.vstack([Z + rng.normal(0, NOISE, Z.shape) for _ in range(5)])
        det.forest = IsolationForest(n_estimators=300, random_state=seed).fit(augmented)
        det.forest_threshold = float(np.percentile(det.forest.score_samples(Z), FOREST_PERCENTILE))
        return det

    # -- scoring ------------------------------------------------------------------

    def _z(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        return (np.where(np.isnan(X), self.median, X) - self.median) / self.scale

    def flags(self, X: np.ndarray) -> np.ndarray:
        Z = self._z(X)
        out = np.abs(Z).max(axis=1) > RANGE_LIMIT
        if self.forest is not None:
            out |= self.forest.score_samples(Z) < self.forest_threshold
        return out

    def check(self, row) -> dict:
        """Score one feature row (dict). Names the feature furthest from usual."""
        x = np.array([vectorize(row)])
        z = self._z(x)[0]
        i = int(np.argmax(np.abs(z)))
        forest_hit = bool(self.forest is not None and self.forest.score_samples(z[None, :])[0] < self.forest_threshold)
        range_hit = bool(abs(z[i]) > RANGE_LIMIT)
        value = x[0, i]
        return {
            "unusual": range_hit or forest_hit,
            "by": "range" if range_hit else ("forest" if forest_hit else None),
            "feature": MODEL_FEATURES[i],
            "value": None if np.isnan(value) else round(float(value), 3),
            "usual": round(float(self.median[i]), 3),
            "z": round(float(z[i]), 1),
        }

    # -- persistence --------------------------------------------------------------

    def save(self, json_path: Path, forest_path: Path) -> None:
        import sklearn

        json_path.write_text(json.dumps({
            "features": MODEL_FEATURES,
            "median": self.median.round(6).tolist(),
            "scale": self.scale.round(6).tolist(),
            "range_limit": RANGE_LIMIT,
            "forest_threshold": self.forest_threshold,
            "sklearn_version": sklearn.__version__,
        }, indent=2))
        with forest_path.open("wb") as f:
            pickle.dump(self.forest, f)

    @classmethod
    def load(cls, json_path: Path, forest_path: Path) -> "AnomalyDetector | None":
        """Load; if only the forest fails (e.g. another scikit-learn version), keep the range check."""
        if not json_path.exists():
            return None
        try:
            meta = json.loads(json_path.read_text())
            if meta.get("features") != MODEL_FEATURES:
                raise ValueError("trained with a different feature list; retrain")
            det = cls(np.array(meta["median"]), np.array(meta["scale"]))
        except Exception as exc:
            log.error("anomaly detector unavailable (%s)", exc)
            return None
        try:
            with forest_path.open("rb") as f:
                det.forest = pickle.load(f)  # our own file from ml/train.py, never user input
            det.forest_threshold = meta["forest_threshold"]
        except Exception as exc:
            log.warning("isolation forest not loaded (%s); using the range check only. "
                        "Retrain with: python -m ml.train data/raw/main.csv", exc)
        return det
