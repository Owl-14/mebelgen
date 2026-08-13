from __future__ import annotations

import json
from pathlib import Path

from src.generators import generate_from_paramspec
from tests import regression


ROOT = Path(__file__).resolve().parent.parent


def test_main_fails_when_existing_golden_placement_is_corrupted(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    paramspecs = tmp_path / "paramspecs"
    projects = tmp_path / "projects"
    paramspecs.mkdir()
    projects.mkdir()

    source = ROOT / "paramspecs" / "tz_stol_kofeyny_cube.json"
    spec_path = paramspecs / "corrupted.json"
    spec = json.loads(source.read_text(encoding="utf-8"))
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    golden = generate_from_paramspec(spec)
    golden["panels"][0]["placement"]["x1"] += 1_000
    (projects / spec_path.name).write_text(
        json.dumps(golden, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(regression, "ROOT", tmp_path)
    monkeypatch.setattr(regression, "SPECS", [spec_path])

    assert regression.main() == 1
    assert "exact vs golden: 0/1" in capsys.readouterr().out
