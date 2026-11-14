"""FastAPI server: runs the measure -> classify -> explain loop and streams it over WebSocket.

Two modes, chosen at start-up (config.load_settings):
  * sim  - fake measurements and in-memory faults. Default; works anywhere.
  * real - SIM_MODE=0 on Linux, as root, with the strata namespace up
           (scripts/netns_up.sh): probes run inside the namespace and faults are
           injected on its veth. Falls back to sim if anything is missing.
Faults are reset on start-up, on shutdown, at exit, and on SIGHUP.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import signal
import time
from contextlib import asynccontextmanager

from dataclasses import replace

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from collector import sim as sim_collector
from config import load_settings
from explain import config as explain_config
from explain.service import ExplainService
from faults import INJECTABLE, LABEL_LAYER, LABELS, LAYERS
from faults.sim import SimFaultController
from ml.anomaly import AnomalyDetector
from ml.model import MODEL_DIR, Classifier

log = logging.getLogger("strata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = load_settings()
classifier = Classifier.load()
anomaly = AnomalyDetector.load(MODEL_DIR / "anomaly.json", MODEL_DIR / "anomaly.pkl")
explainer = ExplainService()
mode, mode_reason = ("sim", settings.sim_reason) if settings.sim_mode else ("real", "real")
collector = None

if mode == "real":
    try:
        from collector.linux import CollectorConfig, LinuxCollector, missing_tools
        from faults.netns import NetnsFaultController

        if missing := missing_tools():
            raise RuntimeError(f"missing tools: {', '.join(missing)}")
        faults = NetnsFaultController(netns=settings.netns)
        collector = LinuxCollector(CollectorConfig.from_env(settings.netns))
    except Exception as exc:
        log.error("real mode unavailable (%s); falling back to sim", exc)
        mode, mode_reason = "sim", f"fallback: {exc}"
        faults = SimFaultController()
else:
    faults = SimFaultController()

log.info("mode=%s (%s), classifier=%s, explain=%s", mode, mode_reason, classifier.kind, explainer.settings.mode)


def _safe_reset() -> None:
    with contextlib.suppress(Exception):
        faults.reset()


atexit.register(_safe_reset)


def _on_hangup(signum, _frame) -> None:
    # An ssh drop sends SIGHUP; reset the network before dying with the default action.
    _safe_reset()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


class Hub:
    """Tracks connected dashboards and the latest snapshot."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.latest: dict | None = None
        self.detected_at: float | None = None

    async def broadcast(self, message: dict) -> None:
        self.latest = message
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()
UNMEASURED_LAYERS = {"wifi"}  # shown on the strata view, never diagnosed


def layer_status(label: str) -> dict[str, str]:
    bad = LABEL_LAYER.get(label)
    return {
        layer: "unmeasured" if layer in UNMEASURED_LAYERS else ("problem" if layer == bad else "ok")
        for layer in LAYERS
    }


def measure() -> dict:
    """One feature row. Blocking (real probes take ~2 s); call it in a thread."""
    if collector is not None:
        return collector.measure()
    state = faults.state
    return sim_collector.measure(state.name, state.severity)


def build_tick(row: dict, measured_at: float) -> dict:
    state = faults.state
    label, probs = classifier.predict(row)

    if state.name and hub.detected_at is None and label == state.name:
        hub.detected_at = time.time()

    return {
        "type": "tick",
        "ts": measured_at,
        "mode": mode,
        "features": row,
        "diagnosis": {
            "label": label,
            "probs": probs,
            "layer": LABEL_LAYER.get(label),
            "layers": layer_status(label),
            "model": classifier.kind,
            "contributions": classifier.contributions(row, label),
        },
        "anomaly": anomaly.check(row) if anomaly else None,
        "explanation": explainer.explain(label, probs, row, LABEL_LAYER.get(label)),
        "fault": {
            "active": state.name,
            "params": state.params,
            "injected_at": state.injected_at,
            "detected_at": hub.detected_at,
        },
    }


async def run_loop() -> None:
    while True:
        started = time.time()
        try:
            row = await asyncio.to_thread(measure)
            await hub.broadcast(build_tick(row, time.time()))
        except Exception:
            log.exception("tick failed")
        # Real rounds already take ~2 s; sim rounds are paced to one per tick.
        await asyncio.sleep(max(0.1, settings.tick_seconds - (time.time() - started)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _safe_reset()  # clean slate in case an earlier run was killed
    with contextlib.suppress(ValueError):  # not in the main thread under some runners
        signal.signal(signal.SIGHUP, _on_hangup)
    if collector is not None:
        await asyncio.to_thread(collector.prepare)  # before any fault, so DNS faults can't break it
        log.info("collector ready: gateway=%s throughput_ip=%s", collector.gateway, collector.cfg.throughput_ip)
    task = asyncio.create_task(run_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        _safe_reset()


app = FastAPI(title="Strata", lifespan=lifespan)


@app.get("/api/status")
def status() -> dict:
    return {
        "sim_mode": mode == "sim",
        "sim_reason": mode_reason,
        "fault_mode": faults.mode,
        "explain_mode": explainer.settings.mode,
        "model": classifier.info(),
        "anomaly_detector": None if anomaly is None else ("forest+range" if anomaly.forest else "range only"),
        "labels": LABELS,
        "layers": LAYERS,
        "injectable": INJECTABLE,
        "active_fault": faults.state.name,
    }


@app.post("/api/faults/reset")
def reset_fault() -> dict:
    faults.reset()
    hub.detected_at = None
    return {"active": None}


@app.post("/api/faults/{name}")
def inject_fault(name: str) -> dict:
    if name not in INJECTABLE:
        raise HTTPException(status_code=404, detail=f"unknown fault: {name}")
    try:
        state = faults.inject(name)
    except Exception as exc:
        log.exception("inject failed")
        _safe_reset()
        raise HTTPException(status_code=500, detail=f"inject failed: {exc}") from exc
    hub.detected_at = None
    return {"active": state.name, "params": state.params, "injected_at": state.injected_at}


class ExplainSettingsIn(BaseModel):
    mode: str
    base_url: str = ""
    model: str = ""
    # None keeps the saved key, "" clears it, anything else replaces it.
    api_key: str | None = None


def _merge(body: ExplainSettingsIn) -> explain_config.ExplainSettings:
    error = explain_config.validate(body.mode, body.base_url.strip(), body.model.strip())
    if error:
        raise HTTPException(status_code=400, detail=error)
    current = explainer.settings
    key = current.api_key if body.api_key is None else body.api_key.strip()
    return replace(current, mode=body.mode, base_url=body.base_url.strip(), model=body.model.strip(), api_key=key)


@app.get("/api/settings/explain")
def get_explain_settings() -> dict:
    return {**explainer.settings.public(), "default_local_url": explain_config.DEFAULT_LOCAL_URL}


@app.put("/api/settings/explain")
def put_explain_settings(body: ExplainSettingsIn) -> dict:
    new = _merge(body)
    explain_config.save(new)
    explainer.update(new)
    log.info("explanation settings changed: %r", new)
    return new.public()


@app.post("/api/settings/explain/test")
def test_explain_settings(body: ExplainSettingsIn) -> dict:
    return explainer.test(_merge(body))


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await ws.accept()
    hub.clients.add(ws)
    if hub.latest:
        await ws.send_json(hub.latest)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
