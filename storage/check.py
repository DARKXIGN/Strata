"""Check the Supabase connection from this host (host network, like the uploader).

    .venv/bin/python -m storage.check            # read test: list agents
    .venv/bin/python -m storage.check --write    # also upsert this agent's row (idempotent)

Prints the project host and a key fingerprint, never the key itself.
"""

from __future__ import annotations

import argparse
import sys

import config  # noqa: F401  (loads .env)
from storage import config as scfg
from storage.supabase_rest import StorageError, SupabaseRest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="upsert this agent's row into public.agents")
    args = ap.parse_args()

    cfg = scfg.load()
    pub = cfg.public()
    print(f"agent={pub['agent_id']} region={pub['region']} upload_enabled={pub['upload_enabled']}")
    if not cfg.upload_enabled:
        print(f"uploads are off: {cfg.reason}")
        return 1
    print(f"supabase host={pub['supabase_host']} key fingerprint={pub['key_fingerprint']}")
    client = SupabaseRest(cfg.url, cfg.key)
    try:
        agents = client.agents()
        print(f"read ok: {len(agents)} agent(s) in public.agents")
        if args.write:
            client.upsert_agent({"id": cfg.agent_id, "name": cfg.agent_name, "region": cfg.region})
            print(f"write ok: upserted agent {cfg.agent_id}")
    except StorageError as exc:
        print(f"FAILED: {exc}")
        print("hints: HTTP 401 = wrong key or URL; HTTP 404 / 'relation does not exist' = run supabase/schema.sql;"
              " 'permission denied' = re-run the grants in schema.sql")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
