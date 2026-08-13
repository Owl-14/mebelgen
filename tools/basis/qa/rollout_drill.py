"""Offline rollback drill for MEB-158 (no provider, Basis or production calls)."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from src.rollout import (
    COMPONENTS,
    RolloutConfig,
    RolloutController,
    RolloutMetric,
    SloBudgets,
    compare_shadow_results,
)
from src.studio_graph import GraphAdapters, MemoryRevisionStore, StudioGraphOrchestrator


ROOT = Path(__file__).resolve().parents[1]


def run_drill() -> dict:
    with tempfile.TemporaryDirectory(prefix="akeda-rollout-drill-") as raw_root:
        root = Path(raw_root)
        spec = json.loads(
            (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(
                encoding="utf-8"
            )
        )
        revisions = MemoryRevisionStore()
        shadow = StudioGraphOrchestrator(GraphAdapters.defaults(revisions))
        candidate = shadow.run(
            project_key="offline-drill",
            spec=spec,
            message="сделай ширину 410",
            generation="shadow-proof",
            shadow=True,
        )
        comparison = compare_shadow_results(candidate, candidate)
        if revisions.rows or not comparison.get("equal"):
            raise RuntimeError("shadow mutation/comparison invariant failed")

        config = RolloutConfig(
            modes={
                component: ("on" if component == "tracing_exporters" else "canary")
                for component in COMPONENTS
            },
            killed=frozenset(),
            canary_tenants=frozenset(),
            canary_users=frozenset(),
            canary_percent=100,
        )
        budgets = SloBudgets(
            latency_p50_ms=10,
            latency_p95_ms=10,
            tokens_p95=100,
            cost_usd_p95=1,
            invalid_op_rate=0.1,
            false_rejection_rate=0.1,
            edit_success_rate=0.8,
            checkpoint_bytes=64 * 1024 * 1024,
            checkpoint_retention_days=7,
            minimum_samples=3,
            window_samples=10,
        )
        controller = RolloutController(root / "rollout", config=config, budgets=budgets)
        before = controller.plan("drill-tenant", "drill-user")
        for _ in range(3):
            dashboard = controller.record(
                RolloutMetric(latency_ms=50, edit_success=True, shadow_equal=True),
                tenant_id="drill-tenant",
                user_id="drill-user",
                plan=before,
            )
        after = controller.plan("drill-tenant", "drill-user")
        if before.primary != "graph" or after.primary != "legacy":
            raise RuntimeError("automatic rollback invariant failed")

        occupied = root / "occupied"
        occupied.write_text("not a directory", encoding="utf-8")
        degraded = StudioGraphOrchestrator.durable(occupied)
        if not degraded.storage_degraded:
            raise RuntimeError("checkpoint degradation invariant failed")

        return {
            "ok": True,
            "environment": "offline",
            "provider_calls": 0,
            "production_writes": 0,
            "shadow": {
                "revision_rows_written": 0,
                "comparison": comparison,
            },
            "canary": {
                "before": before.public_dict(),
                "stop_reasons": dashboard.get("reasons") or [],
                "after": after.public_dict(),
            },
            "checkpoint_storage": {
                "degraded": degraded.storage_degraded,
                "fallback": "legacy",
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_drill()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
