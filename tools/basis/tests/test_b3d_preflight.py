from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.b3d_preflight import B3DPreflightError, evaluate_b3d_project_preflight  # noqa: E402
from src.build_b3d import build_b3d, build_b3d_from_paramspec  # noqa: E402
from src.generators import generate_from_paramspec  # noqa: E402


SPEC = json.loads(
    (ROOT / "paramspecs" / "stol_ofisny_foto.json").read_text(encoding="utf-8")
)


class FakeCloudClient:
    def __init__(self) -> None:
        self.convert_calls = 0

    def model_convert(self, files: list[str], convert_type: int) -> int:
        assert files and Path(files[0]).stat().st_size > 0
        assert convert_type == 1
        self.convert_calls += 1
        return 132

    def poll(self, task_id: int, *, timeout: float) -> dict[str, int]:
        assert task_id == 132 and timeout > 0
        return {"state": 2}

    def download_result(self, task_id: int, out_path: str | Path) -> Path:
        out = Path(out_path)
        out.write_bytes(b"offline-fake-b3d")
        return out


def test_green_paramspec_runs_full_preflight_before_cloud(tmp_path: Path) -> None:
    client = FakeCloudClient()
    preflight = evaluate_b3d_project_preflight(generate_from_paramspec(SPEC)).to_dict()

    result = build_b3d_from_paramspec(SPEC, tmp_path / "result.b3d", client=client)

    assert client.convert_calls == 1
    assert result["bytes"] > 0
    assert preflight["ok"] is True
    assert {check["name"] for check in preflight["checks"]} >= {
        "project_schema", "basis_mapping", "consistency", "geometry",
        "cfrn_encoding", "cfrn_holes_parity", "drilling_geometry",
        "materials", "cfrn_archive",
    }


def test_red_paramspec_never_reaches_paid_client(tmp_path: Path) -> None:
    client = FakeCloudClient()
    red = copy.deepcopy(SPEC)
    red.setdefault("overrides", []).append({
        "action": "add",
        "panel": "floating",
        "type": "shelf",
        "placement": {"x1": 100, "x2": 200, "y1": 100, "y2": 116,
                      "z1": 100, "z2": 200},
    })

    with pytest.raises(B3DPreflightError) as caught:
        build_b3d_from_paramspec(red, tmp_path / "must-not-exist.b3d", client=client)

    assert client.convert_calls == 0
    assert not (tmp_path / "must-not-exist.b3d").exists()
    assert any(error["code"] == "model.incomplete" for error in caught.value.report["errors"])


def test_project_mapping_and_material_contracts_are_mandatory(tmp_path: Path) -> None:
    client = FakeCloudClient()
    project = generate_from_paramspec(SPEC)
    project["basis_mapping"]["ready_for_import"] = False

    report = evaluate_b3d_project_preflight(project).to_dict()
    with pytest.raises(B3DPreflightError):
        build_b3d(project, tmp_path / "must-not-exist.b3d", client=client,
                  skip_encoding_check=True)

    assert not report["ok"]
    assert any(error["code"] == "project.not_ready_for_import"
               for error in report["errors"])
    assert client.convert_calls == 0


def test_project_preflight_does_not_mutate_caller_data() -> None:
    project = generate_from_paramspec(SPEC)
    before = copy.deepcopy(project)

    result = evaluate_b3d_project_preflight(project)

    assert result.ok
    assert project == before
    assert "material_refs" not in project
    assert result.project["material_refs"]
