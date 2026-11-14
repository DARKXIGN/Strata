"""FastAPI server: runs the measure -> classify -> explain loop and streams it over WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from collector import sim as sim_collector
from config import load_settings
from explain import explain
from faults import INJECTABLE, LABEL_LAYER, LABELS, LAYERS
from faults.sim import SimFaultController
from ml import dummy

log = logging.getLogger("strata")
settings = load_settings()
faults = SimFaultController()


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


def layer_status(label: str) -> dict[str, str]:
    bad = LABEL_LAYER.get(label)
    return {layer: ("problem" if layer == bad else "ok") for layer in LAYERS}


def tick() -> dict:
    state = faults.state
    row = sim_collector.measure(state.name, state.severity)
    label, probs = dummy.predict(row)

    if state.name and hub.detected_at is None and label == state.name:
        hub.detected_at = time.time()

    return {
        "type": "tick",
        "ts": time.time(),
        "features": row,
        "diagnosis": {
            "label": label,
            "probs": probs,
            "layer": LABEL_LAYER.get(label),
            "layers": layer_status(label),
        },
        "explanation": explain(label, probs, row),
        "fault": {
            "active": state.name,
            "injected_at": state.injected_at,
            "detected_at": hub.detected_at,
        },
    }


async def run_loop() -> None:
    while True:
        try:
            await hub.broadcast(tick())
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(settings.tick_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        faults.reset()


app = FastAPI(title="Strata", lifespan=lifespan)


@app.get("/api/status")
def status() -> dict:
    return {
        "sim_mode": settings.sim_mode,
        "sim_reason": settings.sim_reason,
        "fault_mode": faults.mode,
        "explain_mode": settings.explain_mode,
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
    state = faults.inject(name)
    hub.detected_at = None
    return {"active": state.name, "injected_at": state.injected_at}


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
