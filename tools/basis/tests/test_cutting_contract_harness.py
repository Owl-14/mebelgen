from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qa"))

import cutting_contract_harness as harness  # noqa: E402
from src.cloud_cutting import CuttingClient  # noqa: E402
from src.cutting_ledger import MutationLedger  # noqa: E402
from src.cutting_preflight import load_cutting_fixture, run_cutting_flow  # noqa: E402


def test_offline_contract_harness_runs_strict_full_order_without_network(capsys) -> None:
    assert harness.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    evidence = output["evidence"]
    assert evidence["mode"] == "offline_contract"
    assert evidence["live_transport_confirmed"] is False
    assert evidence["strict_material_audit"] is True
    assert len(evidence["fixture_sha256"]) == 64
    assert len(evidence["model_sha256"]) == 64
    assert evidence["run_id"].startswith("run_")
    assert len(evidence["run_id"]) == 36
    assert evidence["started_at"].endswith("Z")
    assert evidence["completed_at"].endswith("Z")
    assert [event["step"] for event in output["trace"]] == [
        "create_order", "upload_cad_model", "cad_model_materials",
        "set_link_materials", "cutting_materials", "run_cutting",
        "run_production_files", "production_files_url", "cutted_materials",
    ]
    serialized = json.dumps(output, ensure_ascii=False)
    for forbidden in (
        "offline-test-key", "__FIXTURE_CAD_SHEET__",
        "__FIXTURE_CONFIRMED_MATBASE_TARGET__", "signed-production-archive",
    ):
        assert forbidden not in serialized


def test_remote_material_mismatch_stops_before_links_and_paid_generation() -> None:
    fixture = load_cutting_fixture(harness.DEFAULT_FIXTURE, require_live_approval=False)
    session = harness._offline_session(fixture.data)
    session.responses[2] = harness._Response(200, ["UNAPPROVED MATERIAL"])
    trace = []
    with tempfile.TemporaryDirectory(prefix="meb140-mismatch-") as temp_dir:
        ledger = MutationLedger(Path(temp_dir) / "ledger.sqlite3")
        run = ledger.approve_run(
            approval_digest="e" * 64,
            mode="offline_contract",
            fixture_sha256=fixture.fixture_sha256,
            model_sha256=fixture.model_sha256,
            transport_origin=harness.TEST_ORIGIN,
            max_mutations=5,
            deadline_epoch=10_000_000_000,
        )
        client = CuttingClient(
            base_url=harness.TEST_ORIGIN,
            test_api_key="offline-test-key",
            test_endpoint_allowlist=[harness.TEST_ORIGIN],
            allow_live=True,
            allow_mutations=True,
            ledger=ledger,
            ledger_run=run,
            overall_timeout=30,
            session=session,
            trace_sink=trace.append,
        )
        with pytest.raises(ValueError, match="materials.unverified"):
            run_cutting_flow(client, fixture)
        assert ledger.run_operation_count(client.run_id) == 2
    assert [event["step"] for event in trace] == [
        "create_order", "upload_cad_model", "cad_model_materials",
    ]
    assert len(session.calls) == 3


def test_synthetic_fixture_is_rejected_by_live_loader_before_any_ledger() -> None:
    raw = harness.DEFAULT_FIXTURE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="rejects non-approved or synthetic"):
        load_cutting_fixture(
            harness.DEFAULT_FIXTURE,
            require_live_approval=True,
            approved_fixture_sha256=digest,
        )


def test_wrong_approved_fixture_hash_is_rejected_first() -> None:
    with pytest.raises(ValueError, match="separately approved"):
        load_cutting_fixture(
            harness.DEFAULT_FIXTURE,
            require_live_approval=True,
            approved_fixture_sha256="0" * 64,
        )


def test_ordinary_cli_has_no_live_or_mutation_flags() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    cutting_parser = source[source.index('p_cut = sub.add_parser("cutting"'):
                            source.index("p_mat = sub.add_parser", source.index('p_cut ='))]
    assert "--allow-live" not in cutting_parser
    assert "--allow-mutations" not in cutting_parser
    assert "run-cutting" not in cutting_parser
    assert 'choices=["info"]' in cutting_parser


def test_live_operator_entrypoint_is_separate_and_has_no_default_fixture() -> None:
    source = (ROOT / "operator" / "cutting_live_smoke.py").read_text(encoding="utf-8")
    assert "--fixture" not in source
    assert "--approved-fixture-sha256" not in source
    assert "--ledger" not in source
    assert "--run-id" not in source
    assert "load_canonical_operator_approval" in source
    assert "AUTHORIZATION_PHRASE" in source
    assert "cutting_test_order.json" not in source
