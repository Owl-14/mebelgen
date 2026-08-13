"""Контракт безопасного Windows preflight для MEB-137."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "basis_env_preflight.ps1"
RUNBOOK = ROOT.parent.parent / "docs" / "BASIS_ENV_PREFLIGHT.md"


def test_preflight_is_read_only_by_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'mode = "read-only"' in text
    assert "prohibitedActionsPerformed = @()" in text
    for forbidden in (
        "Start-Process",
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "Set-ItemProperty",
        "Remove-Item",
        "New-Item",
        "winget ",
        "choco ",
        "msiexec",
    ):
        assert forbidden not in text


def test_preflight_requires_external_operator_evidence():
    text = SCRIPT.read_text(encoding="utf-8")
    for blocker in (
        "basis_installation_not_found",
        "basis_version_not_confirmed",
        "basis_scripts_path_not_confirmed",
        "operator_verification_manifest_missing_or_invalid",
    ):
        assert blocker in text
    assert '"result") -eq "pass"' in text
    assert "basisName" in text and "article" in text and "thickness_mm" in text


def test_runbook_covers_required_manual_checks_and_prohibitions():
    text = RUNBOOK.read_text(encoding="utf-8")
    for required in (
        "DisplayVersion",
        "BazisN\\Scripts",
        "baza_materiala.json",
        "basisName",
        "article",
        "thickness_mm",
        "ImportFurnitureFromJSON.js",
        "output_model",
    ):
        assert required in text
    for prohibition in ("не устанавливает", "не лицензирует", "платные операции"):
        assert prohibition in text
