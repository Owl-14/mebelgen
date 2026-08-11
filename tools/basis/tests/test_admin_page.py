"""Static contracts for the standalone identity/admin interface."""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from src.admin_page import activation_page, admin_page, login_page


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)


@pytest.mark.parametrize("page", [login_page, activation_page, admin_page])
def test_admin_pages_have_unique_ids_and_no_remote_ui_dependencies(page) -> None:
    html = page()
    parser = _IdCollector()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    assert "linear-gradient" not in html
    assert "backdrop-filter" not in html
    assert "https://" not in html
    assert "http://" not in html
    assert "/assets/studio/akeda-studio-wordmark.png" in html
    assert "/assets/studio/akeda-studio-mark.png" in html


def test_login_handles_multi_company_selection_without_html_injection() -> None:
    html = login_page()

    assert "organization_selection_required" in html
    assert "organization.replaceChildren" in html
    assert "organization.innerHTML" not in html


def test_admin_ui_preserves_identity_and_migration_boundaries() -> None:
    html = admin_page()

    assert "numeric*1000" in html
    assert "raw.membership||firstArray(raw.memberships)" in html
    assert "Каталоги и чаты пока не подключены" in html
    assert "история AI-чатов" in html
    assert "tenant-миграции" in html
    assert "Открыть Studio" in html
    assert "Открыть компанию" in html
    assert "Вы просматриваете компанию" in html
    assert "от имени Akeda" in html
    assert "Вернуться в админку" in html
    assert "'support.started':'Администратор Akeda открыл компанию'" in html


def test_owner_cabinet_exposes_team_management_without_owner_escalation() -> None:
    html = admin_page()

    assert "function canManageMembers()" in html
    assert "hasCompanyPermission('member.manage')" in html
    assert "memberRole(member)!=='owner'" in html
    assert "Управление компанией" in html
    assert "Вернуться в Studio" in html
    assert "Роль владельца и передача владения управляются только через Akeda." in html
    assert "roleOptions(role,platformManagesCompany())" in html
    assert "'member.provisioned':'Добавлен сотрудник'" in html
    assert "'member.access_rotated':'Выпущен новый пароль сотрудника'" in html


def test_company_entry_hides_internal_access_mechanics() -> None:
    html = admin_page()

    assert "data-enter-company" in html
    assert "function enterCompany" in html
    assert "function leaveCompany" in html
    assert "const resumed=activeSupport()" in html
    assert "await loadSnapshot(resumedOrgId);state.page='company'" in html
    assert "/api/admin/company-view/start" in html
    assert "/api/admin/company-view/end" in html
    assert "/api/admin/support/start" not in html
    assert "Причина и номер обращения" not in html
    assert "Срок доступа" not in html
    assert "read-only" not in html.lower()
    assert "support-сеан" not in html.lower()
    assert "Доступ поддержки" not in html
    assert "отдельная временная копия демо-каталога" in html
