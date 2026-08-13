"""Fail-closed контракт Windows preflight для MEB-137."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "basis_env_preflight.ps1"
IMPORTER = ROOT / "scripts" / "ImportFurnitureFromJSON.js"
MATERIAL_BASE = ROOT / "materials" / "baza_materiala.json"
FIXTURE = ROOT / "projects" / "moderator_cabinet.json"
REAL_B3D = ROOT / "qa" / "fixtures" / "wardrobe_demo_production.b3d"
RUNBOOK = ROOT.parent.parent / "docs" / "BASIS_ENV_PREFLIGHT.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _powershell_51() -> str | None:
    if os.name != "nt":
        return None
    executable = shutil.which("powershell.exe")
    if not executable:
        return None
    probe = subprocess.run(
        [executable, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
        capture_output=True,
        text=True,
        check=False,
    )
    return executable if probe.returncode == 0 and probe.stdout.strip() == "5" else None


def _run_preflight(powershell: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *args,
            "-Json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_preflight_is_read_only_and_bounded_by_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'mode = "read-only"' in text
    assert "prohibitedActionsPerformed = @()" in text
    assert "Get-AuthenticodeSignature" in text
    assert "GetFullPath" in text and "Test-PathWithin" in text
    assert "ReparsePoint" in text
    assert "-Recurse" not in text
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
        "& $canonical",
        "Invoke-Expression",
    ):
        assert forbidden not in text


def test_preflight_requires_signed_identity_and_consistent_manifest():
    text = SCRIPT.read_text(encoding="utf-8")
    for evidence in (
        "signature.Status",
        "SignerCertificate.Subject",
        "ProductName",
        "CompanyName",
        "ProductVersion",
        "basis_executable_sha256",
        "material_base_sha256",
        "script_sha256",
        "fixture_sha256",
        "output_model_sha256",
        "basis-verification-v2",
        "executable_evidence_mismatch",
        "install_path_mismatch",
        "scripts_path_mismatch",
        "material_import_result_invalid",
        "material_slot_invalid",
        "material_thickness_invalid",
        "checked_at_stale",
        "output_model_magic_invalid",
    ):
        assert evidence in text


@pytest.mark.skipif(_powershell_51() is None, reason="requires Windows PowerShell 5.1")
def test_renamed_cmd_and_nonempty_manifest_never_become_ready(tmp_path: Path):
    """Regression: renamed signed Microsoft cmd.exe must not count as BAZIS."""

    powershell = _powershell_51()
    assert powershell is not None

    programs = Path(os.environ["LOCALAPPDATA"]) / "Programs"
    documents = Path(os.environ["USERPROFILE"]) / "Documents"
    if not programs.is_dir() or not documents.is_dir():
        pytest.skip("standard trusted parents are unavailable")

    with tempfile.TemporaryDirectory(prefix="BazisFake-", dir=programs) as install_dir:
        with tempfile.TemporaryDirectory(prefix="BazisFake-", dir=documents) as scripts_parent:
            fake_install = Path(install_dir)
            fake_scripts = Path(scripts_parent) / "Scripts"
            fake_scripts.mkdir()
            fake_executable = fake_install / "mebel.exe"
            shutil.copy2(Path(os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")), fake_executable)

            output_model = REAL_B3D
            import_evidence = tmp_path / "material-import-report.txt"
            import_evidence.write_text("synthetic material import evidence", encoding="utf-8")
            manifest = {
                "schemaVersion": "basis-verification-v2",
                "basis_version": "2026.5.6.0",
                "basis_install_path": str(fake_install),
                "basis_executable_sha256": _sha256(fake_executable),
                "scripts_path": str(fake_scripts),
                "material_base_sha256": _sha256(MATERIAL_BASE),
                "material_import": {
                    "method": "basis-material-import",
                    "result": "pass",
                    "outcome": "completed",
                    "basis_version": "2026.5.6.0",
                    "basis_install_path": str(fake_install),
                    "input_path": str(MATERIAL_BASE),
                    "input_sha256": _sha256(MATERIAL_BASE),
                    "input_count": 5047,
                    "imported_count": 5047,
                    "rejected_count": 0,
                    "board_count": 963,
                    "edge_count": 325,
                    "evidence_type": "basis-material-import-report",
                    "evidence_path": str(import_evidence),
                    "evidence_sha256": _sha256(import_evidence),
                },
                "materials": [
                    {"slot": "board", "basisName": "synthetic", "article": "FAKE-1", "thickness_mm": 16}
                ],
                "edges": [
                    {"basisName": "synthetic edge", "article": "FAKE-E", "thickness_mm": 2.0}
                ],
                "js_smoke": {
                    "script_sha256": _sha256(IMPORTER),
                    "fixture": str(FIXTURE),
                    "fixture_sha256": _sha256(FIXTURE),
                    "result": "pass",
                    "output_model": str(output_model),
                    "output_model_sha256": _sha256(output_model),
                    "model_type": "BZ85",
                    "magic_hex": "425A3835",
                    "section_marker_hex": "010000FF",
                    "file_size_bytes": output_model.stat().st_size,
                    "header_root_name": "Header",
                    "document_root_name": "Document",
                    "parser_result": "pass",
                    "model_node_found": True,
                    "document_section_found": True,
                    "document_compressed": True,
                    "trailer_bytes": 0,
                },
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            manifest_path = tmp_path / "basis-verification.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = _run_preflight(
                powershell,
                "-BasisInstallPath",
                str(fake_install),
                "-ScriptsPath",
                str(fake_scripts),
                "-VerificationManifestPath",
                str(manifest_path),
            )

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert "basis_executable_identity_not_confirmed" in report["blockers"]
    assert "operator_verification_manifest_missing_or_invalid" in report["blockers"]
    assert len(report["basis"]["acceptedRoots"]) == 1
    assert len(report["basis"]["executableEvidence"]) == 1
    assert report["basis"]["executableEvidence"][0]["identityValid"] is False
    assert report["basis"]["executableEvidence"][0]["signatureStatus"] == "Valid"
    assert "Microsoft" in report["basis"]["executableEvidence"][0]["companyName"]
    assert report["basis"]["trustedExecutables"] == []
    assert "executable_evidence_mismatch" in report["operatorVerification"]["errors"]
    assert not any(
        error.startswith("output_model_") or error.startswith("material_import_")
        for error in report["operatorVerification"]["errors"]
    )
    assert report["operatorVerification"]["outputModel"]["structureValid"] is True
    assert report["operatorVerification"]["outputModel"]["headerRootName"] == "Header"
    assert report["operatorVerification"]["outputModel"]["documentRootName"] == "Document"
    assert report["operatorVerification"]["outputModel"]["modelNodeFound"] is True
    assert report["operatorVerification"]["outputModel"]["adler32Valid"] is True
    assert report["operatorVerification"]["outputModel"]["trailerBytes"] == 0


@pytest.mark.skipif(_powershell_51() is None, reason="requires Windows PowerShell 5.1")
def test_arbitrary_install_root_is_rejected_before_any_traversal(tmp_path: Path):
    powershell = _powershell_51()
    assert powershell is not None
    fake_root = tmp_path / "BazisFake"
    nested = fake_root / "deep" / "nested"
    nested.mkdir(parents=True)
    shutil.copy2(Path(os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")), nested / "mebel.exe")

    result = _run_preflight(powershell, "-BasisInstallPath", str(fake_root))

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert "basis_install_path_untrusted_scope" in report["blockers"]
    resolved_fake_root = fake_root.resolve()
    assert all(
        Path(row["path"]).resolve() != resolved_fake_root
        for row in report["basis"]["acceptedRoots"]
    )
    assert all(
        not Path(row["path"]).resolve().is_relative_to(resolved_fake_root)
        for row in report["basis"]["executableEvidence"]
    )
    assert any(row["reason"] == "outside_trusted_scope" for row in report["basis"]["rejectedRoots"])


@pytest.mark.skipif(_powershell_51() is None, reason="requires Windows PowerShell 5.1")
def test_manifest_rejects_import_slot_thickness_age_and_json_output_counterexample(tmp_path: Path):
    powershell = _powershell_51()
    assert powershell is not None
    fake_install = tmp_path / "BazisFake"
    fake_scripts = tmp_path / "BazisN" / "Scripts"
    fake_install.mkdir()
    fake_scripts.mkdir(parents=True)
    fake_executable = fake_install / "mebel.exe"
    shutil.copy2(Path(os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")), fake_executable)
    import_evidence = tmp_path / "material-import-report.txt"
    import_evidence.write_text("counterexample without import result", encoding="utf-8")

    manifest = {
        "schemaVersion": "basis-verification-v2",
        "basis_version": "2026.5.6.0",
        "basis_install_path": str(fake_install),
        "basis_executable_sha256": _sha256(fake_executable),
        "scripts_path": str(fake_scripts),
        "material_base_sha256": _sha256(MATERIAL_BASE),
        "material_import": {
            "method": "basis-material-import",
            "outcome": "completed",
            "basis_version": "2026.5.6.0",
            "basis_install_path": str(fake_install),
            "input_path": str(MATERIAL_BASE),
            "input_sha256": _sha256(MATERIAL_BASE),
            "input_count": 5047,
            "imported_count": 5047,
            "rejected_count": 0,
            "board_count": 963,
            "edge_count": 325,
            "evidence_type": "basis-material-import-report",
            "evidence_path": str(import_evidence),
            "evidence_sha256": _sha256(import_evidence),
        },
        "materials": [
            {"slot": "not-board", "basisName": "synthetic", "article": "FAKE-1", "thickness_mm": -16}
        ],
        "edges": [
            {"basisName": "synthetic edge", "article": "FAKE-E", "thickness_mm": -2.0}
        ],
        "js_smoke": {
            "script_sha256": _sha256(IMPORTER),
            "fixture": str(FIXTURE),
            "fixture_sha256": _sha256(FIXTURE),
            "result": "pass",
            "output_model": str(FIXTURE),
            "output_model_sha256": _sha256(FIXTURE),
            "model_type": "BZ85",
            "magic_hex": "425A3835",
            "section_marker_hex": "010000FF",
            "file_size_bytes": FIXTURE.stat().st_size,
            "header_root_name": "Header",
            "parser_result": "pass",
            "model_node_found": True,
            "document_section_found": True,
            "document_compressed": True,
            "trailer_bytes": 64,
        },
        "checked_at": "2020-01-01T00:00:00Z",
    }
    manifest_path = tmp_path / "counterexample.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_preflight(
        powershell,
        "-BasisInstallPath",
        str(fake_install),
        "-ScriptsPath",
        str(fake_scripts),
        "-VerificationManifestPath",
        str(manifest_path),
    )

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    errors = set(report["operatorVerification"]["errors"])
    assert {
        "material_import_result_invalid",
        "material_slot_invalid",
        "material_thickness_invalid",
        "edge_thickness_invalid",
        "checked_at_stale",
        "output_model_extension_invalid",
        "output_model_magic_invalid",
        "output_model_evidence_invalid",
    } <= errors
    assert report["status"] == "blocked"


def test_runbook_documents_identity_manifest_and_no_binary_execution():
    text = RUNBOOK.read_text(encoding="utf-8")
    for required in (
        "Authenticode",
        "ProductName",
        "CompanyName",
        "basis-verification-v2",
        "basis_executable_sha256",
        "material_import",
        "fixture_sha256",
        "output_model_sha256",
        "ImportFurnitureFromJSON.js",
    ):
        assert required in text
    assert "не запускает" in text and "executable БАЗИС" in text
