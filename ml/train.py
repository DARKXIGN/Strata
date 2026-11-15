"""Train and evaluate the LightGBM fault classifier on collected data.

Evaluation splits by collection session (StratifiedGroupKFold), never by row: rows of
one session share a fault and a severity, so mixing them across train and test would
inflate accuracy. The final model is then fit on all rows.

    .venv/bin/python -m ml.train data/raw/main.csv

Also fits the anomaly detector (ml/anomaly.py) on normal rows only.

Writes models/classifier.txt, models/meta.json, models/anomaly.json, models/anomaly.pkl
and, for slides, models/eval/confusion_matrix.png and models/eval/feature_importance.png.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np

from faults import LABELS
from ml.anomaly import AnomalyDetector
from ml.features import MODEL_FEATURES, vectorize
from ml.model import META_FILE, MODEL_DIR, MODEL_FILE

PARAMS = {
    "objective": "multiclass",
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 10,
    "subsample": 0.9,
    "subsample_freq": 1,
    "colsample_bytree": 0.9,
    "random_state": 0,
    "verbose": -1,
}

LABEL_TEXT = {
    "normal": "Normal",
    "high_latency": "High latency",
    "packet_loss": "Packet loss",
    "bandwidth_limit": "Bandwidth limit",
    "dns_slow": "Slow DNS",
    "dns_failure": "DNS failure",
}


def load(paths: list[Path]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X, y, groups = [], [], []
    for path in paths:
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row["label"] not in LABELS:
                    continue
                X.append(vectorize(row))
                y.append(LABELS.index(row["label"]))
                groups.append(row["session_id"])
    return np.array(X, dtype=float), np.array(y), np.array(groups)


def make_model():
    from lightgbm import LGBMClassifier

    return LGBMClassifier(**PARAMS)


def cross_validate(X, y, groups, folds: int) -> tuple[np.ndarray, list[float]]:
    from sklearn.model_selection import StratifiedGroupKFold

    oof = np.zeros((len(y), len(LABELS)))
    fold_acc = []
    splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=0)
    for train_idx, test_idx in splitter.split(X, y, groups):
        assert not set(groups[train_idx]) & set(groups[test_idx]), "session leaked across split"
        model = make_model().fit(X[train_idx], y[train_idx])
        proba = np.zeros((len(test_idx), len(LABELS)))
        proba[:, model.classes_] = model.predict_proba(X[test_idx])
        oof[test_idx] = proba
        fold_acc.append(float((proba.argmax(1) == y[test_idx]).mean()))
    return oof, fold_acc


def evaluate_anomaly(X, y, groups, folds: int) -> dict:
    """Fit on normal sessions of each fold; false-positive rate on held-out normal
    sessions and the share of each fault's rows flagged as unusual."""
    from sklearn.model_selection import GroupKFold

    normal = LABELS.index("normal")
    Xn, gn = X[y == normal], groups[y == normal]
    fp, flagged = [], {label: [] for label in LABELS if label != "normal"}
    for train_idx, test_idx in GroupKFold(n_splits=folds).split(Xn, groups=gn):
        det = AnomalyDetector.fit(Xn[train_idx])
        fp.append(float(det.flags(Xn[test_idx]).mean()))
        for label in flagged:
            flagged[label].append(float(det.flags(X[y == LABELS.index(label)]).mean()))
    return {
        "normal_false_positive": round(float(np.mean(fp)), 4),
        "faults_flagged": {label: round(float(np.mean(v)), 4) for label, v in flagged.items()},
    }


def plot_confusion(cm: np.ndarray, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    names = [LABEL_TEXT[label] for label in LABELS]
    rate = cm / cm.sum(axis=1, keepdims=True).clip(min=1)
    cmap = LinearSegmentedColormap.from_list("blue", ["#F2F4F6", "#2563EB"])

    fig, ax = plt.subplots(figsize=(7.2, 6), dpi=200)
    ax.imshow(rate, cmap=cmap, vmin=0, vmax=1)
    for i in range(len(names)):
        for j in range(len(names)):
            if cm[i, j] == 0:
                continue
            color = "white" if rate[i, j] > 0.55 else "#191F28"
            ax.text(j, i, f"{rate[i, j] * 100:.0f}%\n({cm[i, j]})", ha="center", va="center",
                    fontsize=9, color=color)
    ax.set_xticks(range(len(names)), names, rotation=30, ha="right")
    ax.set_yticks(range(len(names)), names)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual (injected fault)")
    ax.set_title("Confusion matrix, session-grouped 5-fold CV", fontsize=12, loc="left")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_importance(gain: np.ndarray, path: Path, top: int = 12) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = np.argsort(gain)[::-1][:top][::-1]
    share = gain[order] / gain.sum() * 100
    fig, ax = plt.subplots(figsize=(7.2, 5), dpi=200)
    ax.barh(range(len(order)), share, color="#2563EB", height=0.6)
    ax.set_yticks(range(len(order)), [MODEL_FEATURES[i] for i in order])
    for i, v in enumerate(share):
        ax.text(v + 0.5, i, f"{v:.1f}%", va="center", fontsize=9, color="#4E5968")
    ax.set_xlabel("Share of total split gain (%)")
    ax.set_title("Feature importance (gain)", fontsize=12, loc="left")
    ax.grid(axis="x", color="#E5E8EB")
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main() -> None:
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

    ap = argparse.ArgumentParser(description="Train the Strata fault classifier.")
    ap.add_argument("data", nargs="+", type=Path, help="collected CSV file(s)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--out", type=Path, default=MODEL_DIR)
    args = ap.parse_args()

    X, y, groups = load(args.data)
    sessions = len(set(groups))
    print(f"{len(y)} rows, {sessions} sessions, {X.shape[1]} features")

    oof, fold_acc = cross_validate(X, y, groups, args.folds)
    pred = oof.argmax(1)
    acc = accuracy_score(y, pred)
    f1 = f1_score(y, pred, average="macro")
    cm = confusion_matrix(y, pred, labels=range(len(LABELS)))
    report = classification_report(y, pred, labels=range(len(LABELS)), target_names=LABELS, digits=3,
                                   zero_division=0)
    print(f"session-grouped {args.folds}-fold CV: accuracy {acc:.3f}, macro F1 {f1:.3f}, "
          f"per fold {[round(a, 3) for a in fold_acc]}")
    print(report)
    print("confusion matrix (rows = actual, cols = predicted):")
    print("  " + " ".join(f"{label[:8]:>8}" for label in LABELS))
    for label, row in zip(LABELS, cm):
        print(f"  {' '.join(f'{v:8d}' for v in row)}  {label}")

    anomaly_eval = evaluate_anomaly(X, y, groups, args.folds)
    print(f"anomaly detector (normal-only, session-grouped): normal flagged "
          f"{anomaly_eval['normal_false_positive']:.1%}; faults flagged "
          + ", ".join(f"{k} {v:.0%}" for k, v in anomaly_eval["faults_flagged"].items()))
    detector = AnomalyDetector.fit(X[y == LABELS.index("normal")])

    final = make_model().fit(X, y)
    gain = final.booster_.feature_importance(importance_type="gain")

    out: Path = args.out
    (out / "eval").mkdir(parents=True, exist_ok=True)
    model_file = out / MODEL_FILE.name
    final.booster_.save_model(str(model_file))
    meta = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data": [str(p) for p in args.data],
        "rows": int(len(y)),
        "sessions": sessions,
        "features": MODEL_FEATURES,
        "labels": [LABELS[i] for i in final.classes_],
        "params": PARAMS,
        "cv": {
            "folds": args.folds,
            "accuracy": round(float(acc), 4),
            "macro_f1": round(float(f1), 4),
            "fold_accuracy": [round(a, 4) for a in fold_acc],
            "confusion_matrix": cm.tolist(),
        },
        "anomaly": anomaly_eval,
        "importance_gain": {MODEL_FEATURES[i]: round(float(g), 2) for i, g in enumerate(gain)},
    }
    (out / META_FILE.name).write_text(json.dumps(meta, indent=2))
    detector.save(out / "anomaly.json", out / "anomaly.pkl")
    plot_confusion(cm, out / "eval" / "confusion_matrix.png")
    plot_importance(gain, out / "eval" / "feature_importance.png")
    (out / "eval" / "report.txt").write_text(
        f"session-grouped {args.folds}-fold CV on {len(y)} rows / {sessions} sessions\n"
        f"accuracy {acc:.3f}, macro F1 {f1:.3f}\n\n{report}\n"
        f"anomaly detector trained on normal rows only (session-grouped {args.folds}-fold):\n"
        f"  normal rows flagged: {anomaly_eval['normal_false_positive']:.1%}\n"
        + "".join(f"  {k} rows flagged: {v:.1%}\n" for k, v in anomaly_eval["faults_flagged"].items())
    )
    print(f"saved {model_file}, {out / META_FILE.name}, {out / 'eval'}/")


if __name__ == "__main__":
    main()
