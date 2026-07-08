"""Чат-правка ParamSpec словами (AKD-107/109/110).

Пользователь пишет в Studio «сделай глубину 600», «замени цвет на дуб вотан»,
«добавь ножки 100 мм» — провайдер правит ParamSpec, конвейер пересобирает модель.
LLM меняет ТОЛЬКО ParamSpec (высокоуровневые параметры); координаты по-прежнему
считает детерминированный генератор (rules/core.md).

Провайдер: env SPEC_CHAT_PROVIDER (fallback PARAMSPEC_PROVIDER): mock|openai.
mock — rule-based разбор типовых русских команд, без сети (тесты/CI/офлайн).

Безопасность применения (AKD-110): новая спека валидируется схемой ДО отдачи,
schemaVersion/furniture_type не затираются, сводка изменений — spec_diff().
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CHAT_PROMPT_PATH = ROOT / "prompts" / "spec_chat_prompt.txt"
PARAMSPEC_SCHEMA_PATH = ROOT / "schema" / "paramspec.schema.json"

# Поля, которые чату менять нельзя (структура/происхождение спеки)
PROTECTED_KEYS = ("schemaVersion",)


# ------------------------------------------------------------------ diff

def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(_flatten(v, f"{prefix}{i}."))
    else:
        out[prefix[:-1]] = d
    return out


def spec_diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Человекочитаемая сводка изменений: «поле: старое → новое»."""
    fo, fn = _flatten(old), _flatten(new)
    lines = []
    for k in sorted(set(fo) | set(fn)):
        if fo.get(k) == fn.get(k):
            continue
        if k not in fn:
            lines.append(f"{k}: {fo[k]!r} → (удалено)")
        elif k not in fo:
            lines.append(f"{k}: (нет) → {fn[k]!r}")
        else:
            lines.append(f"{k}: {fo[k]!r} → {fn[k]!r}")
    return lines


# ------------------------------------------------------------------ mock

_NUM = r"(\d+(?:[.,]\d+)?)"


def _num(s: str) -> float:
    v = float(s.replace(",", "."))
    return int(v) if v == int(v) else v


_ARCHETYPE_WORDS = [
    ("тумб", "drawer_unit"), ("шкаф", "cabinet"), ("гардероб", "wardrobe"),
    ("стеллаж", "shelving"), ("полк", "shelving"),
    ("стол", "desk"), ("стойк", "corpus"),
]
_CREATE_WORDS = ("сделай", "создай", "нов", "спроектируй", "построй")


def _try_create(msg: str) -> dict[str, Any] | None:
    """«Сделай тумбу 600×450×550 с 3 ящиками» → новая спека с нуля (D3)."""
    if not any(w in msg for w in _CREATE_WORDS):
        return None
    arch = next((a for w, a in _ARCHETYPE_WORDS if w in msg), None)
    if arch is None:
        return None
    m = re.search(r"(\d{2,4})\s*[x×х*]\s*(\d{2,4})\s*[x×х*]\s*(\d{2,4})", msg)
    # создание — только при явных габаритах или «нов…»/«с нуля»
    # (иначе «сделай стол на металлокаркасе» — это ПРАВКА текущего)
    if m is None and "нов" not in msg and "с нуля" not in msg:
        return None
    dims = tuple(int(v) for v in m.groups()) if m else \
        {"desk": (1200, 700, 750), "wardrobe": (1200, 600, 2100)}.get(arch, (800, 450, 800))
    n_drawers = re.search(r"(\d+|двумя|тремя|четырьмя)\s*ящик", msg)
    n_shelves = re.search(r"(\d+|двумя|тремя|четырьмя)\s*полк", msg)
    n_doors = re.search(r"(\d+|одной|двумя)\s*двер", msg) or ("двер" in msg)
    words = {"одной": 1, "двумя": 2, "тремя": 3, "четырьмя": 4}

    def _n(mm, default):
        if not mm or mm is True:
            return default
        v = mm.group(1)
        return words.get(v) or int(v)

    spec: dict[str, Any] = {
        "schemaVersion": "paramspec-v1",
        "project_name": f"Изделие из чата {dims[0]}×{dims[1]}×{dims[2]}",
        "furniture_type": arch, "archetype": arch,
        "dimensions": {"width": dims[0], "depth": dims[1], "height": dims[2],
                       "tolerance": 5},
        "materials": {"board_thickness": 25 if arch == "desk" else 16,
                      "board_material": "ЛДСП", "edge_band_thickness": 2,
                      "color": "Белый", "color_code": ""},
        "legs": {"type": "нет", "height": 0},
        "warnings": ["Создано из чата — параметры уточнить"],
        "estimated_values": [],
    }
    if arch == "desk":
        spec["apron"] = True
    elif arch == "drawer_unit" and (n_drawers or "ящик" in msg):
        spec["sections"] = [{"kind": "drawers", "drawers": _n(n_drawers, 3)}]
    elif arch in ("cabinet", "wardrobe", "shelving", "drawer_unit", "door_unit"):
        sec: dict[str, Any] = {"kind": "shelves", "shelves": _n(n_shelves, 3)}
        if n_doors:
            sec = {"kind": "door", "door": _n(n_doors if n_doors is not True else None, 1),
                   "shelves": _n(n_shelves, 2)}
        elif n_drawers:
            sec = {"kind": "drawers", "drawers": _n(n_drawers, 3)}
        spec["sections"] = [sec]
    return spec


def _try_question(msg: str, context: dict[str, Any] | None) -> str | None:
    """Вопросы о модели без правки: стоимость/состав/габариты (D3)."""
    c = context or {}
    if re.search(r"сколько\s+сто|цена|стоимост", msg):
        t = c.get("estimate_total")
        return (f"Материалы по смете ≈ {t:,.0f} ₽ (закупка, без работы)."
                .replace(",", " ") if t else "Смета ещё не посчитана.")
    if re.search(r"сколько\s+дета|состав", msg):
        return (f"В изделии {c.get('n_panels', '?')} деталей и "
                f"{c.get('n_holes', '?')} присадок.")
    if re.search(r"габарит|размер изделия", msg):
        d = c.get("dims") or {}
        return f"Габариты: {d.get('w', '?')}×{d.get('d', '?')}×{d.get('h', '?')} мм."
    return None


class MockChatProvider:
    """Rule-based разбор типовых команд — офлайн, для тестов и деградации.

    Понимает: СОЗДАНИЕ с нуля («сделай тумбу 600×450×550 с 3 ящиками»),
    вопросы о модели («сколько стоит», «сколько деталей»), габариты,
    цвет/декор (+фасады), толщину плиты, ножки/опоры, царгу, металлокаркас.
    """

    def chat(self, spec: dict[str, Any], message: str,
             history: list[dict[str, str]] | None = None,
             context: dict[str, Any] | None = None,
             images: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if images:                                    # rule-based не видит картинок
            return {"reply": "Распознавание фото ТЗ требует нейросети — задайте "
                             "GEMINI_API_KEY в tools/basis/.env (бесплатно, "
                             "aistudio.google.com).", "spec": None}
        msg = message.lower()
        created = _try_create(msg)
        if created is not None:
            return {"reply": "Создал новое изделие по описанию — уточняй параметры.",
                    "spec": created, "created": True}
        q = _try_question(msg, context)
        if q is not None:
            return {"reply": q, "spec": None}
        new = copy.deepcopy(spec)
        done: list[str] = []

        for pat, key in ((rf"ширин\w*\D*?{_NUM}", "width"),
                         (rf"глубин\w*\D*?{_NUM}", "depth"),
                         (rf"высот\w*(?!\w*(царг|ножк|опор|экран))\D*?{_NUM}", "height")):
            m = re.search(pat, msg)
            if m:
                new.setdefault("dimensions", {})[key] = _num(m.group(m.lastindex))
                done.append(f"{key} = {new['dimensions'][key]}")

        m = re.search(r"фасад\w*\s+(?:цвет\w*\s+)?(?:на\s+)?«?\"?([а-яёa-z0-9 \-]+?)\"?»?\s*$", msg)
        if m:
            color = m.group(1).strip().capitalize()
            new.setdefault("materials", {})["facade_color"] = color
            done.append(f"цвет фасадов = {color}")
        else:
            m = re.search(r"(?:цвет|декор|материал)\w*\s+(?:на\s+)?«?\"?([а-яёa-z0-9 \-]+?)\"?»?\s*$", msg)
            if m:
                color = m.group(1).strip().capitalize()
                new.setdefault("materials", {})["color"] = color
                done.append(f"цвет = {color}")

        m = re.search(rf"(?:плит|толщин)\w*\D*?{_NUM}", msg)
        if m:
            new.setdefault("materials", {})["board_thickness"] = _num(m.group(1))
            done.append(f"толщина плиты = {new['materials']['board_thickness']}")

        if re.search(r"(убери|удали|без)\s+(ножк|опор)", msg):
            new.setdefault("legs", {})["height"] = 0
            done.append("ножки убраны")
        else:
            m = re.search(rf"(?:ножк|опор)\w*\D*?{_NUM}", msg)
            if m:
                new.setdefault("legs", {})["height"] = _num(m.group(1))
                done.append(f"ножки = {new['legs']['height']} мм")

        if re.search(r"(убери|удали|без)\s+царг", msg):
            new["apron"] = False
            done.append("царга убрана")
        else:
            m = re.search(rf"царг\w*\D*?{_NUM}", msg)
            if m:
                new["apron"] = True
                new["apron_height"] = _num(m.group(1))
                done.append(f"царга H = {new['apron_height']}")

        if re.search(r"металл|каркас\s+из\s+труб", msg):
            new["frame"] = "metal"
            done.append("каркас = metal")

        if not done:
            return {"reply": "Не понял команду (mock-провайдер понимает: габариты, "
                             "цвет, толщину плиты, ножки, царгу, металлокаркас). "
                             "Для свободных формулировок нужен SPEC_CHAT_PROVIDER=openai.",
                    "spec": None}
        return {"reply": "Применил: " + "; ".join(done), "spec": new}


# ------------------------------------------------------------------ openai

class OpenAIChatProvider:
    """Свободные формулировки через OpenAI (OPENAI_API_KEY, OPENAI_MODEL)."""

    def __init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Нет OPENAI_API_KEY для SPEC_CHAT_PROVIDER=openai")
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o")

    def chat(self, spec: dict[str, Any], message: str,
             history: list[dict[str, str]] | None = None,
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        system = CHAT_PROMPT_PATH.read_text(encoding="utf-8").replace(
            "__SCHEMA__", PARAMSPEC_SCHEMA_PATH.read_text(encoding="utf-8"))
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for h in (history or [])[-8:]:               # короткая память диалога
            msgs.append({"role": h.get("role", "user"), "content": h.get("text", "")})
        ctx = f"\nСостояние модели: {json.dumps(context, ensure_ascii=False)}\n" if context else ""
        msgs.append({"role": "user", "content":
                     f"Текущий ParamSpec:\n```json\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n```\n{ctx}\n"
                     f"Запрос пользователя: {message}"})
        r = self.client.chat.completions.create(
            model=self.model, temperature=0.1, messages=msgs,
            response_format={"type": "json_object"})
        data = json.loads(r.choices[0].message.content or "{}")
        return {"reply": str(data.get("reply") or "Готово."),
                "spec": data.get("spec") if isinstance(data.get("spec"), dict) else None}


class GeminiChatProvider:
    """Google Gemini (AKD-203): бесплатный tier, понимает фото/сканы ТЗ.

    Ключ — env GEMINI_API_KEY (aistudio.google.com), модель — GEMINI_MODEL
    (по умолчанию gemini-2.0-flash). REST без SDK; ответ — строго JSON
    {reply, spec} (response_mime_type). Изображения — inline_data base64."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

    def __init__(self) -> None:
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Нет GEMINI_API_KEY для SPEC_CHAT_PROVIDER=gemini")
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    def chat(self, spec: dict[str, Any], message: str,
             history: list[dict[str, str]] | None = None,
             context: dict[str, Any] | None = None,
             images: list[dict[str, str]] | None = None) -> dict[str, Any]:
        import requests
        system = CHAT_PROMPT_PATH.read_text(encoding="utf-8").replace(
            "__SCHEMA__", PARAMSPEC_SCHEMA_PATH.read_text(encoding="utf-8"))
        contents: list[dict[str, Any]] = []
        for h in (history or [])[-8:]:
            role = "model" if h.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": h.get("text", "")}]})
        ctx = f"\nСостояние модели: {json.dumps(context, ensure_ascii=False)}\n" if context else ""
        parts: list[dict[str, Any]] = [{"text":
            f"Текущий ParamSpec:\n```json\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n```\n{ctx}\n"
            f"Запрос пользователя: {message or '(см. приложенные изображения ТЗ)'}"}]
        for img in images or []:                     # фото/скан ТЗ
            parts.append({"inline_data": {"mime_type": img.get("mime", "image/png"),
                                          "data": img.get("data", "")}})
        contents.append({"role": "user", "parts": parts})
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.1,
                                 "response_mime_type": "application/json"},
        }
        r = requests.post(self.URL.format(m=self.model),
                          params={"key": self.api_key}, json=payload, timeout=120)
        if r.status_code == 429:
            raise RuntimeError("Лимит бесплатного тарифа Gemini исчерпан — "
                               "попробуйте через минуту (или завтра)")
        r.raise_for_status()
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in
                       (cand.get("content") or {}).get("parts") or [])
        data = json.loads(text or "{}")
        return {"reply": str(data.get("reply") or "Готово."),
                "spec": data.get("spec") if isinstance(data.get("spec"), dict) else None,
                "created": bool(data.get("created"))}


class GigaChatProvider:
    """Сбер GigaChat (AKD-203): работает с российских серверов (в отличие от
    Gemini). Ключ авторизации (Base64 Client:Secret) — env GIGACHAT_AUTH_KEY;
    обмен на OAuth-токен (~30 мин, кэшируем). Модель — GIGACHAT_MODEL
    (GigaChat / GigaChat-Pro / GigaChat-Max). Фото ТЗ — загрузка файла +
    attachments (нужна vision-модель, напр. GigaChat-Max).

    SSL: сертификаты Сбера выпущены Минцифры РФ — если корневой не установлен
    в системе, GIGACHAT_VERIFY=0 отключает проверку (демо; для прод — поставить
    корневой сертификат Russian Trusted CA)."""

    OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    BASE = "https://gigachat.devices.sberbank.ru/api/v1"

    def __init__(self) -> None:
        self.auth_key = os.environ.get("GIGACHAT_AUTH_KEY")
        if not self.auth_key:
            raise ValueError("Нет GIGACHAT_AUTH_KEY для SPEC_CHAT_PROVIDER=gigachat")
        self.scope = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self.model = os.environ.get("GIGACHAT_MODEL", "GigaChat")
        self.verify = os.environ.get("GIGACHAT_VERIFY", "0") not in ("0", "false", "no")
        self._token = None
        self._exp = 0.0

    def _now(self) -> float:
        import time
        return time.time()

    def _access_token(self) -> str:
        import uuid
        import requests
        if self._token and self._now() < self._exp - 60:
            return self._token
        if not self.verify:
            import urllib3
            urllib3.disable_warnings()
        r = requests.post(self.OAUTH, headers={
            "Authorization": f"Basic {self.auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
        }, data={"scope": self.scope}, timeout=30, verify=self.verify)
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._exp = float(d.get("expires_at", 0)) / 1000 or (self._now() + 1500)
        return self._token

    def _upload_images(self, images: list[dict[str, str]]) -> list[str]:
        import base64
        import requests
        ids: list[str] = []
        for im in images:
            raw = base64.b64decode(im.get("data", ""))
            r = requests.post(f"{self.BASE}/files",
                              headers={"Authorization": f"Bearer {self._access_token()}"},
                              files={"file": ("tz.png", raw, im.get("mime", "image/png"))},
                              data={"purpose": "general"}, timeout=60, verify=self.verify)
            r.raise_for_status()
            ids.append(r.json().get("id"))
        return ids

    def chat(self, spec: dict[str, Any], message: str,
             history: list[dict[str, str]] | None = None,
             context: dict[str, Any] | None = None,
             images: list[dict[str, str]] | None = None) -> dict[str, Any]:
        import requests
        system = CHAT_PROMPT_PATH.read_text(encoding="utf-8").replace(
            "__SCHEMA__", PARAMSPEC_SCHEMA_PATH.read_text(encoding="utf-8"))
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for h in (history or [])[-8:]:
            msgs.append({"role": h.get("role", "user"), "content": h.get("text", "")})
        ctx = f"\nСостояние модели: {json.dumps(context, ensure_ascii=False)}\n" if context else ""
        user_msg: dict[str, Any] = {"role": "user", "content":
            f"Текущий ParamSpec:\n```json\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n```\n{ctx}\n"
            f"Запрос: {message or '(см. приложенные изображения ТЗ)'}\n"
            "Ответь строго JSON-объектом {\"reply\":..., \"spec\":...}."}
        if images:
            user_msg["attachments"] = self._upload_images(images)
        msgs.append(user_msg)
        r = requests.post(f"{self.BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {self._access_token()}",
                                   "Content-Type": "application/json"},
                          json={"model": self.model, "messages": msgs, "temperature": 0.1},
                          timeout=120, verify=self.verify)
        if r.status_code == 429:
            raise RuntimeError("Лимит GigaChat исчерпан — попробуйте позже")
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        c1, c2 = text.find("{"), text.rfind("}")        # вычленить JSON из ответа
        data = json.loads(text[c1:c2 + 1]) if c1 >= 0 else {}
        return {"reply": str(data.get("reply") or "Готово."),
                "spec": data.get("spec") if isinstance(data.get("spec"), dict) else None,
                "created": bool(data.get("created"))}


def get_chat_provider(name: str | None = None) -> Any:
    name = (name or os.environ.get("SPEC_CHAT_PROVIDER")
            or os.environ.get("PARAMSPEC_PROVIDER")
            # авто по наличию ключа; gigachat работает с РФ-серверов (Gemini — нет)
            or ("gigachat" if os.environ.get("GIGACHAT_AUTH_KEY")
                else "gemini" if os.environ.get("GEMINI_API_KEY")
                else "openai" if os.environ.get("OPENAI_API_KEY") else "mock")).lower()
    if name == "gigachat":
        return GigaChatProvider()
    if name == "gemini":
        return GeminiChatProvider()
    if name == "openai":
        return OpenAIChatProvider()
    if name == "mock":
        return MockChatProvider()
    raise ValueError(f"Неизвестный SPEC_CHAT_PROVIDER={name!r} (mock|openai|gemini|gigachat)")


# ------------------------------------------------------------------ вход

def chat_edit(spec: dict[str, Any], message: str,
              history: list[dict[str, str]] | None = None,
              context: dict[str, Any] | None = None,
              images: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Команда словами → {reply, spec|None, changes[], created?}. Невалидное не отдаём.

    Гарантии: PROTECTED_KEYS не меняются; новая спека проходит validate_paramspec,
    иначе spec=None и причина в reply (AKD-110). created=True — изделие с нуля (D3).
    images — фото/сканы ТЗ [{mime, data(base64)}] для vision-провайдера (AKD-203).
    """
    from .paramspec import validate_paramspec

    provider = get_chat_provider()
    try:
        if images:
            res = provider.chat(spec, message, history, context, images=images)
        else:
            try:
                res = provider.chat(spec, message, history, context)
            except TypeError:
                res = provider.chat(spec, message, history)
    except Exception as e:                            # сеть/ключ/парсинг — в чат, не 500
        return {"reply": f"Ошибка провайдера: {e}", "spec": None, "changes": []}

    new = res.get("spec")
    if not new:
        return {"reply": res.get("reply", ""), "spec": None, "changes": []}

    created = bool(res.get("created"))
    if not created:
        for k in PROTECTED_KEYS:                      # структуру не трогаем
            if k in spec:
                new[k] = spec[k]
    errors = validate_paramspec(new)
    if errors:
        return {"reply": "Правка отклонена — спека не прошла схему:\n"
                         + "\n".join(errors[:5]), "spec": None, "changes": []}
    if created:
        return {"reply": res.get("reply", "Создано."), "spec": new,
                "changes": ["новое изделие с нуля"], "created": True}
    changes = spec_diff(spec, new)
    if not changes:
        return {"reply": res.get("reply", "Изменений нет."), "spec": None, "changes": []}
    return {"reply": res.get("reply", "Готово."), "spec": new, "changes": changes}
