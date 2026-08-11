"""Frozen public review links keep one tenant version read-only."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from argon2 import PasswordHasher, Type

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.admin import CSRF_COOKIE, SESSION_COOKIE
from src.identity import IdentityStore
from src.studio import _Studio, make_handler
from src.studio_reviews import ReviewNotFound, StudioReviewStore
from src.webviewer import SCENE_JS


class Browser:
    def __init__(self, port: int) -> None:
        self.port = port
        self.origin = f"http://127.0.0.1:{port}"
        self.cookies: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        csrf: bool = False,
    ) -> tuple[int, bytes]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method.upper() not in {"GET", "HEAD"}:
            headers["Origin"] = self.origin
        if csrf:
            headers["X-CSRF-Token"] = self.cookies[CSRF_COOKIE]
        if self.cookies:
            headers["Cookie"] = "; ".join(
                f"{key}={value}" for key, value in self.cookies.items()
            )
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw_headers = response.getheaders()
        result = response.status, response.read()
        connection.close()
        for key, value in raw_headers:
            if key.casefold() != "set-cookie":
                continue
            parsed = SimpleCookie()
            parsed.load(value)
            for name, morsel in parsed.items():
                if morsel["max-age"] == "0" or not morsel.value:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return result

    def request_bytes(
        self,
        method: str,
        path: str,
        body: bytes,
        *,
        content_type: str,
        filename: str = "",
    ) -> tuple[int, bytes, dict[str, str]]:
        headers = {"Content-Type": content_type, "Origin": self.origin}
        if filename:
            from urllib.parse import quote

            headers["X-Akeda-Filename"] = quote(filename, safe="")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_headers = {key.casefold(): value for key, value in response.getheaders()}
        result = response.status, response.read(), response_headers
        connection.close()
        return result

    def login(self, email: str, password: str) -> None:
        status, body = self.request(
            "POST", "/api/auth/login", {"email": email, "password": password}
        )
        assert status == 200, body
        assert SESSION_COOKIE in self.cookies and CSRF_COOKIE in self.cookies


def test_review_store_expires_link_but_keeps_management_record(tmp_path: Path) -> None:
    reviews = StudioReviewStore(tmp_path / "reviews")
    spec = {
        "schemaVersion": "paramspec-v1",
        "project_name": "Версия со сроком",
    }
    token, created = reviews.create(
        spec,
        organization_id="org-1",
        organization_name="Константа",
        project_file="product.json",
        actor_user_id="user-1",
        actor_name="Алексей",
        expires_in_days=7,
    )
    path = reviews.root / f"{reviews.token_hash(token)}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["expires_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ReviewNotFound):
        reviews.load(token)
    managed = reviews.list_for_project(
        organization_id="org-1", project_file="product.json"
    )
    assert managed[0]["id"] == created["id"]
    assert managed[0]["access_status"] == "expired"


def test_review_link_freezes_version_and_accepts_public_decision(tmp_path: Path) -> None:
    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    )
    identity_path = tmp_path / "identity.sqlite3"
    store = IdentityStore(identity_path, password_hasher=hasher)
    store.migrate()
    platform = store.bootstrap_platform_admin(
        "platform@akeda.test", "Platform", "platform password long enough"
    )
    company = store.provision_organization(
        platform["id"], "Константа", "Алексей Лазарев", "alexey@constanta.test"
    )
    other_company = store.provision_organization(
        platform["id"], "Другая компания", "Другой сотрудник", "other@company.test"
    )

    source = json.loads(
        (ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8")
    )
    source["project_name"] = "Исходный шкаф"
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_path = legacy_dir / "legacy.json"
    legacy_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    tenant_dir = (
        tmp_path / "tenants" / company["organization"]["id"] / "paramspecs"
    )
    tenant_dir.mkdir(parents=True)
    product_path = tenant_dir / "product.json"
    product_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    other_tenant_dir = (
        tmp_path / "tenants" / other_company["organization"]["id"] / "paramspecs"
    )
    other_tenant_dir.mkdir(parents=True)
    (other_tenant_dir / "product.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8"
    )

    studio = _Studio(
        legacy_path,
        tmp_path / "out",
        identity_db=identity_path,
        tenant_root=tmp_path / "tenants",
        require_auth=True,
    )
    studio.identity_store = store
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        editor = Browser(port)
        editor.login(company["login"], company["starter_password"])
        incoming = json.loads(json.dumps(source))
        incoming["project_name"] = "Шкаф — версия для клиента"
        incoming["dimensions"]["width"] += 13

        assert editor.request(
            "POST", "/api/reviews/create", {"file": "product.json", "spec": incoming}
        )[0] == 403
        status, create_body = editor.request(
            "POST",
            "/api/reviews/create",
            {"file": "product.json", "spec": incoming},
            csrf=True,
        )
        assert status == 200, create_body
        created = json.loads(create_body)
        review_path = urlsplit(created["url"]).path
        token = review_path.rsplit("/", 1)[-1]
        revision = created["review"]["revision"]
        assert len(token) >= 32 and len(revision) == 64
        assert created["review"]["link_mode"] == "snapshot"
        assert created["review"]["expires_at"] is None

        stored_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (tmp_path / "tenants" / "_review_links").glob("*.json")
        )
        assert token not in stored_text
        assert revision in stored_text

        changed = json.loads(json.dumps(source))
        changed["project_name"] = "Шкаф уже изменён после отправки"
        changed["dimensions"]["width"] = 2999
        product_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")

        client = Browser(port)
        status, review_body = client.request("GET", review_path)
        html = review_body.decode("utf-8")
        assert status == 200
        assert "Шкаф — версия для клиента" in html
        assert "Константа" in html
        assert "Шкаф уже изменён после отправки" not in html
        assert "2999" not in html
        assert SCENE_JS in html
        assert "Согласовать" in html and "Нужны изменения" in html
        assert "viewport-fit=cover" in html and 'class="mobile-dock"' in html
        assert 'id="reviewSheet"' in html and 'id="layers" class="stage-tools"' in html
        assert 'data-layer="holes"' in html and 'aria-pressed="true"' in html
        assert "Фиксированная версия" in html
        assert "4 / 5" not in html and "layersToggle" not in html
        assert "Прикрепить файлы" in html and "formatEdges" in html
        assert "Скачать PDF" in html and 'id="mobilePdf"' in html
        assert "downloadPresentationPdf" in html and "presentationPdf" in html
        assert "РАЗОБРАННЫЙ ВИД И ОСНОВНЫЕ ХАРАКТЕРИСТИКИ" in html
        assert "REVIEW.revision" in html and "производственных данных" in html
        assert "chatMsg" not in html and "btnSave" not in html and "btnB3d" not in html
        assert "paramspec-v1" not in html

        png = b"\x89PNG\r\n\x1a\n" + b"client note"
        status, upload_body, _ = client.request_bytes(
            "POST",
            review_path + "/attachments",
            png,
            content_type="image/png",
            filename="Эскиз правки.png",
        )
        assert status == 200, upload_body
        attachment = json.loads(upload_body)["attachment"]
        assert attachment["name"] == "Эскиз правки.png"
        assert attachment["content_type"] == "image/png"
        assert client.request(
            "GET", review_path + "/attachments/" + attachment["id"]
        )[0] == 404

        status, rejected_body, _ = client.request_bytes(
            "POST",
            review_path + "/attachments",
            b"<svg><script>alert(1)</script></svg>",
            content_type="image/svg+xml",
            filename="unsafe.svg",
        )
        assert status == 400
        assert "JPG" in json.loads(rejected_body)["error"]

        assert client.request(
            "POST",
            review_path + "/decision",
            {"decision": "approved", "reviewer_name": ""},
        )[0] == 400
        status, decision_body = client.request(
            "POST",
            review_path + "/decision",
            {
                "decision": "approved",
                "reviewer_name": "Мария, дизайнер клиента",
                "comment": "Внешний вид согласован",
                "attachment_ids": [attachment["id"]],
            },
        )
        assert status == 200, decision_body
        decision = json.loads(decision_body)
        assert decision["status"] == "approved"
        assert decision["revision"] == revision
        assert decision["decision"]["revision"] == revision
        assert decision["decision"]["attachments"][0]["name"] == "Эскиз правки.png"

        status, attachment_body = client.request(
            "GET", review_path + "/attachments/" + attachment["id"]
        )
        assert status == 200 and attachment_body == png

        status, inbox_body = editor.request("POST", "/api/reviews", {}, csrf=True)
        assert status == 200, inbox_body
        inbox = json.loads(inbox_body)
        assert inbox["project_file"] == "product.json"
        assert inbox["reviews"][0]["status"] == "approved"
        assert inbox["reviews"][0]["responsible_name"] == "Алексей Лазарев"
        assert inbox["reviews"][0]["decision"]["attachments"][0]["id"] == attachment["id"]
        status, private_attachment = editor.request(
            "GET",
            f'/api/reviews/attachments/{created["review"]["id"]}/{attachment["id"]}',
        )
        assert status == 200 and private_attachment == png

        outsider = Browser(port)
        outsider.login(other_company["login"], other_company["starter_password"])
        status, outsider_inbox = outsider.request("POST", "/api/reviews", {}, csrf=True)
        assert status == 200 and json.loads(outsider_inbox)["reviews"] == []
        assert outsider.request(
            "GET",
            f'/api/reviews/attachments/{created["review"]["id"]}/{attachment["id"]}',
        )[0] == 404

        status, approved_body = client.request("GET", review_path)
        approved_html = approved_body.decode("utf-8")
        assert status == 200
        assert '"status": "approved"' in approved_html
        assert "Мария, дизайнер клиента" in approved_html

        unsaved = json.loads(json.dumps(changed))
        unsaved["dimensions"]["width"] += 1
        status, unsaved_body = editor.request(
            "POST",
            "/api/reviews/create",
            {
                "file": "product.json",
                "spec": unsaved,
                "link_mode": "live",
                "expires_in_days": 7,
            },
            csrf=True,
        )
        assert status == 409
        assert json.loads(unsaved_body)["code"] == "save_required"

        status, live_create_body = editor.request(
            "POST",
            "/api/reviews/create",
            {
                "file": "product.json",
                "spec": changed,
                "link_mode": "live",
                "expires_in_days": 7,
            },
            csrf=True,
        )
        assert status == 200, live_create_body
        live_created = json.loads(live_create_body)
        live_path = urlsplit(live_created["url"]).path
        live_revision = live_created["review"]["revision"]
        assert live_created["review"]["link_mode"] == "live"
        assert live_created["review"]["expires_at"]

        status, live_body = client.request("GET", live_path)
        live_html = live_body.decode("utf-8")
        assert status == 200
        assert "Шкаф уже изменён после отправки" in live_html
        assert "2999" in live_html
        assert "Обновляемый просмотр" in live_html

        latest = json.loads(json.dumps(changed))
        latest["project_name"] = "Шкаф — актуальная сохранённая версия"
        latest["dimensions"]["width"] = 3011
        product_path.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
        status, latest_body = client.request("GET", live_path)
        latest_html = latest_body.decode("utf-8")
        assert status == 200
        assert "Шкаф — актуальная сохранённая версия" in latest_html
        assert "3011" in latest_html
        assert live_revision not in latest_html

        status, stale_body = client.request(
            "POST",
            live_path + "/decision",
            {
                "decision": "approved",
                "reviewer_name": "Мария",
                "revision": live_revision,
            },
        )
        assert status == 409
        assert json.loads(stale_body)["code"] == "review_revision_changed"

        import hashlib

        latest_revision = hashlib.sha256(
            json.dumps(
                latest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        status, live_decision_body = client.request(
            "POST",
            live_path + "/decision",
            {
                "decision": "approved",
                "reviewer_name": "Мария",
                "revision": latest_revision,
            },
        )
        assert status == 200, live_decision_body
        assert json.loads(live_decision_body)["revision"] == latest_revision

        status, revoke_body = editor.request(
            "POST",
            "/api/reviews/revoke",
            {"review_id": live_created["review"]["id"]},
            csrf=True,
        )
        assert status == 200, revoke_body
        assert client.request("GET", live_path)[0] == 404
        status, managed_body = editor.request("POST", "/api/reviews", {}, csrf=True)
        assert status == 200
        managed = json.loads(managed_body)["reviews"]
        revoked = next(
            item for item in managed if item["id"] == live_created["review"]["id"]
        )
        assert revoked["access_status"] == "revoked"
        assert revoked["link_mode"] == "live"
        assert revoked["decision"]["revision"] == latest_revision
        assert client.request("GET", "/review/not-a-real-token")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
