"""Inference: load the trained LightGBM model, fall back to the rule-based dummy if absent."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from collector.features import FeatureRow
from faults import LABELS
from ml import dummy
from ml.features import MODEL_FEATURES, vectorize

log = logging.getLogger("strata.model")

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models"
MODEL_FILE = MODEL_DIR / "classifier.txt"
META_FILE = MODEL_DIR / "meta.json"


class Classifier:
    """Wraps either the trained model or the dummy rules behind one predict() API."""

    def __init__(self, booster=None, meta: dict | None = None) -> None:
        self.booster = booster
        self.meta = meta or {}
        self.kind = "lightgbm" if booster is not None else "dummy"

    @classmethod
    def load(cls, model_file: Path = MODEL_FILE, meta_file: Path = META_FILE) -> "Classifier":
        if not model_file.exists() or not meta_file.exists():
            log.warning("no trained model at %s; using dummy rules", model_file)
            return cls()
        try:
            import lightgbm as lgb

            meta = json.loads(meta_file.read_text())
            if meta.get("features") != MODEL_FEATURES:
                raise ValueError("model was trained with a different feature list; retrain it")
            return cls(lgb.Booster(model_file=str(model_file)), meta)
        except Exception as exc:  # keep the demo running on any load problem
            log.error("failed to load model (%s); using dummy rules", exc)
            return cls()

    def predict_proba(self, row: FeatureRow) -> dict[str, float]:
        if self.booster is None:
            return dummy.predict_proba(row)
        labels = self.meta["labels"]
        probs = self.booster.predict([vectorize(row)])[0]
        out = {label: 0.0 for label in LABELS}
        out.update({label: round(float(p), 4) for label, p in zip(labels, probs)})
        return out

    def predict(self, row: FeatureRow) -> tuple[str, dict[str, float]]:
        probs = self.predict_proba(row)
        return max(probs, key=probs.get), probs

    def info(self) -> dict:
        if self.booster is None:
            return {"kind": "dummy"}
        cv = self.meta.get("cv", {})
        return {
            "kind": "lightgbm",
            "trained_at": self.meta.get("trained_at"),
            "rows": self.meta.get("rows"),
            "sessions": self.meta.get("sessions"),
            "cv_accuracy": cv.get("accuracy"),
            "cv_macro_f1": cv.get("macro_f1"),
        }
