#!/usr/bin/env python3
"""Smallest complete SDK example: HOLD every step, then print metrics.

Flow:
    existing AgentVersion -> create Run -> submit HOLD for every Step -> metrics

Credentials are read from the environment (never hard-code keys)::

    export ATL_API_KEY="ag_xxx"
    export ATL_BASE_URL="http://127.0.0.1:8000"        # optional, this is the default
    export ATL_AGENT_VERSION_ID="agv_xxx"              # reuse one version across runs
    # or, to create a version once from an agent id:
    export ATL_AGENT_ID="agt_xxx"

Run:
    python3 dashboard/examples/sdk_hold_agent.py
"""

from __future__ import annotations

import os
import sys

from newworldtrading import ATLClient, Decision


def resolve_agent_version_id(client: ATLClient) -> str:
    version_id = os.environ.get("ATL_AGENT_VERSION_ID")
    if version_id:
        return version_id
    agent_id = os.environ.get("ATL_AGENT_ID")
    if not agent_id:
        raise SystemExit(
            "Set ATL_AGENT_VERSION_ID (preferred) or ATL_AGENT_ID to create one."
        )
    version = client.create_agent_version(
        agent_id,
        version="0.1.0",
        architecture="buy_and_hold_noop",
        model_backbones=["rule-based"],
    )
    print(f"Created AgentVersion: {version.id}")
    return version.id


def main() -> int:
    api_key = os.environ.get("ATL_API_KEY")
    if not api_key:
        raise SystemExit("ATL_API_KEY is required")
    base_url = os.environ.get("ATL_BASE_URL", "http://127.0.0.1:8000")
    start = os.environ.get("ATL_START_DATE", "2026-04-15")
    end = os.environ.get("ATL_END_DATE", "2026-04-16")

    client = ATLClient(base_url=base_url, api_key=api_key)
    agent_version_id = resolve_agent_version_id(client)

    run = client.create_run(
        agent_version_id,
        environment_id="us-equity-hourly-v1",
        start_date=start,
        end_date=end,
        symbols=["AAPL", "MSFT"],
    )
    print(f"Run: {run.id}\n")

    steps = 0
    while True:
        step = client.get_next_step(run.id)
        if step.status == "completed":
            break
        if step.status == "loading":
            print("  loading market data...")
            client.wait(2)
            continue
        if step.status != "awaiting_decision":
            client.wait(0.5)
            continue

        client.submit_decision(
            run.id,
            step.id,
            Decision(orders=[], rationale="No valid signal."),
        )
        steps += 1

    print(f"Submitted HOLD for {steps} steps.\n")
    result = client.get_run_result(run.id)
    print("=== Metrics ===")
    for key, value in result.metrics.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
