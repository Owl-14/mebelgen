"""Static contract for the standalone Studio catalog workspace (MEB-116)."""

from __future__ import annotations

import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE, _filter_catalog_projects, _list_projects  # noqa: E402


class _DomIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.by_id[str(values["id"])] = (tag, values)


def _dom() -> _DomIndex:
    dom = _DomIndex()
    dom.feed(PAGE)
    return dom


def _style() -> str:
    match = re.search(r"<style>(.*?)</style>", PAGE, flags=re.DOTALL | re.IGNORECASE)
    assert match
    return match.group(1)


def test_catalog_has_its_own_accessible_context() -> None:
    dom = _dom()
    tag, catalog = dom.by_id["catalog"]
    assert tag == "div"
    assert catalog.get("role") == "region"
    assert catalog.get("aria-labelledby") == "catalogTitle"
    assert catalog.get("aria-hidden") == "true"

    context_tag, context = dom.by_id["catalogContext"]
    assert context_tag == "section"
    assert context.get("aria-labelledby") == "catalogContextTitle"
    assert "hidden" in context
    assert "catalogContextCount" in dom.by_id

    close_tag, close = dom.by_id["catClose"]
    assert close_tag == "button"
    assert close.get("type") == "button"
    assert close.get("aria-label") == "Закрыть каталог"

    inspector_tag, inspector = dom.by_id["catInspector"]
    assert inspector_tag == "aside"
    assert inspector.get("aria-labelledby") == "catInspectName"
    assert "hidden" in inspector
    assert dom.by_id["catGrid"][1].get("role") == "listbox"
    assert dom.by_id["catOpen"][1].get("type") == "button"
    assert dom.by_id["catScopes"][1].get("role") == "group"
    assert dom.by_id["catResponsible"][1].get("aria-label") == "Ответственный"
    assert dom.by_id["catStatus"][1].get("aria-label") == "Статус изделия"
    assert dom.by_id["catalogPreviewRenderer"][1].get("aria-hidden") == "true"


def test_catalog_uses_canonical_3d_snapshots_and_background_refresh() -> None:
    markup = PAGE[PAGE.index("function catalogThumbMarkup") : PAGE.index("function catalogTypeLabel")]
    assert "item.preview_current?exact:fallback" in markup
    assert "data-preview-file" in markup
    assert "data-fallback" in markup
    assert "MebelScene($('catalogPreviewRenderer'))" in PAGE
    assert "fetch('/api/catalog-preview-source'" in PAGE
    assert "fetch('/api/catalog-preview'" in PAGE
    assert "CATALOG_PREVIEW_SCENE.setView('axon')" in PAGE
    assert "CATALOG_PREVIEW_SCENE.snapshot(360)" in PAGE
    assert "queueCatalogPreviews(items)" in PAGE
    save = PAGE[PAGE.index("async function saveSpec") : PAGE.index("$('btnSave').onclick")]
    assert "scene3d.snapshot" not in save
    assert "queueCatalogPreviews" in save


def test_catalog_mode_removes_stale_product_panels_from_layout() -> None:
    css = _style()
    assert re.search(
        r"#app\.catalog-mode[^\{]*\{[^}]*grid-template-columns\s*:\s*"
        r"var\(--side-width\)\s+minmax\(0,1fr\)",
        css,
        flags=re.DOTALL,
    )
    assert "#app.catalog-mode #fs_project" in css
    assert "#app.catalog-mode #modelState" in css
    assert "#app.catalog-mode #fs_part" in css
    assert "#app.catalog-mode #rightside" in css
    assert "#app.catalog-mode #rightRail" in css
    assert "#app.catalog-mode #catalogContext" in css


def test_catalog_mode_is_a_single_state_transition_api() -> None:
    declaration = re.search(r"function\s+setCatalogMode\s*\(", PAGE)
    assert declaration
    function_slice = PAGE[declaration.start() : declaration.start() + 1900]
    assert "classList.toggle('catalog-mode',on)" in function_slice
    assert "classList.toggle('on',on)" in function_slice
    assert "setAttribute('aria-hidden',String(!on))" in function_slice
    assert "element.inert=on" in function_slice
    assert "scene3d.resize()" in function_slice
    assert "catalogContext" in function_slice

    assert "$('projCat').onclick=()=>openCatalog()" in PAGE
    assert "$('catClose').onclick=()=>closeCatalog()" in PAGE
    assert "if(CATALOG_MODE){" in PAGE
    assert "if(CAT_SELECTED_FILE){clearCatalogSelection" in PAGE
    assert "closeCatalog();return;" in PAGE
    assert "history.pushState({akedaView:'catalog'}" in PAGE
    assert "window.addEventListener('popstate'" in PAGE
    assert "c.onclick=()=>catalogCardClick(c.dataset.f)" in PAGE
    assert "now-CAT_LAST_CLICK_AT<420" in PAGE
    assert "$('catOpen').onclick=()=>openCatalogItem()" in PAGE


def test_catalog_selection_does_not_open_model_context() -> None:
    render = PAGE[PAGE.index("function renderCatalog(){") : PAGE.index("async function openCatalogItem")]
    assert "role=\"option\"" in render
    assert "aria-selected" in render
    assert "catalogCardClick(c.dataset.f)" in render
    assert "fetch('/api/open'" not in render

    inspector = PAGE[PAGE.index("function renderCatalogInspector(){") : PAGE.index("function syncCatalogSelection")]
    assert "catWorkspace').classList.toggle('has-selection'" in inspector
    assert "catInspectResponsible" in inspector
    assert "catInspectAuthor" in inspector
    assert "catInspectUpdated" in inspector
    assert "catalogOwnerMarkup(p)" in render


def test_catalog_keyboard_contract_and_escape_priority() -> None:
    keyboard = PAGE[PAGE.index("function catalogCardKeydown") : PAGE.index("async function renameCatalogItem")]
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter", "Escape"):
        assert key in keyboard
    assert "if(!CAT_SELECTED_FILE){event.preventDefault();event.stopPropagation();closeCatalog();return;}" in keyboard
    assert "if(CAT_SELECTED_FILE){clearCatalogSelection" in PAGE
    assert "openCatalogItem(file)" in keyboard


def test_catalog_projects_publish_real_metadata(tmp_path: Path) -> None:
    spec = {
        "schemaVersion": "paramspec-v1",
        "project_name": "Тестовое изделие",
        "furniture_type": "тумба",
        "archetype": "cabinet",
        "dimensions": {"width": 800, "depth": 400, "height": 720},
        "materials": {"color": "Дуб"},
        "catalog": {
            "creator_user_id": "user-1",
            "responsible_user_id": "user-2",
            "responsible": "Старое имя",
            "author": "Старый автор",
        },
    }
    (tmp_path / "product.json").write_text(
        json.dumps(spec, ensure_ascii=False), encoding="utf-8"
    )
    [project] = _list_projects(
        tmp_path, member_names={"user-1": "Илья", "user-2": "Алексей"}
    )
    assert project["responsible"] == "Алексей"
    assert project["author"] == "Илья"
    assert project["creator_user_id"] == "user-1"
    assert project["responsible_user_id"] == "user-2"
    assert project["category"] == "Тумбы"
    assert project["status"] == "active"
    assert isinstance(project["updated_at"], int) and project["updated_at"] > 0
    assert project["preview"] is False
    assert project["preview_current"] is False


def test_catalog_preview_is_current_only_after_the_spec_mtime(tmp_path: Path) -> None:
    spec_path = tmp_path / "product.json"
    spec_path.write_text(json.dumps({
        "schemaVersion": "paramspec-v1",
        "project_name": "Тест",
        "archetype": "cabinet",
        "dimensions": {"width": 800, "depth": 400, "height": 720},
        "materials": {"color": "Белый"},
    }), encoding="utf-8")
    preview_dir = tmp_path / ".previews"
    preview_dir.mkdir()
    preview_path = preview_dir / "product.png"
    preview_path.write_bytes(b"png")

    os.utime(preview_path, ns=(1_000_000_000, 1_000_000_000))
    os.utime(spec_path, ns=(2_000_000_000, 2_000_000_000))
    [stale] = _list_projects(tmp_path)
    assert stale["preview"] is True
    assert stale["preview_current"] is False

    os.utime(preview_path, ns=(3_000_000_000, 3_000_000_000))
    [current] = _list_projects(tmp_path)
    assert current["preview_current"] is True


def test_catalog_filters_are_server_side_and_composable() -> None:
    projects = [
        {
            "name": "Тумба Алексея",
            "responsible_user_id": "alexey",
            "category": "Тумбы",
            "status": "active",
        },
        {
            "name": "Шкаф Ильи",
            "responsible_user_id": "ilya",
            "category": "Шкафы",
            "status": "active",
        },
        {
            "name": "Черновик тумбы",
            "responsible_user_id": "",
            "category": "Черновики",
            "status": "draft",
        },
    ]
    assert [item["name"] for item in _filter_catalog_projects(
        projects, {"scope": "mine"}, current_user_id="alexey"
    )] == ["Тумба Алексея"]
    assert [item["name"] for item in _filter_catalog_projects(
        projects, {"scope": "unassigned"}, current_user_id="alexey"
    )] == ["Черновик тумбы"]
    assert [item["name"] for item in _filter_catalog_projects(
        projects,
        {"responsible_user_id": "ilya", "type": "Шкафы", "status": "active", "q": "шкаф"},
        current_user_id="alexey",
    )] == ["Шкаф Ильи"]
