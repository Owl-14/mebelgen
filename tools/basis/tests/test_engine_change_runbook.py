from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_engine_runbook_is_mandatory_and_covers_release_safety():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    runbook_path = ROOT / "rules" / "engine-change-runbook.md"
    runbook = runbook_path.read_text(encoding="utf-8")

    assert "rules/engine-change-runbook.md" in agents
    assert "До первого изменения обязательно полностью прочитай" in agents
    for contract in (
        "create_paramspec",
        "edit_operations",
        "production_gate",
        "copy-on-write",
        "trace_id",
        "paramspec-v1",
        "python -m pytest tests/ -q",
        "python -m tests.regression",
        "rollback",
    ):
        assert contract in runbook, f"runbook lost required contract: {contract}"
