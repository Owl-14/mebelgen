"""Production-shaped identity and tenant-access foundation tests."""

from __future__ import annotations

import sqlite3
import sys
import re
from pathlib import Path

import pytest
from argon2 import PasswordHasher, Type


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.identity import (  # noqa: E402
    AuthenticationFailed,
    Conflict,
    DEMO_SESSION_PERMISSIONS,
    IdentityStore,
    InvalidInvitation,
    PermissionDenied,
    SCHEMA_VERSION,
    SoleOwner,
    ValidationError,
)


ADMIN_PASSWORD = "platform admin password 2026"
OWNER_PASSWORD = "company owner password 2026"
MEMBER_PASSWORD = "company member password 2026"


@pytest.fixture
def store(tmp_path: Path) -> IdentityStore:
    # The algorithm is still Argon2id; smaller costs keep unit tests fast.
    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8 * 1024,
        parallelism=1,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
    identity = IdentityStore(tmp_path / "identity.sqlite3", password_hasher=hasher)
    assert identity.migrate() == SCHEMA_VERSION
    return identity


def _bootstrap(store: IdentityStore, *, now: int = 1_000) -> dict:
    return store.bootstrap_platform_admin(
        "platform@akeda.test", "Platform Owner", ADMIN_PASSWORD, now=now
    )


def _organization_with_owner(
    store: IdentityStore,
    *,
    now: int = 1_000,
    owner_email: str = "owner@example.test",
    owner_password: str = OWNER_PASSWORD,
) -> tuple[dict, dict, dict]:
    platform = _bootstrap(store, now=now)
    created = store.create_organization(
        platform["id"],
        "Example Furniture",
        "Company Owner",
        owner_email,
        now=now + 1,
    )
    owner_login = store.activate_invitation(
        created["activation_token"], owner_password, now=now + 2
    )
    return platform, created["organization"], owner_login


def _row(path: Path, sql: str, parameters: tuple = ()) -> sqlite3.Row:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        result = connection.execute(sql, parameters).fetchone()
        assert result is not None
        return result
    finally:
        connection.close()


def test_migration_enables_sqlite_safety_and_required_tables(store: IdentityStore) -> None:
    connection = store._connect()
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert {
        "users",
        "organizations",
        "memberships",
        "sessions",
        "invitations",
        "support_sessions",
        "audit_events",
    } <= tables


def test_demo_company_mode_is_explicit_and_visible_in_snapshot(store: IdentityStore) -> None:
    platform = _bootstrap(store)
    created = store.create_organization(
        platform["id"],
        "Akeda Demo",
        "Demo Owner",
        "demo@akeda.test",
        mode="demo",
        now=1_001,
    )

    assert created["organization"]["mode"] == "demo"
    snapshot = store.snapshot(platform["id"], now=1_002)
    assert next(item for item in snapshot["organizations"] if item["id"] == created["organization"]["id"])["mode"] == "demo"
    with pytest.raises(ValidationError, match="режим компании"):
        store.create_organization(
            platform["id"], "Bad Mode", "Owner", "bad-mode@akeda.test", mode="mutable-demo"
        )


def test_demo_login_can_only_change_its_ephemeral_workspace(store: IdentityStore) -> None:
    platform = _bootstrap(store)
    created = store.create_organization(
        platform["id"],
        "Akeda Demo",
        "Demo Owner",
        "demo@akeda.test",
        mode="demo",
        now=1_001,
    )
    demo = store.activate_invitation(
        created["activation_token"], OWNER_PASSWORD, now=1_002
    )

    permissions = store.organization_permissions(
        demo["user"]["id"], created["organization"]["id"], now=1_003
    )
    session = store.session(demo["session_token"], now=1_003)

    assert permissions == DEMO_SESSION_PERMISSIONS
    assert session is not None
    assert set(session["permissions"]) == DEMO_SESSION_PERMISSIONS
    assert "project.write" in permissions
    assert "ai.run" in permissions
    assert "member.invite" not in permissions
    assert "organization.manage" not in permissions
    assert "production.export" not in permissions
    with pytest.raises(PermissionDenied):
        store.invite_member(
            demo["user"]["id"],
            created["organization"]["id"],
            "intruder@akeda.test",
            "Intruder",
            "owner",
            now=1_004,
        )


def test_password_and_opaque_tokens_are_never_stored_in_plaintext(
    store: IdentityStore,
) -> None:
    platform = _bootstrap(store)
    created = store.create_organization(
        platform["id"], "Secretless Company", "Owner", "owner@secret.test", now=1_001
    )
    invitation_token = created["activation_token"]
    activated = store.activate_invitation(invitation_token, OWNER_PASSWORD, now=1_002)

    user_row = _row(
        store.path, "SELECT password_hash FROM users WHERE id = ?", (activated["user"]["id"],)
    )
    session_row = _row(
        store.path,
        "SELECT token_hash, csrf_hash FROM sessions WHERE id = ?",
        (activated["session"]["id"],),
    )
    invitation_row = _row(
        store.path,
        "SELECT token_hash FROM invitations WHERE id = ?",
        (created["invitation"]["id"],),
    )

    assert user_row["password_hash"].startswith("$argon2id$")
    assert OWNER_PASSWORD not in user_row["password_hash"]
    assert bytes(session_row["token_hash"]) != activated["session_token"].encode()
    assert bytes(session_row["csrf_hash"]) != activated["csrf_token"].encode()
    assert bytes(invitation_row["token_hash"]) != invitation_token.encode()
    database_bytes = store.path.read_bytes()
    for secret in (
        OWNER_PASSWORD,
        invitation_token,
        activated["session_token"],
        activated["csrf_token"],
    ):
        assert secret.encode() not in database_bytes


def test_invitation_expiry_boundary_reissue_and_replay(store: IdentityStore) -> None:
    platform = _bootstrap(store, now=100)
    created = store.create_organization(
        platform["id"],
        "Expiry Company",
        "First Owner",
        "expiry@example.test",
        invitation_ttl_seconds=10,
        now=101,
    )
    with pytest.raises(InvalidInvitation):
        store.activate_invitation(created["activation_token"], OWNER_PASSWORD, now=111)

    replacement = store.invite_member(
        platform["id"],
        created["organization"]["id"],
        "expiry@example.test",
        "First Owner",
        "owner",
        ttl_seconds=10,
        now=112,
    )
    accepted = store.activate_invitation(
        replacement["activation_token"], OWNER_PASSWORD, now=121
    )
    assert accepted["membership"]["role"] == "owner"
    with pytest.raises(InvalidInvitation):
        store.activate_invitation(replacement["activation_token"], OWNER_PASSWORD, now=121)
    with pytest.raises(InvalidInvitation):
        store.activate_invitation(created["activation_token"], OWNER_PASSWORD, now=105)


def test_create_organization_and_invitation_are_one_transaction(
    store: IdentityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = _bootstrap(store)

    def fail_invitation(*args, **kwargs):
        raise RuntimeError("simulated invitation failure")

    monkeypatch.setattr(store, "_insert_invitation", fail_invitation)
    with pytest.raises(RuntimeError, match="simulated"):
        store.create_organization(
            platform["id"], "Must Roll Back", "Owner", "owner@rollback.test", now=1_001
        )
    row = _row(store.path, "SELECT COUNT(*) AS n FROM organizations")
    assert row["n"] == 0


def test_session_expiry_csrf_logout_and_bootstrap_guard(store: IdentityStore) -> None:
    platform = _bootstrap(store, now=100)
    with pytest.raises(Conflict):
        _bootstrap(store, now=101)

    issued = store.login(
        platform["email"], ADMIN_PASSWORD, "127.0.0.1", "pytest", ttl_seconds=10, now=102
    )
    assert store.session(issued["session_token"], now=111)
    assert store.verify_csrf(issued["session_token"], issued["csrf_token"], now=111)
    assert not store.verify_csrf(issued["session_token"], "wrong", now=111)
    assert store.session(issued["session_token"], now=112) is None
    assert not store.verify_csrf(issued["session_token"], issued["csrf_token"], now=112)

    fresh = store.login(platform["email"], ADMIN_PASSWORD, ttl_seconds=10, now=120)
    assert store.logout(fresh["session_token"], now=121)
    assert not store.logout(fresh["session_token"], now=122)
    assert store.session(fresh["session_token"], now=121) is None


def test_permission_matrix_and_platform_admin_membership_actions(store: IdentityStore) -> None:
    platform, organization, owner_login = _organization_with_owner(store)
    owner = owner_login["user"]
    assert store.has_organization_permission(owner["id"], organization["id"], "member.invite")
    assert store.has_organization_permission(owner["id"], organization["id"], "member.manage")
    assert not store.has_organization_permission(owner["id"], organization["id"], "platform.user.manage")
    assert store.has_platform_permission(platform["id"], "platform.user.manage")
    assert not store.has_organization_permission(platform["id"], organization["id"], "project.read")

    tech_invite = store.invite_member(
        platform["id"],
        organization["id"],
        "tech@example.test",
        "Technologist",
        "technologist",
        now=1_003,
    )
    tech = store.activate_invitation(
        tech_invite["activation_token"], MEMBER_PASSWORD, now=1_004
    )
    assert store.has_organization_permission(
        tech["user"]["id"], organization["id"], "production.build"
    )
    assert store.has_organization_permission(
        tech["user"]["id"], organization["id"], "project.write"
    )
    with pytest.raises(PermissionDenied):
        store.invite_member(
            tech["user"]["id"],
            organization["id"],
            "blocked@example.test",
            "Blocked",
            "reviewer",
            now=1_005,
        )
    owner_invite = store.invite_member(
        owner["id"],
        organization["id"],
        "owner-can-add@example.test",
        "Owner Managed Reviewer",
        "reviewer",
        now=1_005,
    )
    assert owner_invite["invitation"]["role"] == "reviewer"
    with pytest.raises(PermissionDenied, match="не может назначать владельцев"):
        store.invite_member(
            owner["id"],
            organization["id"],
            "second-owner@example.test",
            "Second Owner",
            "owner",
            now=1_006,
        )


def test_platform_provisions_and_rotates_compact_employee_credentials(
    store: IdentityStore,
) -> None:
    platform = _bootstrap(store)
    created = store.provision_organization(
        platform["id"],
        "Managed Company",
        "Managed Owner",
        "managed-owner@example.test",
        now=1_001,
    )
    password = created["starter_password"]
    assert re.fullmatch(r"[A-Za-z0-9]{6}", password)
    assert any(char.islower() for char in password)
    assert any(char.isupper() for char in password)
    assert any(char.isdigit() for char in password)

    owner_login = store.login(created["login"], password, now=1_002)
    organization_id = created["organization"]["id"]
    assert owner_login["organization"]["id"] == organization_id

    member = store.provision_member(
        platform["id"],
        organization_id,
        "designer-managed@example.test",
        "First Name",
        "designer",
        now=1_003,
    )
    assert store.login(member["login"], member["starter_password"], now=1_004)
    updated = store.update_managed_member(
        platform["id"],
        organization_id,
        member["membership"]["id"],
        name="Updated Name",
        email="updated-managed@example.test",
        role="reviewer",
        status="active",
        now=1_005,
    )
    assert updated["display_name"] == "Updated Name"
    assert updated["email"] == "updated-managed@example.test"
    assert updated["role"] == "reviewer"

    rotated = store.reset_managed_member_password(
        platform["id"], organization_id, member["membership"]["id"], now=1_006
    )
    assert re.fullmatch(r"[A-Za-z0-9]{6}", rotated["starter_password"])
    with pytest.raises(AuthenticationFailed):
        store.login(member["login"], member["starter_password"], now=1_007)
    assert store.login(rotated["login"], rotated["starter_password"], now=1_008)


def test_owner_self_service_is_tenant_scoped_and_cannot_manage_owners(
    store: IdentityStore,
) -> None:
    platform, organization, owner_login = _organization_with_owner(store)
    owner = owner_login["user"]
    employee = store.provision_member(
        owner["id"],
        organization["id"],
        "owner-managed@example.test",
        "Owner Managed",
        "designer",
        now=1_003,
    )
    assert re.fullmatch(r"[A-Za-z0-9]{6}", employee["starter_password"])
    employee_login = store.login(
        employee["login"], employee["starter_password"], now=1_004
    )

    updated = store.update_managed_member(
        owner["id"],
        organization["id"],
        employee["membership"]["id"],
        name="Updated by Owner",
        email="owner-managed-updated@example.test",
        role="technologist",
        status="active",
        now=1_005,
    )
    assert updated["role"] == "technologist"
    assert store.session(employee_login["session_token"], now=1_005) is None

    rotated = store.reset_managed_member_password(
        owner["id"], organization["id"], employee["membership"]["id"], now=1_006
    )
    assert rotated["login"] == "owner-managed-updated@example.test"
    assert store.login(rotated["login"], rotated["starter_password"], now=1_007)

    owner_membership_id = owner_login["membership"]["id"]
    with pytest.raises(PermissionDenied, match="владельц"):
        store.update_managed_member(
            owner["id"],
            organization["id"],
            owner_membership_id,
            name=owner["display_name"],
            email=owner["email"],
            role="owner",
            status="disabled",
            now=1_008,
        )
    with pytest.raises(PermissionDenied, match="владельца"):
        store.reset_managed_member_password(
            owner["id"], organization["id"], owner_membership_id, now=1_009
        )
    with pytest.raises(PermissionDenied, match="не может назначать владельцев"):
        store.provision_member(
            owner["id"],
            organization["id"],
            "owner-escalation@example.test",
            "Owner Escalation",
            "owner",
            now=1_010,
        )

    other = store.provision_organization(
        platform["id"],
        "Other Company",
        "Other Owner",
        "other-owner@example.test",
        now=1_011,
    )
    with pytest.raises(PermissionDenied):
        store.provision_member(
            owner["id"],
            other["organization"]["id"],
            "foreign@example.test",
            "Foreign Employee",
            "reviewer",
            now=1_012,
        )

    audit = store.list_audit(owner["id"], organization["id"], now=1_013)
    actions = {event["action"] for event in audit}
    assert {"member.provisioned", "member.updated", "member.access_rotated"} <= actions
    assert employee["starter_password"].encode() not in store.path.read_bytes()
    assert rotated["starter_password"].encode() not in store.path.read_bytes()


def test_sole_owner_invariant_and_role_change_revokes_session(store: IdentityStore) -> None:
    platform, organization, first_owner = _organization_with_owner(store)
    first_membership = first_owner["membership"]
    with pytest.raises(SoleOwner):
        store.update_membership(
            platform["id"],
            organization["id"],
            first_membership["id"],
            status="disabled",
            now=1_003,
        )
    assert store.session(first_owner["session_token"], now=1_003)
    with pytest.raises(SoleOwner):
        store.update_user_status(
            platform["id"], first_owner["user"]["id"], "disabled", now=1_003
        )

    invitation = store.invite_member(
        platform["id"],
        organization["id"],
        "owner2@example.test",
        "Second Owner",
        "owner",
        now=1_004,
    )
    second_owner = store.activate_invitation(
        invitation["activation_token"], "second owner password 2026", now=1_005
    )
    updated = store.update_membership(
        platform["id"],
        organization["id"],
        first_membership["id"],
        role="admin",
        now=1_006,
    )
    assert updated["role"] == "admin"
    assert store.session(first_owner["session_token"], now=1_006) is None
    with pytest.raises(SoleOwner):
        store.update_membership(
            platform["id"],
            organization["id"],
            second_owner["membership"]["id"],
            role="admin",
            now=1_007,
        )


def test_disabled_membership_and_user_invalidate_sessions(store: IdentityStore) -> None:
    platform, organization, owner = _organization_with_owner(store)
    invitation = store.invite_member(
        platform["id"],
        organization["id"],
        "designer@example.test",
        "Designer",
        "designer",
        now=1_003,
    )
    designer = store.activate_invitation(
        invitation["activation_token"], MEMBER_PASSWORD, now=1_004
    )
    assert store.session(designer["session_token"], now=1_004)

    store.update_membership(
        platform["id"],
        organization["id"],
        designer["membership"]["id"],
        status="disabled",
        now=1_005,
    )
    assert store.session(designer["session_token"], now=1_005) is None
    with pytest.raises(PermissionDenied):
        store.login(
            designer["user"]["email"], MEMBER_PASSWORD, organization_id=organization["id"], now=1_006
        )

    store.update_membership(
        platform["id"],
        organization["id"],
        designer["membership"]["id"],
        status="active",
        now=1_007,
    )
    relogin = store.login(
        designer["user"]["email"], MEMBER_PASSWORD, organization_id=organization["id"], now=1_008
    )
    store.update_user_status(platform["id"], designer["user"]["id"], "disabled", now=1_009)
    assert store.session(relogin["session_token"], now=1_009) is None
    with pytest.raises(AuthenticationFailed):
        store.login(designer["user"]["email"], MEMBER_PASSWORD, now=1_010)


def test_existing_account_invitation_cannot_reset_password(store: IdentityStore) -> None:
    platform, first_org, owner = _organization_with_owner(store)
    second = store.create_organization(
        platform["id"],
        "Second Company",
        "Same Person",
        owner["user"]["email"],
        now=1_010,
    )
    with pytest.raises(AuthenticationFailed):
        store.activate_invitation(
            second["activation_token"], "attacker chosen password 2026", now=1_011
        )
    accepted = store.activate_invitation(
        second["activation_token"], OWNER_PASSWORD, now=1_012
    )
    assert accepted["user"]["id"] == owner["user"]["id"]
    assert accepted["organization"]["id"] != first_org["id"]


def test_support_is_read_only_expires_and_org_admin_can_end(store: IdentityStore) -> None:
    platform, organization, owner = _organization_with_owner(store, now=2_000)
    platform_login = store.login(
        platform["email"], ADMIN_PASSWORD, ttl_seconds=600, now=2_003
    )
    support = store.start_support(
        platform["id"],
        organization["id"],
        "Ticket AKD-900",
        1,
        primary_session_id=platform_login["session"]["id"],
        now=2_004,
    )
    permissions = store.organization_permissions(
        platform["id"],
        organization["id"],
        support_session_id=support["id"],
        primary_session_id=platform_login["session"]["id"],
        now=2_063,
    )
    assert "project.read" in permissions
    assert "project.write" not in permissions
    assert "member.manage" not in permissions
    with pytest.raises(Conflict, match="текущий сеанс"):
        store.start_support(
            platform["id"],
            organization["id"],
            "Ticket AKD-900 duplicate",
            1,
            primary_session_id=platform_login["session"]["id"],
            now=2_005,
        )
    assert not store.organization_permissions(
        platform["id"],
        organization["id"],
        support_session_id=support["id"],
        primary_session_id=platform_login["session"]["id"],
        now=2_064,
    )

    active = store.start_support(
        platform["id"],
        organization["id"],
        "Ticket AKD-901",
        10,
        primary_session_id=platform_login["session"]["id"],
        now=2_070,
    )
    assert store.end_support(owner["user"]["id"], active["id"], now=2_071)
    assert not store.organization_permissions(
        platform["id"],
        organization["id"],
        support_session_id=active["id"],
        primary_session_id=platform_login["session"]["id"],
        now=2_072,
    )


def test_company_view_needs_only_the_company_and_keeps_platform_identity(store: IdentityStore) -> None:
    platform, organization, _owner = _organization_with_owner(store, now=3_000)
    platform_login = store.login(platform["email"], ADMIN_PASSWORD, now=3_003)

    company_view = store.enter_company_view(
        platform["id"],
        organization["id"],
        primary_session_id=platform_login["session"]["id"],
        now=3_004,
    )

    assert company_view["platform_user_id"] == platform["id"]
    assert company_view["organization_id"] == organization["id"]
    assert company_view["reason"] == "Просмотр компании из админ-панели Akeda"
    assert company_view["scope"] == "read_only"


def test_audit_is_append_only_and_rejects_secret_metadata(store: IdentityStore) -> None:
    platform = _bootstrap(store)
    visible_events = store.list_audit(platform["id"])
    assert visible_events[0]["actor_name"] == "Platform Owner"
    assert visible_events[0]["actor_email"] == "platform@akeda.test"
    with pytest.raises(Exception):
        store.append_audit(
            "unsafe",
            actor_user_id=platform["id"],
            metadata={"session_token": "do-not-store"},
        )

    connection = sqlite3.connect(store.path)
    try:
        event_id = connection.execute("SELECT id FROM audit_events LIMIT 1").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE audit_events SET action='tampered' WHERE id=?", (event_id,)
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit_events WHERE id=?", (event_id,))
    finally:
        connection.close()
