"""Offline HTTP rollback drill for MEB-158 (no live provider or production writes)."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from argon2 import PasswordHasher, Type

from src.admin import CSRF_COOKIE, SESSION_COOKIE
from src.identity import IdentityStore
from src.rollout import COMPONENTS
from src.studio import _Studio, make_handler
import src.spec_chat as spec_chat


ROOT = Path(__file__).resolve().parents[1]


class _Browser:
    def __init__(self, port: int) -> None:
        self.port = port
        self.origin = f"http://127.0.0.1:{port}"
        self.cookies: dict[str, str] = {}

    def post(self, path: str, payload: dict[str, Any], *, csrf: bool = False) -> dict:
        headers = {"Content-Type": "application/json", "Origin": self.origin}
        if self.cookies:
            headers["Cookie"] = "; ".join(
                f"{name}={value}" for name, value in self.cookies.items()
            )
        if csrf:
            headers["X-CSRF-Token"] = self.cookies[CSRF_COOKIE]
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request(
            "POST", path, body=json.dumps(payload).encode("utf-8"), headers=headers
        )
        response = connection.getresponse()
        raw_headers = response.getheaders()
        body = response.read()
        connection.close()
        for key, value in raw_headers:
            if key.casefold() == "set-cookie":
                parsed = SimpleCookie()
                parsed.load(value)
                for name, morsel in parsed.items():
                    if morsel.value:
                        self.cookies[name] = morsel.value
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} {path}: {body[:300]!r}")
        return json.loads(body)


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    before = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def _server(studio: _Studio) -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _file_snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    }


def _rollout_env(mode: str) -> dict[str, str]:
    values = {
        f"AKEDA_ROLLOUT_{component.upper()}": mode for component in COMPONENTS
    }
    values.update({
        "SPEC_CHAT_PROVIDER": "mock",
        "AKEDA_TELEMETRY_BACKEND": "none",
        "AKEDA_CANARY_PERCENT": "100" if mode == "canary" else "0",
    })
    return values


def run_drill() -> dict:
    with tempfile.TemporaryDirectory(prefix="akeda-rollout-drill-") as raw_root:
        root = Path(raw_root)
        source = json.loads(
            (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(
                encoding="utf-8"
            )
        )
        legacy_path = root / "legacy" / "product.json"
        legacy_path.parent.mkdir()
        legacy_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

        hasher = PasswordHasher(
            time_cost=1, memory_cost=1024, parallelism=1, hash_len=16,
            salt_len=8, type=Type.ID,
        )
        identity_path = root / "identity.sqlite3"
        identities = IdentityStore(identity_path, password_hasher=hasher)
        identities.migrate()
        platform = identities.bootstrap_platform_admin(
            "platform@drill.invalid", "Offline Drill", "offline platform password"
        )
        provisioned = identities.provision_organization(
            platform["id"], "Offline Tenant", "Drill Owner", "owner@drill.invalid"
        )
        tenant_root = root / "tenants"
        tenant_spec_root = (
            tenant_root / provisioned["organization"]["id"] / "paramspecs"
        )
        tenant_spec_root.mkdir(parents=True)
        (tenant_spec_root / "product.json").write_text(
            json.dumps(source, ensure_ascii=False), encoding="utf-8"
        )
        production_before = _file_snapshot(tenant_spec_root)

        counters = {"provider_calls": 0, "offline_provider_calls": 0}
        real_factory = spec_chat.get_chat_provider

        def audited_provider(name: str | None = None) -> Any:
            resolved = spec_chat.resolve_provider_name(name)
            if resolved != "mock":
                counters["provider_calls"] += 1
                raise RuntimeError(f"live provider forbidden in offline drill: {resolved}")
            provider = real_factory("mock")
            original_chat = provider.chat

            def counted_chat(*args: Any, **kwargs: Any) -> Any:
                counters["offline_provider_calls"] += 1
                return original_chat(*args, **kwargs)

            provider.chat = counted_chat
            return provider

        request = {
            "spec": source,
            "message": "сделай ширину 410",
            "history": [],
            "provider": "mock",
        }
        with patch("src.spec_chat.get_chat_provider", side_effect=audited_provider):
            with _environment(_rollout_env("on") | {"AKEDA_ROLLOUT_LANGGRAPH": "shadow"}):
                shadow_studio = _Studio(
                    legacy_path, root / "shadow-out", identity_db=identity_path,
                    tenant_root=tenant_root, require_auth=True,
                )
                shadow_studio.identity_store = identities
                with _server(shadow_studio) as port:
                    browser = _Browser(port)
                    browser.post("/api/auth/login", {
                        "email": provisioned["login"],
                        "password": provisioned["starter_password"],
                    })
                    if (
                        SESSION_COOKIE not in browser.cookies
                        or CSRF_COOKIE not in browser.cookies
                    ):
                        raise RuntimeError("real session adapter did not issue session/CSRF")
                    shadow = browser.post("/api/chat", request, csrf=True)

            with _environment(_rollout_env("on")):
                fallback_studio = _Studio(
                    legacy_path, root / "fallback-out", identity_db=identity_path,
                    tenant_root=tenant_root, require_auth=True,
                )
                fallback_studio.identity_store = identities
                if fallback_studio.ai_graph is None:
                    raise RuntimeError("candidate graph was not routed for fallback drill")
                fallback_studio.ai_graph.checkpoint_retention.max_bytes = 1
                with _server(fallback_studio) as port:
                    browser = _Browser(port)
                    browser.post("/api/auth/login", {
                        "email": provisioned["login"],
                        "password": provisioned["starter_password"],
                    })
                    fallback = browser.post("/api/chat", request, csrf=True)
                fallback_studio.ai_graph._sqlite_connection.close()

        production_after = _file_snapshot(tenant_spec_root)
        production_writes = sum(
            production_before.get(path) != production_after.get(path)
            for path in set(production_before) | set(production_after)
        )
        comparison = shadow.get("rollout", {}).get("shadow_comparison") or {}
        if shadow.get("rollout", {}).get("primary") != "legacy":
            raise RuntimeError("shadow changed the HTTP primary route")
        if not comparison.get("equal"):
            raise RuntimeError(f"independent legacy/candidate comparison failed: {comparison}")
        if fallback.get("rollout", {}).get("primary") != "legacy":
            raise RuntimeError("storage failure did not report actual legacy fallback")
        if not fallback.get("rollout", {}).get("checkpoint_degraded"):
            raise RuntimeError("storage failure did not report degraded checkpoint state")
        if counters["provider_calls"] != 0 or production_writes != 0:
            raise RuntimeError(
                f"offline safety invariant failed: counters={counters}, "
                f"production_writes={production_writes}"
            )

        return {
            "ok": True,
            "environment": "offline-http",
            "provider_calls": counters["provider_calls"],
            "offline_provider_calls": counters["offline_provider_calls"],
            "production_writes": production_writes,
            "session": {"authenticated": True, "csrf_enforced": True},
            "shadow": {
                "primary": shadow["rollout"]["primary"],
                "candidate": "langgraph",
                "comparison": comparison,
            },
            "checkpoint_storage": {
                "degraded": fallback["rollout"]["checkpoint_degraded"],
                "reported_primary": fallback["rollout"]["primary"],
                "fallback": "legacy",
                "persistent_stop": fallback["rollout"]["slo"]["stopped"],
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_drill()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
