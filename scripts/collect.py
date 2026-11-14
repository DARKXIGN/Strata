"""Collect labeled training data inside the strata namespace. Run with sudo on the OCI host.

WHAT THIS CHANGES: it injects and resets faults through faults/netns.py (strata-host qdisc
and /etc/netns/strata/resolv.conf only). A reset runs between sessions, at the end,
on Ctrl+C, on SIGTERM/SIGHUP (e.g. ssh drop), and on any error. If the process is
killed with SIGKILL, run `sudo scripts/fault.sh reset` by hand.

Data is collected in sessions: one fault with one random severity for N consecutive
rows. Each row stores its session_id so training can split by session (CLAUDE.md).
Re-running with the same --out resumes: it only collects what each label still needs.

    sudo .venv/bin/python -m scripts.collect run --rows-per-class 5 --session-rows 5 --out data/raw/trial.csv
    sudo .venv/bin/python -m scripts.collect run --rows-per-class 200 --out data/raw/main.csv
    .venv/bin/python -m scripts.collect summary data/raw/main.csv
"""

from __future__ import annotations

import argparse
import atexit
import csv
import json
import os
import random
import signal
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402,F401  (loads .env)
from collector.features import FEATURES  # noqa: E402
from collector.linux import OPTIONAL_TOOLS, CollectorConfig, LinuxCollector, missing_tools  # noqa: E402
from faults import LABELS  # noqa: E402
from faults.netns import NetnsFaultController, sample_params  # noqa: E402

META = ["run_id", "session_id", "label", "round", "ts", "duration_s", "params"]
COLUMNS = META + FEATURES


def existing_counts(path: Path) -> tuple[Counter, int]:
    counts: Counter = Counter()
    sessions: set[str] = set()
    if path.exists():
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                counts[row["label"]] += 1
                sessions.add(row["session_id"])
    return counts, len(sessions)


def plan(remaining: dict[str, int], session_rows: int, rng: random.Random) -> list[tuple[str, int]]:
    tasks = []
    for label, n in remaining.items():
        while n > 0:
            take = min(session_rows, n)
            tasks.append((label, take))
            n -= take
    rng.shuffle(tasks)
    return tasks


class _Stop(Exception):
    pass


def _raise_stop(signum, _frame):
    raise _Stop(signal.Signals(signum).name)


def run(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        sys.exit("run with sudo (faults and `ip netns exec` need root)")
    if missing := missing_tools():
        sys.exit(f"missing tools: {', '.join(missing)}")
    if missing_tools(OPTIONAL_TOOLS):
        print("WARNING: traceroute is not installed, so trace_* columns will be empty for these rows.\n"
              "         Install it first (sudo apt install -y traceroute) unless you really mean this.")
        time.sleep(5)

    labels = args.labels or LABELS
    unknown = set(labels) - set(LABELS)
    if unknown:
        sys.exit(f"unknown labels: {', '.join(sorted(unknown))}")

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    counts, n_sessions = existing_counts(out)
    remaining = {lb: max(0, args.rows_per_class - counts[lb]) for lb in labels}
    rng = random.Random(args.seed)
    tasks = plan(remaining, args.session_rows, rng)
    total = sum(remaining.values())
    if total == 0:
        print(f"{out} already has {args.rows_per_class} rows for every label.")
        return

    faults = NetnsFaultController()
    atexit.register(faults.reset)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _raise_stop)
    faults.reset()  # clean slate in case an earlier run was killed

    collector = LinuxCollector(CollectorConfig.from_env())
    collector.prepare()  # before any fault, so DNS faults can't break it
    print(f"gateway={collector.gateway} throughput_ip={collector.cfg.throughput_ip}")
    if not collector.gateway:
        sys.exit("no default route inside the namespace; run scripts/netns_up.sh")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    new_file = not out.exists() or out.stat().st_size == 0
    print(f"collecting {total} rows in {len(tasks)} sessions -> {out}")
    started, done = time.time(), 0

    try:
        with out.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            if new_file:
                writer.writeheader()
            for i, (label, rows) in enumerate(tasks):
                session_id = f"{run_id}-{n_sessions + i:04d}"
                if label == "normal":
                    faults.reset()
                    params = {}
                else:
                    params = faults.inject(label, sample_params(label, rng)).params
                time.sleep(args.settle)
                print(f"[{i + 1}/{len(tasks)}] {session_id} {label} {params}")
                for r in range(rows):
                    t0 = time.time()
                    feats = collector.measure()
                    writer.writerow({
                        "run_id": run_id, "session_id": session_id, "label": label, "round": r,
                        "ts": round(t0, 3), "duration_s": round(time.time() - t0, 2),
                        "params": json.dumps(params), **feats,
                    })
                    f.flush()
                    done += 1
                    eta = (time.time() - started) / done * (total - done)
                    print(f"    row {r + 1}/{rows}  ext_rtt={feats['ext_rtt_ms']} loss={feats['ext_loss_pct']} "
                          f"dns={feats['dns_primary_ms']}/{feats['dns_alt_ms']} tput={feats['throughput_mbps']}  "
                          f"eta {eta / 60:.0f} min", flush=True)
                    if args.interval:
                        time.sleep(args.interval)
                faults.reset()
                time.sleep(args.settle)
    except (_Stop, KeyboardInterrupt) as exc:
        print(f"\nstopped ({exc or 'Ctrl+C'}); rows so far are saved. Re-run the same command to resume.")
    finally:
        faults.reset()
        print("faults reset")


# --- summary ------------------------------------------------------------------

KEY_FEATURES = ["gw_rtt_ms", "ext_rtt_ms", "ext_loss_pct", "dns_primary_ok", "dns_primary_ms",
                "dns_alt_ms", "trace_jump_hop", "throughput_mbps"]


def summary(args: argparse.Namespace) -> None:
    by_label: dict[str, list[dict]] = defaultdict(list)
    sessions: dict[str, set] = defaultdict(set)
    durations = []
    with args.file.open(newline="") as f:
        for row in csv.DictReader(f):
            by_label[row["label"]].append(row)
            sessions[row["label"]].add(row["session_id"])
            durations.append(float(row["duration_s"]))

    def med(rows: list[dict], key: str) -> str:
        vals = [float(r[key]) for r in rows if r[key] not in ("", "None")]
        missing = len(rows) - len(vals)
        text = "-" if not vals else f"{statistics.median(vals):.1f}"
        return text + (f" ({missing} empty)" if missing else "")

    print(f"{args.file}: {sum(map(len, by_label.values()))} rows, "
          f"median round {statistics.median(durations):.1f}s" if durations else "empty")
    print("median per label:")
    header = ["label", "rows", "sess"] + KEY_FEATURES
    print("  " + " | ".join(header))
    for label in LABELS:
        rows = by_label.get(label, [])
        if not rows:
            continue
        cells = [label, str(len(rows)), str(len(sessions[label]))] + [med(rows, k) for k in KEY_FEATURES]
        print("  " + " | ".join(cells))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--rows-per-class", type=int, default=200)
    r.add_argument("--session-rows", type=int, default=20, help="rows per session (one fault + one severity)")
    r.add_argument("--labels", nargs="*", help=f"subset of {LABELS}")
    r.add_argument("--out", type=Path, default=ROOT / "data/raw/main.csv")
    r.add_argument("--settle", type=float, default=3.0, help="seconds to wait after inject/reset")
    r.add_argument("--interval", type=float, default=0.0, help="extra seconds between rows")
    r.add_argument("--seed", type=int, default=None)
    s = sub.add_parser("summary")
    s.add_argument("file", type=Path)
    args = ap.parse_args()
    run(args) if args.cmd == "run" else summary(args)


if __name__ == "__main__":
    main()
