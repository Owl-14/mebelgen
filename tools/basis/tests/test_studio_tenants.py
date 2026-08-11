"""Recoverable tenant catalog lifecycle for MEB-114."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio_tenants import TenantWorkspaceManager  # noqa: E402


def _auth(organization_id: str = "org-a") -> dict:
    return {
        "context": {
            "organization": {"id": organization_id, "mode": "standard"},
            "session": {"id": "session-a"},
        }
    }


def _manager(tmp_path: Path) -> tuple[TenantWorkspaceManager, dict, Path]:
    legacy = tmp_path / "legacy" / "legacy.json"
    legacy.parent.mkdir()
    legacy.write_text(
        json.dumps({
            "schemaVersion": "paramspec-v1",
            "draft": True,
            "project_name": "Legacy",
        }),
        encoding="utf-8",
    )
    manager = TenantWorkspaceManager(
        legacy,
        tmp_path / "legacy-out",
        tenant_root=tmp_path / "tenants",
    )
    auth = _auth()
    workspace = manager.workspace(auth)
    spec = {
        "schemaVersion": "paramspec-v1",
        "project_name": "Тумба для архива",
        "furniture_type": "тумба",
        "archetype": "cabinet",
        "dimensions": {"width": 800, "depth": 400, "height": 720},
        "materials": {"color": "Дуб"},
        "catalog": {
            "creator_user_id": "designer-a",
            "responsible_user_id": "designer-a",
            "author": "Автор",
            "responsible": "Автор",
        },
    }
    product = workspace.spec_dir / "product.json"
    product.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    (workspace.spec_dir / "product.versions.json").write_text(
        json.dumps([{"ts": "2026-08-10T12:00:00", "spec": spec}], ensure_ascii=False),
        encoding="utf-8",
    )
    preview_dir = workspace.spec_dir / ".previews"
    preview_dir.mkdir()
    (preview_dir / "product.png").write_bytes(b"\x89PNG\r\n\x1a\npreview")
    history_dir = workspace.spec_dir / ".history"
    history_dir.mkdir()
    (history_dir / "product.ai.json").write_text(
        json.dumps([{"message": "Добавь полку"}], ensure_ascii=False),
        encoding="utf-8",
    )
    for suffix, payload in (
        (".project.json", b"project"),
        (".cfrn", b"cfrn"),
        (".b3d", b"b3d"),
    ):
        (workspace.out_dir / f"product{suffix}").write_bytes(payload)
    delivery = workspace.out_dir / "deliveries" / "тумба_для_архива"
    delivery.mkdir(parents=True)
    (delivery / "index.html").write_text("delivery", encoding="utf-8")
    manager.set_current(auth, product)
    return manager, auth, product


def test_ai_history_persists_trace_id_for_studio_comparison(tmp_path: Path) -> None:
    manager, auth, product = _manager(tmp_path)
    trace_id = "0123456789abcdef0123456789abcdef"
    entry = manager.append_ai_history(
        auth,
        product,
        message="Измени ширину",
        reply="Готово",
        before_spec={"schemaVersion": "paramspec-v1"},
        after_spec={"schemaVersion": "paramspec-v1", "draft": True},
        trace_id=trace_id,
    )
    assert entry["trace_id"] == trace_id
    assert manager.read_ai_history(auth, product)[-1]["trace_id"] == trace_id


def test_archive_round_trip_preserves_model_history_preview_and_outputs(
    tmp_path: Path,
) -> None:
    manager, auth, product = _manager(tmp_path)
    workspace = manager.workspace(auth)

    archived = manager.archive_product(
        auth,
        product,
        actor_user_id="designer-a",
        actor_name="Автор",
        reason="Заказ отменён",
    )

    assert not product.exists()
    assert not (workspace.spec_dir / "product.versions.json").exists()
    assert not (workspace.spec_dir / ".previews" / "product.png").exists()
    assert not (workspace.spec_dir / ".history" / "product.ai.json").exists()
    # Production outputs remain addressable by builds.json, while the archive
    # also contains immutable copies in case those paths are later reused.
    assert (workspace.out_dir / "product.b3d").read_bytes() == b"b3d"

    [listed] = manager.list_archived_products(auth)
    assert listed["id"] == archived["id"]
    assert listed["reason"] == "Заказ отменён"
    assert listed["preview"] is True
    assert manager.archived_preview_path(auth, archived["id"]).read_bytes().startswith(
        b"\x89PNG"
    )
    bundle = workspace.spec_dir.parent / "archive" / archived["id"]
    assert (bundle / "data" / "out" / "product.b3d").read_bytes() == b"b3d"
    assert (
        bundle / "data" / "out" / "deliveries" / "тумба_для_архива" / "index.html"
    ).is_file()

    restored = manager.restore_product(
        auth,
        archived["id"],
        actor_user_id="owner-a",
        actor_name="Владелец",
    )
    assert restored["state"] == "restored"
    assert json.loads(product.read_text(encoding="utf-8"))["project_name"] == (
        "Тумба для архива"
    )
    assert (workspace.spec_dir / "product.versions.json").is_file()
    assert (workspace.spec_dir / ".history" / "product.ai.json").is_file()
    assert (workspace.spec_dir / ".previews" / "product.png").is_file()
    assert manager.list_archived_products(auth) == []


def test_restore_refuses_to_overwrite_an_active_file(tmp_path: Path) -> None:
    manager, auth, product = _manager(tmp_path)
    archived = manager.archive_product(
        auth,
        product,
        actor_user_id="designer-a",
        actor_name="Автор",
        reason="Дубль изделия",
    )
    product.write_text(
        json.dumps({"schemaVersion": "paramspec-v1", "project_name": "Новый файл"}),
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError):
        manager.restore_product(
            auth,
            archived["id"],
            actor_user_id="owner-a",
            actor_name="Владелец",
        )

    assert json.loads(product.read_text(encoding="utf-8"))["project_name"] == "Новый файл"
    assert manager.list_archived_products(auth)[0]["id"] == archived["id"]


def test_archive_is_tenant_scoped(tmp_path: Path) -> None:
    manager, auth, product = _manager(tmp_path)
    archived = manager.archive_product(
        auth,
        product,
        actor_user_id="designer-a",
        actor_name="Автор",
        reason="Заказ отменён",
    )

    other = _auth("org-b")
    assert manager.list_archived_products(other) == []
    with pytest.raises(FileNotFoundError):
        manager.archived_preview_path(other, archived["id"])
    with pytest.raises(FileNotFoundError):
        manager.restore_product(
            other,
            archived["id"],
            actor_user_id="owner-b",
            actor_name="Другой владелец",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (("restore_root", "outside"), ("restore_path", "../outside.json")),
)
def test_restore_rejects_corrupt_manifest_paths(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    manager, auth, product = _manager(tmp_path)
    workspace = manager.workspace(auth)
    archived = manager.archive_product(
        auth,
        product,
        actor_user_id="designer-a",
        actor_name="Автор",
        reason="Проверка архива",
    )
    bundle = workspace.spec_dir.parent / "archive" / archived["id"]
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0][field] = value
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="archive manifest|границу компании"):
        manager.restore_product(
            auth,
            archived["id"],
            actor_user_id="owner-a",
            actor_name="Владелец",
        )

    assert not product.exists()
    assert not (workspace.spec_dir.parent / "outside.json").exists()
    assert manager.list_archived_products(auth)[0]["id"] == archived["id"]
