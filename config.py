"""Runtime configuration loaded from .env, with environment checks."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _has_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def can_inject_real_faults() -> bool:
    """Real fault injection needs Linux, root, tc, and the strata namespace from scripts/netns_up.sh."""
    netns = os.getenv("STRATA_NETNS", "strata")
    return (
        platform.system() == "Linux"
        and _has_root()
        and shutil.which("tc") is not None
        and Path(f"/run/netns/{netns}").exists()
    )


# Values from older .env.example copies that no longer match the netns scripts.
STALE_ENV = {
    "STRATA_VETH_HOST": ({"st-host"}, "strata-host"),
    "STRATA_VETH_NS": ({"st-ns"}, "strata-ns"),
    "STRATA_DNS_SERVERS": ({"1.1.1.1 8.8.8.8", "1.1.1.1,8.8.8.8"}, "1.1.1.1"),
}
REMOVED_ENV = {"STRATA_DNS_PRIMARY": "STRATA_DNS_SERVERS=1.1.1.1", "FAULT_INTERFACE": None}


def stale_env_problems() -> list[str]:
    """Settings left over from an old .env that would break real mode, as fix hints."""
    problems = []
    for key, (old, new) in STALE_ENV.items():
        if os.getenv(key, "").strip() in old:
            problems.append(f"{key}={os.environ[key]} is outdated; set {key}={new} in .env")
    for key, hint in REMOVED_ENV.items():
        if os.getenv(key) is not None:
            problems.append(f"{key} is no longer used; remove it from .env" + (f" (use {hint})" if hint else ""))
    return problems


@dataclass(frozen=True)
class Settings:
    sim_mode: bool
    sim_reason: str
    explain_mode: str
    llm_base_url: str
    llm_model: str
    llm_timeout: float
    netns: str
    tick_seconds: float = 1.0


def load_settings() -> Settings:
    requested_sim = os.getenv("SIM_MODE", "1").strip() != "0"
    if requested_sim:
        sim, reason = True, "SIM_MODE=1"
    elif problems := stale_env_problems():
        sim, reason = True, "stale .env: " + "; ".join(problems)
    elif not can_inject_real_faults():
        sim, reason = True, "forced: needs Linux, root, tc and the strata namespace"
    else:
        sim, reason = False, "real"
    return Settings(
        sim_mode=sim,
        sim_reason=reason,
        explain_mode=os.getenv("EXPLAIN_MODE", "template").strip() or "template",
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_timeout=float(os.getenv("LLM_TIMEOUT", "3") or 3),
        netns=os.getenv("STRATA_NETNS", "strata").strip() or "strata",
    )
