"""Ephemeral public-demo repository: isolation, reset and expiry guarantees."""

from __future__ import annotations

import builtins
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.demo_sandbox import (  # noqa: E402
    DemoCapacityExceeded,
    DemoProjectRepository,
    DemoRevisionConflict,
    DemoSandbox,
    DemoTemplate,
    ExpiredDemoSession,
    InvalidDemoData,
    UnknownDemoProject,
    UnknownDemoSession,
)


class FakeClock:
    def __init__(self, value: float = 1_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _spec(name: str, width: int = 800) -> dict:
    return {
        "schemaVersion": "paramspec-v1",
        "project_name": name,
        "archetype": "cabinet",
        "dimensions": {"width": width, "depth": 400, "height": 720},
        "sections": [{"kind": "shelves", "shelves": 2}],
    }


@pytest.fixture
def template() -> DemoTemplate:
    return DemoTemplate(
        {
            "cabinet-demo": _spec("Демо-тумба"),
            "desk-demo": _spec("Демо-стол", 1200),
        },
        chat_history={
            "cabinet-demo": [
                {"role": "user", "text": "Сделай тумбу шире"},
                {"role": "assistant", "text": "Ширина изменена до 800 мм"},
            ]
        },
        metadata={"company": "Akeda Demo", "reset_policy": "per-session"},
        template_id="akeda-public-demo-v1",
    )


def test_template_is_an_immutable_detached_snapshot() -> None:
    original = {"cabinet-demo": _spec("Образец")}
    chat = {"cabinet-demo": [{"role": "user", "text": "Покажи образец"}]}
    template = DemoTemplate(original, chat_history=chat)
    first_digest = template.digest

    # Neither constructor inputs nor detached outputs retain a reference to the
    # canonical baseline.
    original["cabinet-demo"]["dimensions"]["width"] = 9_999
    chat["cabinet-demo"][0]["text"] = "испорчено"
    detached = template.snapshot()
    detached["projects"]["cabinet-demo"]["dimensions"]["width"] = 1
    detached["chat_history"]["cabinet-demo"][0]["text"] = "тоже испорчено"

    fresh = template.snapshot()
    assert fresh["projects"]["cabinet-demo"]["dimensions"]["width"] == 800
    assert fresh["chat_history"]["cabinet-demo"][0]["text"] == "Покажи образец"
    assert template.digest == first_digest

    # Digest is deterministic and does not depend on mapping insertion order.
    reordered = DemoTemplate({"cabinet-demo": _spec("Образец")}, chat_history={
        "cabinet-demo": [{"text": "Покажи образец", "role": "user"}]
    })
    assert reordered.digest == first_digest


def test_sessions_isolate_project_overlays_and_chat_history(
    template: DemoTemplate,
) -> None:
    sandbox = DemoSandbox(template)
    alice = sandbox.start_session(principal_id="browser-alice")
    bob = sandbox.start_session(principal_id="browser-bob")
    assert isinstance(alice, DemoProjectRepository)

    alice_spec = alice.read_project("cabinet-demo").spec
    alice_spec["dimensions"]["width"] = 1_100
    saved = alice.save_project("cabinet-demo", alice_spec, expected_revision=0)
    alice.save_project("alice-draft", _spec("Черновик Alice"), expected_revision=0)
    alice.append_chat_message(
        "cabinet-demo", "user", "Добавь дверь", metadata={"source": "demo-ui"}
    )
    alice.append_chat_message("cabinet-demo", "assistant", "Дверь добавлена")

    assert saved.revision == 1 and saved.source == "session"
    assert alice.read_project("cabinet-demo").spec["dimensions"]["width"] == 1_100
    assert len(alice.chat_messages("cabinet-demo")) == 4
    assert "alice-draft" in {item.project_id for item in alice.list_projects()}

    # A second browser with the same public account sees only the pristine demo.
    assert bob.read_project("cabinet-demo").revision == 0
    assert bob.read_project("cabinet-demo").spec["dimensions"]["width"] == 800
    assert len(bob.chat_messages("cabinet-demo")) == 2
    with pytest.raises(UnknownDemoProject):
        bob.read_project("alice-draft")

    # The immutable company template was not changed either.
    assert template.snapshot()["projects"]["cabinet-demo"]["dimensions"]["width"] == 800
    assert len(template.snapshot()["chat_history"]["cabinet-demo"]) == 2


def test_new_login_starts_clean_and_reset_discards_all_session_changes(
    template: DemoTemplate,
) -> None:
    sandbox = DemoSandbox(template)
    first = sandbox.start_session(principal_id="shared-demo-account")
    changed = first.read_project("cabinet-demo").spec
    changed["project_name"] = "Изменено первым посетителем"
    first.save_project("cabinet-demo", changed)
    first.clear_chat("cabinet-demo")
    first.delete_project("desk-demo")

    # An explicit in-session reset is available for the UI's "Start over".
    first.reset()
    assert first.read_project("cabinet-demo").spec["project_name"] == "Демо-тумба"
    assert len(first.chat_messages("cabinet-demo")) == 2
    assert first.read_project("desk-demo").spec["project_name"] == "Демо-стол"

    changed = first.read_project("cabinet-demo").spec
    changed["project_name"] = "Ещё одна правка"
    first.save_project("cabinet-demo", changed)
    assert first.close()

    second = sandbox.start_session(principal_id="shared-demo-account")
    assert second.session_id != first.session_id
    assert second.read_project("cabinet-demo").spec["project_name"] == "Демо-тумба"
    with pytest.raises(UnknownDemoSession):
        first.read_project("cabinet-demo")


def test_optimistic_revisions_delete_recreate_and_template_restore(
    template: DemoTemplate,
) -> None:
    session = DemoSandbox(template).start_session()
    edited = session.read_project("cabinet-demo").spec
    edited["dimensions"]["width"] = 900
    revision_one = session.save_project(
        "cabinet-demo", edited, expected_revision=0
    )
    assert revision_one.revision == 1

    with pytest.raises(DemoRevisionConflict) as conflict:
        session.save_project("cabinet-demo", edited, expected_revision=0)
    assert (conflict.value.expected, conflict.value.current) == (0, 1)

    assert session.delete_project("cabinet-demo", expected_revision=1) == 2
    with pytest.raises(UnknownDemoProject):
        session.read_project("cabinet-demo")
    recreated = session.save_project(
        "cabinet-demo", _spec("Пересоздано"), expected_revision=2
    )
    assert recreated.revision == 3
    with pytest.raises(DemoRevisionConflict):
        session.reset_project("cabinet-demo", expected_revision=2)
    restored = session.reset_project("cabinet-demo", expected_revision=3)
    assert restored and restored.revision == 4 and restored.source == "session"
    assert restored.spec["project_name"] == "Демо-тумба"
    # Reset restores baseline content without resetting the concurrency token
    # to zero: a tab that loaded the original baseline cannot overwrite it.
    with pytest.raises(DemoRevisionConflict) as after_reset:
        session.save_project("cabinet-demo", _spec("Устарело"), expected_revision=0)
    assert after_reset.value.current == 4

    session.save_project("temporary", _spec("Временно"))
    assert session.reset_project("temporary", expected_revision=1) is None
    with pytest.raises(UnknownDemoProject):
        session.read_project("temporary")


def test_absolute_ttl_lazy_expiry_cleanup_and_capacity(template: DemoTemplate) -> None:
    clock = FakeClock()
    ids = iter(("session-one", "session-two", "session-three"))
    sandbox = DemoSandbox(
        template,
        ttl_seconds=10,
        max_sessions=1,
        clock=clock,
        session_id_factory=lambda: next(ids),
    )
    first = sandbox.start_session()
    clock.advance(9.999)
    assert first.read_project("cabinet-demo").project_id == "cabinet-demo"
    with pytest.raises(DemoCapacityExceeded):
        sandbox.start_session()

    # Absolute TTL is not extended by the read above.
    clock.advance(0.001)
    with pytest.raises(ExpiredDemoSession):
        first.read_project("cabinet-demo")
    assert sandbox.active_session_count == 0

    second = sandbox.start_session()
    clock.advance(10)
    assert sandbox.cleanup_expired() == 1
    with pytest.raises(UnknownDemoSession):
        sandbox.resume_session(second.session_id)


def test_chat_values_are_detached_and_clear_is_session_local(
    template: DemoTemplate,
) -> None:
    sandbox = DemoSandbox(template, message_id_factory=lambda: "message-fixed")
    session = sandbox.start_session()
    metadata = {"change": {"width": 900}}
    created = session.append_chat_message(
        "cabinet-demo", "assistant", "Готово", metadata=metadata
    )
    metadata["change"]["width"] = 1
    created["metadata"]["change"]["width"] = 2
    history = session.chat_messages("cabinet-demo")
    history[-1]["metadata"]["change"]["width"] = 3
    assert session.chat_messages("cabinet-demo")[-1]["metadata"]["change"]["width"] == 900

    session.clear_chat("cabinet-demo")
    assert session.chat_messages("cabinet-demo") == []
    other = sandbox.start_session()
    assert len(other.chat_messages("cabinet-demo")) == 2


def test_thread_safe_chat_append_has_no_lost_or_duplicate_messages(
    template: DemoTemplate,
) -> None:
    sandbox = DemoSandbox(template)
    session = sandbox.start_session()

    def append(index: int) -> str:
        message = session.append_chat_message(
            "desk-demo", "user", f"concurrent message {index}"
        )
        return message["id"]

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(append, range(120)))

    history = session.chat_messages("desk-demo")
    assert len(history) == 120
    assert len(ids) == len(set(ids)) == 120
    assert [item["sequence"] for item in history] == list(range(1, 121))
    assert {item["text"] for item in history} == {
        f"concurrent message {index}" for index in range(120)
    }


def test_invalid_data_limits_and_cross_project_negative_cases() -> None:
    with pytest.raises(InvalidDemoData):
        DemoTemplate({})
    with pytest.raises(InvalidDemoData):
        DemoTemplate({"../production": _spec("Нельзя")})
    with pytest.raises(InvalidDemoData):
        DemoTemplate({"ok": {"bad": float("nan")}})
    with pytest.raises(InvalidDemoData):
        DemoTemplate({"ok": _spec("Ок")}, chat_history=[])
    with pytest.raises(InvalidDemoData):
        DemoTemplate(
            {"ok": _spec("Ок")},
            chat_history={"missing": [{"role": "user", "text": "Ошибка"}]},
        )

    sandbox = DemoSandbox(
        {"only": _spec("Один")},
        max_projects_per_session=1,
        max_project_bytes=512,
        max_chat_message_bytes=10,
        max_chat_messages_per_project=1,
    )
    session = sandbox.start_session()
    with pytest.raises(UnknownDemoProject):
        session.read_project("missing")
    with pytest.raises(InvalidDemoData):
        session.read_project("../../paramspecs/secret")
    with pytest.raises(DemoCapacityExceeded):
        session.save_project("second", _spec("Два"))
    with pytest.raises(DemoCapacityExceeded):
        session.save_project("only", {"blob": "x" * 1_000})
    with pytest.raises(InvalidDemoData):
        session.append_chat_message("only", "admin", "no")
    with pytest.raises(DemoCapacityExceeded):
        session.append_chat_message("only", "user", "01234567890")
    session.append_chat_message("only", "user", "first")
    with pytest.raises(DemoCapacityExceeded):
        session.append_chat_message("only", "assistant", "second")


def test_template_is_pinned_and_reset_cannot_bypass_project_capacity() -> None:
    sandbox = DemoSandbox(
        {"baseline": _spec("Эталон")}, max_projects_per_session=1
    )
    with pytest.raises(AttributeError):
        sandbox.template = DemoTemplate({"replacement": _spec("Подмена")})

    session = sandbox.start_session()
    assert session.delete_project("baseline", expected_revision=0) == 1
    session.save_project("temporary", _spec("Временно"), expected_revision=0)
    with pytest.raises(DemoCapacityExceeded):
        session.reset_project("baseline", expected_revision=1)
    assert {item.project_id for item in session.list_projects()} == {"temporary"}


def test_runtime_has_no_filesystem_write_path(
    template: DemoTemplate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving/AI history succeeds even when every builtin file open is forbidden."""

    sandbox = DemoSandbox(template)
    session = sandbox.start_session()
    edited = json.loads(json.dumps(session.read_project("cabinet-demo").spec))
    edited["dimensions"]["width"] = 950

    def forbidden_open(*args, **kwargs):
        raise AssertionError("demo sandbox attempted filesystem access")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    session.save_project("cabinet-demo", edited)
    session.append_chat_message("cabinet-demo", "user", "Сохрани только здесь")
    assert session.snapshot()["projects"]["cabinet-demo"]["dimensions"]["width"] == 950


def test_real_paramspec_is_usable_without_mutating_its_source_file() -> None:
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    before = source.read_bytes()
    sandbox = DemoSandbox({"showcase-tumba": json.loads(before.decode("utf-8"))})
    session = sandbox.start_session()
    edited = session.read_project("showcase-tumba").spec
    edited["dimensions"]["width"] += 100
    session.save_project("showcase-tumba", edited, expected_revision=0)

    assert session.read_project("showcase-tumba").spec["dimensions"]["width"] == 500
    assert source.read_bytes() == before
