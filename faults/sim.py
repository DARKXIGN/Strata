"""Simulated fault controller. Holds state in memory; never touches the real network."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from faults import INJECTABLE


@dataclass
class FaultState:
    name: str | None = None
    severity: float = 1.0
    injected_at: float | None = None
    params: dict[str, float] = field(default_factory=dict)


class SimFaultController:
    mode = "sim"

    def __init__(self) -> None:
        self.state = FaultState()

    def inject(self, name: str, severity: float | None = None) -> FaultState:
        if name not in INJECTABLE:
            raise ValueError(f"unknown fault: {name}")
        self.reset()
        self.state = FaultState(
            name=name,
            severity=severity if severity is not None else random.uniform(0.7, 1.3),
            injected_at=time.time(),
        )
        return self.state

    def reset(self) -> FaultState:
        self.state = FaultState()
        return self.state
