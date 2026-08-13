from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qa"))

import cutting_contract_harness as harness  # noqa: E402


def test_offline_contract_harness_runs_full_order_without_network(capsys) -> None:
    assert harness.main([]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["evidence"]["mode"] == "offline_contract"
    assert output["evidence"]["production_files_response_valid"] is True
    assert output["evidence"]["mutation_count"] == 5
    assert [event["step"] for event in output["trace"]] == [
        "create_order",
        "upload_cad_model",
        "cad_model_materials",
        "set_link_materials",
        "run_cutting",
        "run_production_files",
        "production_files_url",
    ]
    serialized = json.dumps(output, ensure_ascii=False)
    assert "offline-contract-key" not in serialized
    assert "MEB-140 FIXTURE BOARD" not in serialized
    assert "signed-production-archive" not in serialized


def test_material_mismatch_stops_before_link_and_paid_generation() -> None:
    fixture_path = harness.DEFAULT_FIXTURE
    fixture = harness._load_fixture(fixture_path)
    trace = []
    client, session = harness._offline_client(fixture, trace)
    session.responses[2] = harness._Response(200, ["UNAPPROVED MATERIAL"])

    try:
        harness.run_flow(
            client,
            fixture,
            fixture_path,
            idempotency_prefix="MEB-140-MISMATCH",
        )
    except ValueError as exc:
        assert "cutting.materials.unverified" in str(exc)
    else:
        raise AssertionError("unverified material links must stop the flow")

    assert [event["step"] for event in trace] == [
        "create_order", "upload_cad_model", "cad_model_materials"
    ]
    assert client.mutation_count == 2
    assert len(session.calls) == 3
