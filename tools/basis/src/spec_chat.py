"""Чат-правка ParamSpec словами (AKD-107/109/110, MEB-143).

Пользователь пишет в Studio «сделай глубину 600», «замени цвет на дуб вотан» —
провайдер возвращает минимальные типизированные операции, reducer атомарно
применяет их к ParamSpec, а конвейер пересобирает модель. Координаты по-прежнему
считает детерминированный генератор (rules/core.md), не LLM.

Провайдер: env SPEC_CHAT_PROVIDER (fallback PARAMSPEC_PROVIDER): mock|openai.
mock — rule-based разбор типовых русских команд, без сети (тесты/CI/офлайн).

Безопасность применения: preconditions проверяются перед каждой операцией,
пакет применяется copy-on-write, итог валидируется как ParamSpec v1, а сводка
изменений строится только по фактически затронутым полям.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from .prompt_registry import (
    build_chat_prompt_request,
    build_prompt_request,
    classify_intent,
    evaluate_request_policy,
)

ROOT = Path(__file__).resolve().parent.parent

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


def _coordinate_overrides(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return provider-controlled geometry while tolerating unchanged legacy data."""
    out = []
    for override in spec.get("overrides") or []:
        if not isinstance(override, dict):
            continue
        if "placement" in override or "move" in override or override.get("action") == "add":
            out.append({key: copy.deepcopy(override.get(key))
                        for key in ("panel", "action", "placement", "move", "type")
                        if key in override})
    return out


_FORBIDDEN_LLM_GEOMETRY_KEYS = {
    "placement", "move", "x", "y", "z", "x1", "x2", "y1", "y2", "z1", "z2",
}


def _contains_llm_coordinates(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in _FORBIDDEN_LLM_GEOMETRY_KEYS for key in value):
            return True
        return any(_contains_llm_coordinates(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_llm_coordinates(item) for item in value)
    return False


def _coordinate_refusal(usage: Any, trace: dict[str, Any] | None = None) -> dict[str, Any]:
    message = ("Правка отклонена: LLM не может задавать placement/move/координаты "
               "деталей; используйте семантические AddPanel/MovePanel.")
    return {
        "reply": message,
        "error": message,
        "code": "llm_coordinates_forbidden",
        "reason": {"code": "llm_coordinates_forbidden", "message": message},
        "spec": None,
        "changes": [],
        "operations": [],
        "resolved_operations": [],
        "usage": usage,
        "trace": trace or {"prompts": []},
    }


def _production_gate_refusal(
    decision: Any,
    usage: Any,
    *,
    operations: list[dict[str, Any]] | None = None,
    resolved_operations: list[dict[str, Any]] | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details = [problem.detail for problem in decision.report.errors[:5]]
    reply = "Правка отклонена производственным гейтом:\n" + "\n".join(details)
    return {
        "reply": reply,
        "error": "Предложенная AI-правка не прошла производственный гейт.",
        "code": "production_gate_rejected",
        "spec": None,
        "changes": [],
        "operations": operations or [],
        "resolved_operations": resolved_operations or [],
        "usage": usage,
        "check_report": decision.report.to_dict(),
        "trace": trace or {"prompts": []},
    }


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
        request = build_chat_prompt_request(
            spec, message, history, context, has_images=bool(images)
        )
        trace = {"prompts": [request.trace], "router": {
            "kind": "deterministic", "node": request.node,
        }}
        if images:                                    # rule-based не видит картинок
            return {"reply": "Распознавание фото ТЗ требует нейросети — задайте "
                             "GEMINI_API_KEY в tools/basis/.env (бесплатно, "
                             "aistudio.google.com).", "spec": None, "trace": trace}
        msg = message.lower()
        created = _try_create(msg)
        if created is not None:
            return {"reply": "Создал новое изделие по описанию — уточняй параметры.",
                    "spec": created, "created": True, "trace": trace}
        q = _try_question(msg, context)
        if q is not None:
            return {"reply": q, "spec": None, "trace": trace}
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
                    "spec": None, "trace": trace}
        # Офлайн-провайдер тоже говорит с ядром патчами.  Узкий compatibility
        # patch нужен только старым mock-командам (ножки/царга/frame), для
        # которых в первом срезе EditOperation пока нет публичного варианта.
        operations, compatibility = _legacy_response_patches(spec, new)
        return {"reply": "Применил: " + "; ".join(done),
                "operations": operations,
                "compatibility_patches": compatibility,
                "trace": trace}


# ------------------------------------------------------------------ openai-совместимые

# Пресеты OpenAI-совместимых сервисов (AKD-209): китайские модели доступны с
# РФ-серверов и сильнее GigaChat в следовании инструкциям. base_url/model можно
# переопределить env-ами LLM_BASE_URL / LLM_MODEL / LLM_VISION_MODEL.
_OAI_PRESETS = {
    "openai":   {"base": None, "key": "OPENAI_API_KEY", "model": "gpt-4o",
                 "vision": "gpt-4o", "json_mode": True},
    "kimi":     {"base": "https://api.moonshot.ai/v1", "key": "KIMI_API_KEY",
                 "model": "moonshot-v1-8k", "vision": "moonshot-v1-8k-vision-preview",
                 "json_mode": True},
    # glm-4.5-flash — thinking-модель: без отключения размышлений «думает»
    # минутами на больших промптах (extra_body поддержан OpenAI SDK)
    "glm":      {"base": "https://open.bigmodel.cn/api/paas/v4", "key": "GLM_API_KEY",
                 "model": "glm-4.5-flash", "vision": "glm-4.5v", "json_mode": False,
                 "extra": {"thinking": {"type": "disabled"}}},
    "deepseek": {"base": "https://api.deepseek.com", "key": "DEEPSEEK_API_KEY",
                 "model": "deepseek-chat", "vision": "deepseek-chat", "json_mode": True},
}


def _json_object(text: str) -> dict[str, Any]:
    """Первый полноценный JSON-объект из ответа модели.

    Провайдеры присылают его то в markdown-обёртке, то двумя объектами подряд
    (GigaChat), и жадный разбор «от первой { до последней }» падал с
    «Extra data»: живое ТЗ отклонялось как сбой сети. Берём первый объект,
    который действительно разбирается.
    """
    decoder = json.JSONDecoder()
    position = text.find("{")
    while position >= 0:
        try:
            value, _ = decoder.raw_decode(text[position:])
        except ValueError:
            position = text.find("{", position + 1)
            continue
        if isinstance(value, dict):
            return value
        position = text.find("{", position + 1)
    return {}


def _provider_result(data: dict[str, Any], request: Any, *, usage: Any = None,
                     model: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reply": str(data.get("reply") or "Готово."),
        "spec": data.get("spec") if isinstance(data.get("spec"), dict) else None,
        "operations": data.get("operations") if isinstance(data.get("operations"), list) else None,
        "created": bool(data.get("created")),
        "trace": {"prompts": [request.trace], "router": {
            "kind": "deterministic", "node": request.node,
        }},
    }
    if usage is not None:
        result["usage"] = {
            "model": model,
            "total": getattr(usage, "total_tokens", None),
            "prompt": getattr(usage, "prompt_tokens", None),
            "completion": getattr(usage, "completion_tokens", None),
        }
    return result


def _merge_usage(first: Any, second: Any) -> Any:
    """Сложить расход двух вызовов (зрение + сборка) — иначе счётчик и бюджет
    видят только сборку. Модель берётся из второго (сборщика)."""
    if not isinstance(first, dict):
        return second
    if not isinstance(second, dict):
        return first
    merged = dict(second)
    for key in ("prompt", "completion", "total"):
        values = [v for v in (first.get(key), second.get(key)) if isinstance(v, (int, float))]
        merged[key] = sum(values) if values else None
    return merged


class OpenAICompatProvider:
    """Любой OpenAI-совместимый чат (OpenAI, Kimi/Moonshot, GLM/Zhipu, DeepSeek).

    Понимает фото ТЗ (vision-модель, формат image_url data-URI) и возвращает
    расход токенов для счётчика. JSON вытаскиваем лениво — часть сервисов не
    поддерживает response_format."""

    def __init__(self, preset: str = "openai") -> None:
        p = _OAI_PRESETS.get(preset, _OAI_PRESETS["openai"])
        api_key = os.environ.get("LLM_API_KEY") or os.environ.get(p["key"])
        if not api_key:
            raise ValueError(f"Нет {p['key']} (или LLM_API_KEY) для SPEC_CHAT_PROVIDER={preset}")
        base = os.environ.get("LLM_BASE_URL", p["base"])
        from openai import OpenAI
        try:
            timeout = max(1.0, float(os.environ.get("SPEC_CHAT_TIMEOUT_S", "120")))
        except ValueError:
            timeout = 120.0
        # Не оставляем интерфейс ждать SDK-дефолт (до 10 минут плюс повторы),
        # но один повтор на 429/5xx даём: иначе любая сетевая икота провайдера
        # превращается в ошибку команды.
        try:
            retries = max(0, int(os.environ.get("SPEC_CHAT_MAX_RETRIES", "1")))
        except ValueError:
            retries = 1
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=retries,
                             **({"base_url": base} if base else {}))
        self.model = os.environ.get("LLM_MODEL", p["model"])
        self.vision_model = os.environ.get("LLM_VISION_MODEL", p["vision"])
        self.json_mode = os.environ.get("LLM_JSON_MODE",
                                        "1" if p["json_mode"] else "0") not in ("0", "false", "no")
        self.extra = p.get("extra") or {}             # extra_body (напр. thinking off)

    def balance(self) -> dict[str, Any] | None:
        """Денежный баланс аккаунта: Kimi/Moonshot — GET /users/me/balance,
        DeepSeek — GET /user/balance. У GLM/OpenAI такого API нет — None."""
        import requests
        base = str(self.client.base_url).rstrip("/")
        headers = {"Authorization": f"Bearer {self.client.api_key}"}
        for path in ("/users/me/balance", "/user/balance"):
            try:
                r = requests.get(f"{base}{path}", headers=headers, timeout=15)
                body = r.json() if r.ok else None
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            d = body.get("data") or {}
            if isinstance(d, dict) and "available_balance" in d:
                return {"value": d["available_balance"], "unit": "¥"}
            infos = body.get("balance_infos") or []
            if infos and isinstance(infos[0], dict) and "total_balance" in infos[0]:
                currency = str(infos[0].get("currency") or "")
                try:
                    value: Any = float(infos[0]["total_balance"])
                except (TypeError, ValueError):
                    value = infos[0]["total_balance"]
                return {"value": value, "unit": {"CNY": "¥", "USD": "$"}.get(currency, currency)}
        return None

    def vision_extract(self, images: list[dict[str, str]]) -> str:
        """Этап 1 конвейера (AKD-211): факты с фото ТЗ простым текстом."""
        request = build_prompt_request("vision_facts")
        content: list[dict[str, Any]] = [{"type": "text", "text": request.system}]
        for im in images:
            content.append({"type": "image_url", "image_url":
                            {"url": f"data:{im.get('mime','image/png')};base64,{im.get('data','')}"}})
        r = self.client.chat.completions.create(
            model=self.vision_model, temperature=0.1,
            messages=[{"role": "user", "content": content}])
        usage = getattr(r, "usage", None)
        self.last_usage = {"model": self.vision_model,
                           "total": getattr(usage, "total_tokens", None),
                           "prompt": getattr(usage, "prompt_tokens", None),
                           "completion": getattr(usage, "completion_tokens", None)}
        text = r.choices[0].message.content or ""
        return str(_json_object(text).get("reply") or text)

    def chat(self, spec: dict[str, Any], message: str,
             history: list[dict[str, str]] | None = None,
             context: dict[str, Any] | None = None,
             images: list[dict[str, str]] | None = None) -> dict[str, Any]:
        request = build_chat_prompt_request(
            spec, message, history, context, has_images=bool(images)
        )
        msgs: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
        for h in request.history:
            msgs.append({"role": h.get("role", "user"), "content": h.get("text", "")})
        text = request.user
        if images:                                   # vision-формат OpenAI: content-массив
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            for im in images:
                content.append({"type": "image_url", "image_url":
                                {"url": f"data:{im.get('mime','image/png')};base64,{im.get('data','')}"}})
            msgs.append({"role": "user", "content": content})
        else:
            msgs.append({"role": "user", "content": text})
        model = self.vision_model if images else self.model
        kw: dict[str, Any] = {"model": model, "temperature": 0.1, "messages": msgs}
        if self.json_mode and not images:
            kw["response_format"] = {"type": "json_object"}
        if self.extra:
            kw["extra_body"] = self.extra
        r = self.client.chat.completions.create(**kw)
        out = r.choices[0].message.content or "{}"
        data = _json_object(out)
        usage = getattr(r, "usage", None)
        result = _provider_result(data, request, usage=usage, model=model)
        result["raw_text"] = out                      # для AI-журнала; chat_edit не отдаёт наружу
        return result


# обратная совместимость: SPEC_CHAT_PROVIDER=openai
def OpenAIChatProvider() -> "OpenAICompatProvider":  # noqa: N802
    return OpenAICompatProvider("openai")


class GeminiChatProvider:
    """Google Gemini (AKD-203): бесплатный tier, понимает фото/сканы ТЗ.

    Ключ — env GEMINI_API_KEY (aistudio.google.com), модель — GEMINI_MODEL
    (по умолчанию gemini-2.0-flash). REST без SDK; ответ следует capability
    schema выбранного узла. Изображения — inline_data base64."""

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
        request = build_chat_prompt_request(
            spec, message, history, context, has_images=bool(images)
        )
        contents: list[dict[str, Any]] = []
        for h in request.history:
            role = "model" if h.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": h.get("text", "")}]})
        parts: list[dict[str, Any]] = [{"text": request.user}]
        for img in images or []:                     # фото/скан ТЗ
            parts.append({"inline_data": {"mime_type": img.get("mime", "image/png"),
                                          "data": img.get("data", "")}})
        contents.append({"role": "user", "parts": parts})
        payload = {
            "system_instruction": {"parts": [{"text": request.system}]},
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
        body = r.json()
        cand = (body.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in
                       (cand.get("content") or {}).get("parts") or [])
        data = _json_object(text)
        result = _provider_result(data, request)
        result["usage"] = self._usage(body)
        result["raw_text"] = text
        return result

    def _usage(self, body: dict[str, Any]) -> dict[str, Any]:
        meta = body.get("usageMetadata") or {}
        return {"model": self.model, "total": meta.get("totalTokenCount"),
                "prompt": meta.get("promptTokenCount"),
                "completion": meta.get("candidatesTokenCount")}

    def vision_extract(self, images: list[dict[str, str]]) -> str:
        import requests
        request = build_prompt_request("vision_facts")
        parts: list[dict[str, Any]] = [{"text": request.user}]
        for image in images:
            parts.append({"inline_data": {
                "mime_type": image.get("mime", "image/png"),
                "data": image.get("data", ""),
            }})
        payload = {
            "system_instruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.1,
                                 "response_mime_type": "application/json"},
        }
        response = requests.post(
            self.URL.format(m=self.model), params={"key": self.api_key},
            json=payload, timeout=120,
        )
        response.raise_for_status()
        body = response.json()
        self.last_usage = self._usage(body)
        candidate = (body.get("candidates") or [{}])[0]
        text = "".join(part.get("text", "") for part in
                       (candidate.get("content") or {}).get("parts") or [])
        return str(_json_object(text).get("reply") or text)


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
        self.model = os.environ.get("GIGACHAT_MODEL", "GigaChat")   # текст (900k free)
        self.vision_model = os.environ.get("GIGACHAT_VISION_MODEL", "GigaChat-Max")
        # verify: False — отключить; иначе путь к CA-бандлу с корневым Минцифры
        # (requests при verify=True берёт certifi и не видит системное хранилище)
        _v = os.environ.get("GIGACHAT_VERIFY", "0")
        if _v in ("0", "false", "no"):
            self.verify: Any = False
        else:
            self.verify = (os.environ.get("GIGACHAT_CA")
                           or os.environ.get("REQUESTS_CA_BUNDLE") or True)
        self._token = None
        self._exp = 0.0

    def balance(self) -> list[dict[str, Any]]:
        """Остаток бесплатных токенов по моделям: [{usage, value}] (для счётчика)."""
        import requests
        r = requests.get(f"{self.BASE}/balance",
                         headers={"Authorization": f"Bearer {self._access_token()}"},
                         timeout=20, verify=self.verify)
        r.raise_for_status()
        return r.json().get("balance", [])

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

    def vision_extract(self, images: list[dict[str, str]]) -> str:
        """Этап 1 конвейера (AKD-211): распознать фото ТЗ и выписать факты
        ПРОСТЫМ ТЕКСТОМ (не JSON). Дальше по этим фактам собирает другая модель."""
        import requests
        att = self._upload_images(images)
        request = build_prompt_request("vision_facts")
        r = requests.post(f"{self.BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {self._access_token()}",
                                   "Content-Type": "application/json"},
                          json={"model": self.vision_model, "temperature": 0.1,
                                "messages": [{"role": "user", "content": request.system,
                                              "attachments": att}]},
                          timeout=120, verify=self.verify)
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage") or {}
        self.last_usage = {"model": self.vision_model, "total": usage.get("total_tokens"),
                           "prompt": usage.get("prompt_tokens"),
                           "completion": usage.get("completion_tokens")}
        text = body["choices"][0]["message"]["content"]
        return str(_json_object(text).get("reply") or text)

    def chat(self, spec: dict[str, Any], message: str,
             history: list[dict[str, str]] | None = None,
             context: dict[str, Any] | None = None,
             images: list[dict[str, str]] | None = None) -> dict[str, Any]:
        import requests
        request = build_chat_prompt_request(
            spec, message, history, context, has_images=bool(images)
        )
        msgs: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
        for h in request.history:
            msgs.append({"role": h.get("role", "user"), "content": h.get("text", "")})
        user_msg: dict[str, Any] = {"role": "user", "content": request.user}
        model = self.model
        if images:                                       # фото ТЗ — vision-модель
            user_msg["attachments"] = self._upload_images(images)
            model = self.vision_model
        msgs.append(user_msg)
        r = requests.post(f"{self.BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {self._access_token()}",
                                   "Content-Type": "application/json"},
                          json={"model": model, "messages": msgs, "temperature": 0.1},
                          timeout=120, verify=self.verify)
        if r.status_code == 429:
            raise RuntimeError("Лимит GigaChat исчерпан — попробуйте позже")
        r.raise_for_status()
        body = r.json()
        text = body["choices"][0]["message"]["content"]
        data = _json_object(text)                        # вычленить JSON из ответа
        usage = body.get("usage") or {}
        result = _provider_result(data, request)
        result["usage"] = {"model": model, "total": usage.get("total_tokens"),
                           "prompt": usage.get("prompt_tokens"),
                           "completion": usage.get("completion_tokens")}
        result["raw_text"] = text
        return result


def resolve_provider_name(name: str | None = None) -> str:
    """Имя провайдера, которое реально будет использовано (для конвейера AKD-211)."""
    return (name or os.environ.get("SPEC_CHAT_PROVIDER")
            or os.environ.get("PARAMSPEC_PROVIDER")
            # авто по наличию ключа; gigachat работает с РФ-серверов (Gemini — нет)
            or ("gigachat" if os.environ.get("GIGACHAT_AUTH_KEY")
                else "gemini" if os.environ.get("GEMINI_API_KEY")
                else "openai" if os.environ.get("OPENAI_API_KEY") else "mock")).lower()


def get_chat_provider(name: str | None = None) -> Any:
    name = resolve_provider_name(name)
    if name == "gigachat":
        return GigaChatProvider()
    if name == "gemini":
        return GeminiChatProvider()
    if name in ("openai", "kimi", "glm", "deepseek"):   # OpenAI-совместимые (AKD-209)
        return OpenAICompatProvider(name)
    if name == "mock":
        return MockChatProvider()
    raise ValueError(f"Неизвестный SPEC_CHAT_PROVIDER={name!r} "
                     "(mock|openai|kimi|glm|deepseek|gemini|gigachat)")


# ------------------------------------------------------------------ вход

def _legacy_response_patches(spec: dict[str, Any], new: dict[str, Any]) \
        -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Translate rolling-update full-spec responses into narrow patches.

    Only the old mock provider's established non-operation fields remain on the
    compatibility path.  Any other full-spec rewrite is rejected instead of
    silently accepting collateral changes.
    """
    old_flat, new_flat = _flatten(spec), _flatten(new)
    changed = sorted(path for path in set(old_flat) | set(new_flat)
                     if old_flat.get(path, _MISSING) != new_flat.get(path, _MISSING))
    operations: list[dict[str, Any]] = []
    compatibility: list[dict[str, Any]] = []
    compatible_paths = {"legs.height", "apron", "apron_height", "frame"}

    for path in changed:
        old_value = old_flat.get(path, _MISSING)
        new_value = new_flat.get(path, _MISSING)
        precondition = ([{"kind": "value_equals", "path": path, "value": old_value}]
                        if old_value is not _MISSING else
                        [{"kind": "target_exists", "target_id": path.split(".")[0]}])
        if path.startswith("dimensions.") and path.count(".") == 1 and new_value is not _MISSING:
            operations.append({"op": "SetDimension", "target_id": path,
                               "preconditions": precondition,
                               "dimension": path.split(".")[1], "value": new_value})
        elif path.startswith("materials.") and path.count(".") == 1:
            operations.append({"op": "SetMaterial", "target_id": path,
                               "preconditions": precondition,
                               "field": path.split(".")[1],
                               "value": None if new_value is _MISSING else new_value})
        elif path == "archetype" and new_value is not _MISSING:
            operations.append({"op": "ChangeArchetype", "target_id": "archetype",
                               "preconditions": precondition, "archetype": new_value})
        elif path in compatible_paths:
            compatibility.append({"path": path, "old": old_value,
                                  "value": new_value})
        else:
            raise ValueError(f"legacy full-spec response changed unsupported field {path!r}")
    return operations, compatibility


_MISSING = object()


def _unique_section_id(spec: dict[str, Any], preferred: str) -> str:
    existing = {
        str(section.get("id") or "").strip()
        for section in spec.get("sections") or []
        if isinstance(section, dict)
    }
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", preferred.strip()).strip("-") or "section"
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def _normalize_provider_operations(
    operations: list[Any], spec: dict[str, Any] | None = None,
) -> list[Any]:
    """Translate provider output into the typed operation contract.

    Only deterministic compilation is allowed here. Targets and preconditions
    come from the selected ParamSpec, not provider guesses. For AddSection the
    requested semantic id is allocated uniquely against the current sections;
    no command wording or catalog fixture is special-cased.
    """
    normalized: list[Any] = []
    aliases = {
        "add_panel": "AddPanel",
        "add": "AddPanel",
        "move_panel": "MovePanel",
        "move": "MovePanel",
    }
    semantic_fields = {
        "panel_type", "section_id", "between", "above", "below", "middle",
        "align_front", "align_back", "delta_mm",
    }
    source_spec = spec or {}
    for raw in operations:
        if not isinstance(raw, dict):
            normalized.append(raw)
            continue
        if "op" in raw:
            item = copy.deepcopy(raw)
            op_name = item.get("op")
            if op_name == "SetDimension" and item.get("dimension") in {
                "width", "depth", "height", "depth_carcass", "tolerance",
            }:
                item["target_id"] = f"dimensions.{item['dimension']}"
                current = _flatten(source_spec).get(item["target_id"], _MISSING)
                item["preconditions"] = (
                    [{"kind": "value_equals", "path": item["target_id"], "value": current}]
                    if current is not _MISSING else
                    [{"kind": "target_exists", "target_id": "dimensions"}]
                )
            elif op_name == "SetMaterial" and isinstance(item.get("field"), str):
                item["target_id"] = f"materials.{item['field']}"
                current = _flatten(source_spec).get(item["target_id"], _MISSING)
                item["preconditions"] = (
                    [{"kind": "value_equals", "path": item["target_id"], "value": current}]
                    if current is not _MISSING else
                    [{"kind": "target_exists", "target_id": "materials"}]
                )
            elif op_name == "ChangeArchetype":
                item["target_id"] = "archetype"
                item["preconditions"] = [{
                    "kind": "value_equals", "path": "archetype",
                    "value": source_spec.get("archetype"),
                }]
            elif op_name == "DuplicateModel":
                item["target_id"] = "model"
                item["preconditions"] = [{"kind": "target_exists", "target_id": "model"}]
            elif op_name == "AddSection" and isinstance(item.get("section"), dict):
                section = item["section"]
                target_id = str(item.get("target_id") or "")
                supplied_id = target_id.removeprefix("section:")
                explicit_section_target = target_id.startswith("section:")
                plain_section_target = (
                    bool(target_id)
                    and ":" not in target_id
                    and target_id not in {"section", "sections"}
                )
                if (not section.get("id") and supplied_id
                        and (explicit_section_target or plain_section_target)):
                    section["id"] = supplied_id
                section_id = _unique_section_id(
                    source_spec, str(section.get("id") or supplied_id or "section")
                )
                section["id"] = section_id
                canonical_target = f"section:{section_id}"
                item["target_id"] = canonical_target
                item["preconditions"] = [{
                    "kind": "target_missing", "target_id": canonical_target,
                }]
            elif op_name in {"QueryModel", "DiagnoseModel"}:
                item["target_id"] = "model"
                item["preconditions"] = [{"kind": "target_exists", "target_id": "model"}]
            elif op_name in {
                "UpdateSection", "DeleteSection", "AddShelf", "MovePanel",
                "MovePart", "ResizePart", "DeletePart",
            } and item.get("target_id"):
                item["preconditions"] = [{
                    "kind": "target_exists", "target_id": item["target_id"],
                }]
            elif op_name == "AddPanel" and item.get("target_id"):
                item["preconditions"] = [{
                    "kind": "target_missing", "target_id": item["target_id"],
                }]
            normalized.append(item)
            continue
        kind = str(raw.get("kind") or raw.get("operation") or raw.get("action") or "")
        op = aliases.get(kind)
        if op is None:
            normalized.append(raw)
            continue
        panel_id = str(raw.get("panel_id") or raw.get("panel") or raw.get("name") or "")
        target_id = panel_id if panel_id.startswith("part:") else f"part:{panel_id}"
        condition = "target_missing" if op == "AddPanel" else "target_exists"
        item = {
            "op": op,
            "target_id": target_id,
            "preconditions": [{"kind": condition, "target_id": target_id}],
            "panel_type": raw.get("panel_type") or raw.get("type"),
        }
        item.update({key: raw[key] for key in semantic_fields if key in raw})
        normalized.append(item)
    return normalized


def _apply_compatibility_patches(spec: dict[str, Any],
                                 patches: list[dict[str, Any]]) -> dict[str, Any]:
    working = copy.deepcopy(spec)
    for patch in patches:
        path = str(patch["path"])
        actual = _flatten(working).get(path, _MISSING)
        expected = patch["old"]
        if expected is _MISSING:
            if actual is not _MISSING:
                raise ValueError(f"precondition failed for compatibility patch {path!r}")
        elif actual is _MISSING or actual != expected:
            raise ValueError(f"precondition failed for compatibility patch {path!r}")
        parts = path.split(".")
        current = working
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        if patch["value"] is _MISSING:
            current.pop(parts[-1], None)
        else:
            current[parts[-1]] = patch["value"]
    return working


def _drawer_facts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Фактические размеры ящиков для edit-промпта (MEB-166).

    Без них модель переводит «стенки до 2/3 фасада» в выдуманные миллиметры.
    Считает детерминированный генератор; сбой генерации — просто без фактов.
    """
    sections = spec.get("sections") or []
    drawer_sections = [i for i, s in enumerate(sections)
                       if isinstance(s, dict) and s.get("kind") == "drawers"]
    if not drawer_sections:
        return []
    # предел берём у самого генератора: пробная сборка с заведомо огромной
    # высотой короба возвращает уже зажатые боковины — одна формула, не две
    probe = copy.deepcopy(spec)
    for index in drawer_sections:
        probe["sections"][index]["box_height"] = 100_000
    try:
        from .generators import generate_from_paramspec
        project = generate_from_paramspec(copy.deepcopy(spec))
        probe_panels = generate_from_paramspec(probe).get("panels") or []
    except Exception:
        return []
    panels = project.get("panels") or []
    facts: list[dict[str, Any]] = []
    for index in drawer_sections:
        section = sections[index]
        # section_id генераторов: drawer_unit — drawer_stack, колонки — id или col<N>
        ids = {str(section.get("id") or f"col{index + 1}")}
        if len(drawer_sections) == 1:
            ids.add("drawer_stack")
        fronts = sorted((p for p in panels if p.get("type") == "drawer_front"
                         and p.get("section_id") in ids),
                        key=lambda p: -p["placement"]["y1"])
        sides = [p for p in panels if p.get("type") == "drawer_side_left"
                 and p.get("section_id") in ids]
        if not fronts:
            continue
        heights = [round(p["dimensions"]["height"], 1) for p in fronts]
        limits = [p["dimensions"]["height"] for p in probe_panels
                  if p.get("type") == "drawer_side_left" and p.get("section_id") in ids]
        facts.append({
            "target_id": f"sections.{index}",
            "drawers": len(fronts),
            "front_heights_mm": heights,              # сверху вниз
            "box_height_mm": round(sides[0]["dimensions"]["height"], 1) if sides else None,
            "box_height_explicit": "box_height" in section,
            "max_box_height_mm": round(min(limits), 1) if limits else None,
        })
    return facts


def _repair_created_spec(
    provider: Any,
    candidate: dict[str, Any],
    decision: Any,
    history: list[dict[str, str]] | None,
    context: dict[str, Any] | None,
    capture: dict[str, Any],
) -> tuple[Any, dict[str, Any]] | None:
    """Один повтор приёмки ТЗ: вернуть модели ошибки гейта и применить её правку.

    Модель отвечает типизированными операциями (узел repair), их применяет тот
    же детерминированный reducer, что и обычные правки чата, — геометрию она
    по-прежнему не считает. Повтор ровно один: если и он не помог, изделие
    честно отклоняется, а обе попытки видно в AI-журнале.
    """
    from .edit_operations import apply_edit_operations
    from .paramspec_normalize import normalize_candidate
    from .production_gate import evaluate_production_gate

    errors = [f"{issue.code}: {issue.detail}" for issue in decision.report.errors[:8]]
    repair_context = dict(context or {})
    repair_context["check_errors"] = errors
    capture["repair_errors"] = errors
    try:
        answer = provider.chat(copy.deepcopy(candidate),
                               "Исправь изделие по ошибкам проверок.",
                               history, repair_context)
    except Exception as error:  # noqa: BLE001 - ремонт не обязан удаться
        capture["repair_failed"] = f"{type(error).__name__}: {error}"
        return None
    if not isinstance(answer, dict):
        return None
    raw = answer.pop("raw_text", None)
    if raw:
        capture["raw_response"] = (capture.get("raw_response") or "") + "\n--- ремонт ---\n" + raw
    operations = answer.get("operations")
    if not isinstance(operations, list) or not operations:
        capture["repair_operations"] = []
        return None
    try:
        normalized_operations = _normalize_provider_operations(operations, candidate)
        if _contains_llm_coordinates(normalized_operations):
            capture["repair_failed"] = "llm_coordinates_forbidden"
            return None
        applied = apply_edit_operations(candidate, normalized_operations, context)
    except Exception as error:  # noqa: BLE001 - кривой ремонт не ломает приёмку
        capture["repair_failed"] = f"{type(error).__name__}: {error}"
        return None
    capture["repair_operations"] = [str(item.get("op")) for item in normalized_operations
                                    if isinstance(item, dict)]
    repaired = normalize_candidate(applied["spec"]).spec
    return evaluate_production_gate(repaired, unresolved_materials_are_errors=False), repaired


# Перегрузка провайдера — не вина ТЗ: 429 и таймауты стоит повторить на резерве.
_TRANSIENT_MARKERS = ("429", "rate limit", "ratelimit", "too many requests", "timeout",
                      "timed out", "502", "503", "504", "overload", "перегруж",
                      "访问量过大", "temporarily unavailable")


def _is_transient(error: BaseException) -> bool:
    status = getattr(error, "status_code", None) or getattr(
        getattr(error, "response", None), "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    text = f"{type(error).__name__}: {error}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def fallback_provider_names(current: str) -> list[str]:
    """Кого пробовать вместо перегруженного: SPEC_CHAT_FALLBACK или ключи из .env."""
    configured = [item.strip().lower() for item in
                  os.environ.get("SPEC_CHAT_FALLBACK", "").split(",") if item.strip()]
    if configured:
        return [name for name in configured if name != current]
    return [entry["id"] for entry in available_providers()["providers"]
            if entry["id"] not in (current, "mock")]


def _chat_with_fallback(provider: Any, name: str, capture: dict[str, Any],
                        *args: Any, **kwargs: Any) -> tuple[Any, Any]:
    """Спросить сборщика, а при его перегрузке — резервного провайдера.

    Клиент не должен видеть «сервис недоступен», пока в .env есть живой ключ.
    Обе попытки видно в AI-журнале: fallback_from и подменённый provider.
    """
    try:
        return provider, provider.chat(*args, **kwargs)
    except Exception as error:
        if not _is_transient(error):
            raise
        # Сначала тот же провайдер: 429 у него держится секунды, а резерв обычно
        # слабее как сборщик — уходить на него сразу значит менять задержку на
        # заведомо худший результат.
        import time as _time

        _time.sleep(float(os.environ.get("SPEC_CHAT_RETRY_PAUSE_S", "3")))
        try:
            answer = provider.chat(*args, **kwargs)
        except Exception as repeat_error:
            if not _is_transient(repeat_error):
                raise
            capture["retry_failed"] = f"{type(repeat_error).__name__}"
        else:
            capture["retried_same_provider"] = name
            return provider, answer
        for spare_name in fallback_provider_names(name):
            try:
                spare = get_chat_provider(spare_name)
                answer = spare.chat(*args, **kwargs)
            except Exception:
                continue
            capture["fallback_from"] = f"{name}: {type(error).__name__}"
            capture["provider"] = spare_name
            capture["model"] = str(getattr(spare, "model", spare_name))
            return spare, answer
        raise


def chat_edit(spec: dict[str, Any], message: str,
              history: list[dict[str, str]] | None = None,
              context: dict[str, Any] | None = None,
              images: list[dict[str, str]] | None = None,
              provider: str | None = None,
              journal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Команда словами → атомарный operation patch + совместимый Studio-ответ.

    journal — необязательный dict, который заполняется для AI-журнала: провайдер,
    модель, узел, версия промпта, сырой ответ модели, факты с фото, исключение.

    Гарантии: LLM-операции типизированы, имеют target/preconditions и применяются
    copy-on-write; новая спека проходит validate_paramspec, иначе spec=None.
    created=True остаётся совместимым путём импорта нового изделия с нуля (D3).
    images — фото/сканы ТЗ [{mime, data(base64)}] для vision-провайдера (AKD-203).
    provider — явный выбор нейросети из UI (AKD-210); None → из env.
    """
    from .telemetry import span

    def summarize(payload: dict[str, Any]) -> dict[str, Any]:
        with span("response.summarize", {
            "response.changed": isinstance(payload.get("spec"), dict),
            "check.outcome": "error" if payload.get("error") else "pass",
        }):
            return payload

    capture = journal if isinstance(journal, dict) else {}
    request_policy = evaluate_request_policy(message)
    if not request_policy["allowed"]:
        capture["node"] = "request_policy"
        reply = "Запрос отклонён политикой безопасности. Сформулируйте мебельную правку без инструкций по раскрытию или обходу системных правил."
        return summarize({
            "reply": reply, "error": reply, "code": request_policy["code"],
            "spec": None, "changes": [], "operations": [],
            "resolved_operations": [], "usage": None,
            "trace": {"prompts": [], "policy": request_policy},
        })

    build_name = resolve_provider_name(provider)
    routed_node = classify_intent(message, spec, context, has_images=bool(images))
    if routed_node == "edit_operations":
        facts = _drawer_facts(spec)
        if facts:
            context = {**(context or {}), "drawer_facts": facts}
    prompt_meta = build_prompt_request(
        routed_node, message=message, spec=spec, history=history, context=context
    ).trace
    with span("intent.classify", {
        "provider": build_name,
        "image.count": len(images or []),
        "operation.types": [routed_node],
        "prompt.version": prompt_meta["prompt_version"],
    }):
        build = get_chat_provider(build_name)
    provider_spec = copy.deepcopy(spec)
    model_name = str(getattr(build, "model", "mock"))
    base_trace = {
        "provider": build_name,
        "model": model_name,
        "prompt.version": prompt_meta["prompt_version"],
        "gen_ai.system": build_name,
        "gen_ai.request.model": model_name,
        "langsmith.span.kind": "llm",
    }
    capture.update(provider=build_name, model=model_name, node=routed_node,
                   prompt_version=prompt_meta["prompt_version"])
    # Конвейер «глаза+мозг» (AKD-211): если пришло фото, а сборщик — не тот
    # провайдер, что назначен на зрение (VISION_EXTRACT_PROVIDER, обычно GigaChat),
    # то этап 1 — GigaChat распознаёт факты с фото ТЗ текстом, этап 2 — сборщик
    # (GLM) собирает изделие уже по этим фактам, без картинки.
    extract_name = (os.environ.get("VISION_EXTRACT_PROVIDER") or build_name).lower()
    vision_trace = build_prompt_request("vision_facts").trace if images else None
    two_stage = False
    try:
        with span("operations.plan", base_trace) as plan_span:
            vis = get_chat_provider(extract_name) if images else None
            two_stage = bool(images and hasattr(vis, "vision_extract"))
            if two_stage:
                with span("vision.extract", {
                    "provider": extract_name,
                    "model": str(getattr(vis, "vision_model", getattr(vis, "model", ""))),
                    "image.count": len(images or []),
                    "prompt.version": (vision_trace or {}).get("prompt_version"),
                    "langsmith.span.kind": "llm",
                }):
                    desc = vis.vision_extract(images) if hasattr(vis, "vision_extract") else ""
                capture["vision_facts"] = desc
                if desc.strip():
                    aug = (("Создай новый ParamSpec по этому ТЗ. " + message).strip()
                           + "\n\nРаспознано с фото ТЗ (используй как факты, ничего не додумывай "
                             "сверх):\n" + desc)
                    build, res = _chat_with_fallback(build, build_name, capture,
                                                     provider_spec, aug, history, context)
                else:                                 # распознать не вышло — фото напрямую
                    build, res = _chat_with_fallback(build, build_name, capture, provider_spec,
                                                     message, history, context, images=images)
                if isinstance(res, dict):
                    res["usage"] = _merge_usage(getattr(vis, "last_usage", None),
                                                res.get("usage"))
            elif images:
                build, res = _chat_with_fallback(build, build_name, capture, provider_spec,
                                                 message, history, context, images=images)
            else:
                try:
                    build, res = _chat_with_fallback(build, build_name, capture,
                                                     provider_spec, message, history, context)
                except TypeError:
                    build, res = _chat_with_fallback(build, build_name, capture,
                                                     provider_spec, message, history)
            if isinstance(res, dict):
                capture["raw_response"] = res.pop("raw_text", None)
            provider_usage = res.get("usage") if isinstance(res, dict) else None
            if isinstance(provider_usage, dict):
                plan_span.set_attributes({
                    "model": provider_usage.get("model") or model_name,
                    "gen_ai.request.model": provider_usage.get("model") or model_name,
                    "gen_ai.usage.input_tokens": provider_usage.get("prompt"),
                    "gen_ai.usage.output_tokens": provider_usage.get("completion"),
                    "gen_ai.usage.total_tokens": provider_usage.get("total"),
                })
    except Exception as e:                            # сеть/ключ/парсинг — в чат, не 500
        capture["exception"] = f"{type(e).__name__}: {e}"
        error_message = f"Сервис AI не ответил: {e}"
        node = classify_intent(message, spec, context, has_images=bool(images))
        request = build_prompt_request(node, message=message, spec=spec,
                                       history=history, context=context)
        return summarize({"reply": error_message, "error": error_message,
                "code": "ai_provider_failed", "spec": None,
                "changes": [], "trace": {"prompts": [request.trace], "router": {
                    "kind": "deterministic", "node": node,
                }}})

    with span("operations.validate", {
        **base_trace, "check.name": "provider_response",
    }) as response_span:
        if not isinstance(res, dict):
            response_span.set_attributes({
                "check.outcome": "fail", "error.codes": ["invalid_response"],
            })
            return summarize({"reply": "Сервис AI вернул некорректный ответ.",
                              "error": "Сервис AI вернул некорректный ответ.",
                              "code": "invalid_provider_response",
                              "spec": None, "changes": []})
        response_span.set_attributes({"check.outcome": "pass"})

    usage = res.get("usage")                          # расход токенов (для счётчика)
    node = routed_node
    provider_trace = res.get("trace")
    trace = copy.deepcopy(provider_trace) if isinstance(provider_trace, dict) else {}
    if not isinstance(trace.get("prompts"), list):
        trace["prompts"] = []
    # Provider metadata is evidence, not authority.  Keep the router trace in
    # sync with the deterministic capability that is actually enforced below.
    trace["router"] = {"kind": "deterministic", "node": node}
    if two_stage and vision_trace:
        trace = dict(trace)
        trace["prompts"] = [vision_trace, *(trace.get("prompts") or [])]
    if two_stage and isinstance(res, dict):           # пометка конвейера в ответе
        res["reply"] = "📷 Фото распознано → " + str(res.get("reply") or "готово")
    # The provider may echo trace metadata, but it cannot choose its own
    # capability.  Only the deterministic route computed before the call is
    # authoritative; otherwise a crafted response could escalate an edit into
    # unrestricted create_paramspec.
    legacy_spec = res.get("spec") if isinstance(res.get("spec"), dict) else None
    raw_operations = (list(res.get("operations"))
                      if isinstance(res.get("operations"), list) else [])
    if node in {"intent_routing", "vision_facts", "diagnosis", "answer_query"}:
        return summarize({"reply": res.get("reply", ""), "spec": None, "changes": [],
                "operations": [], "resolved_operations": [], "usage": usage,
                "trace": trace})
    if node == "create_paramspec":
        if legacy_spec is None:
            reply = "Не удалось собрать новый ParamSpec: AI не вернул полное изделие."
            return summarize({"reply": reply, "error": reply,
                    "code": "create_paramspec_missing", "spec": None,
                    "changes": [], "operations": [], "resolved_operations": [],
                    "usage": usage, "trace": trace})
        if _coordinate_overrides(legacy_spec):
            return _coordinate_refusal(usage, trace)
        # Синонимы секций, габариты строкой и лишние поля правим детерминированно:
        # иначе гейт отклоняет верно распознанное ТЗ из-за мелкой неточности LLM.
        from .paramspec_normalize import normalize_candidate
        from .production_gate import evaluate_production_gate

        normalized = normalize_candidate(legacy_spec)
        legacy_spec = normalized.spec
        if normalized.notes:
            capture["normalization"] = normalized.notes

        # Приёмка ТЗ: артикул материала подбирает проектировщик в Studio, поэтому
        # нерешённый слот — предупреждение. Экспорт в производство по-прежнему
        # идёт через строгий гейт и красную спеку наружу не выпустит.
        decision = evaluate_production_gate(legacy_spec,
                                            unresolved_materials_are_errors=False)
        repaired_ops: list[str] = []
        if not decision.report.ok:
            attempt = _repair_created_spec(build, legacy_spec, decision, history,
                                           context, capture)
            if attempt is not None:
                repaired_decision, repaired_spec = attempt
                if repaired_decision.report.ok:
                    decision, legacy_spec = repaired_decision, repaired_spec
                    repaired_ops = capture.get("repair_operations") or []
        if not decision.report.ok:
            refusal = _production_gate_refusal(decision, usage, trace=trace)
            refusal["normalization"] = normalized.notes
            return refusal
        accepted = decision.accepted_spec
        assert accepted is not None
        unresolved = [issue.detail for issue in decision.report.warnings
                      if issue.code == "materials.unresolved"]
        if unresolved:
            warnings = accepted.get("warnings")
            if not isinstance(warnings, list):
                warnings = []
                accepted["warnings"] = warnings
            warnings.append("Материалы не выбраны из производственной базы — "
                            "уточнить в Studio до экспорта в производство.")
        reply = res.get("reply", "Создано.")
        if normalized.notes:
            reply += "\nПоправлено под контракт: " + "; ".join(normalized.notes[:5])
        if repaired_ops:
            reply += ("\nИсправлено по ошибкам проверок: "
                      + ", ".join(dict.fromkeys(repaired_ops)))
        if unresolved:
            reply += f"\nМатериалы нужно выбрать из базы: слотов — {len(unresolved)}."
        return summarize({"reply": reply, "spec": accepted,
                "changes": ["новое изделие с нуля"], "created": True,
                "operations": [], "resolved_operations": [], "usage": usage,
                "normalization": normalized.notes,
                "check_report": decision.report.to_dict(), "trace": trace})
    if node == "part_edit":
        if legacy_spec is not None:
            reply = "Правка отклонена — узел детали принимает только типизированные операции."
            return {"reply": reply, "error": reply,
                    "code": "part_edit_contract_violation",
                    "spec": None, "changes": [],
                    "operations": [], "resolved_operations": [], "usage": usage,
                    "trace": trace}
        normalized_for_scope = _normalize_provider_operations(raw_operations, spec)
        allowed_part_ops = {"AddPanel", "MovePanel", "DeletePart"}
        if any(not isinstance(item, dict) or item.get("op") not in allowed_part_ops
               for item in normalized_for_scope):
            reply = "Правка отклонена — узел детали может выполнять только AddPanel/MovePanel/DeletePart."
            return {"reply": reply, "error": reply,
                    "code": "part_edit_operation_forbidden",
                    "spec": None, "changes": [],
                    "operations": [], "resolved_operations": [], "usage": usage,
                    "trace": trace}
        raw_operations = normalized_for_scope

    compatibility = (res.get("compatibility_patches")
                     if isinstance(res.get("compatibility_patches"), list) else [])
    try:
        if legacy_spec is not None:
            expected_geometry = _coordinate_overrides(spec)
            if _coordinate_overrides(legacy_spec) != expected_geometry:
                return _coordinate_refusal(usage, trace)
            for key in PROTECTED_KEYS:
                if key in spec:
                    legacy_spec[key] = spec[key]
            legacy_operations, compatibility = _legacy_response_patches(spec, legacy_spec)
            raw_operations.extend(legacy_operations)
        if _contains_llm_coordinates(raw_operations):
            return _coordinate_refusal(usage, trace)
        raw_operations = _normalize_provider_operations(raw_operations, spec)
        if not raw_operations and not compatibility:
            return {"reply": res.get("reply", ""), "spec": None,
                    "changes": [], "operations": [], "resolved_operations": [],
                    "usage": usage, "trace": trace}

        from .edit_operations import EditApplicationError, apply_edit_operations
        operation_names = [str(item.get("op") or item.get("kind") or "unknown")
                           for item in raw_operations if isinstance(item, dict)]
        with span("paramspec.apply", {
            "operation.types": operation_names,
            "response.changed": bool(raw_operations or compatibility),
        }):
            applied = apply_edit_operations(spec, raw_operations, context)
            new = _apply_compatibility_patches(applied["spec"], compatibility)
    except Exception as error:
        reply = f"Правка отклонена — операции не применены: {error}"
        return {"reply": reply, "error": reply,
                "code": "operation_validation_failed", "spec": None,
                "changes": [], "operations": [], "resolved_operations": [],
                "usage": usage, "trace": trace}

    changes = spec_diff(spec, new)
    operation_replies = applied.get("replies") or []
    reply = str(res.get("reply") or "")
    if operation_replies:
        reply = "\n".join(operation_replies if not reply or reply == "Готово." else [reply, *operation_replies])
    if not changes:
        return summarize({"reply": reply or "Изменений нет.", "spec": None, "changes": [],
                "operations": applied["operations"],
                "resolved_operations": applied.get("resolved_operations") or [],
                "usage": usage, "trace": trace})
    from .production_gate import evaluate_production_gate

    decision = evaluate_production_gate(new)
    if not decision.report.ok:
        return _production_gate_refusal(
            decision,
            usage,
            operations=applied["operations"],
            resolved_operations=applied.get("resolved_operations") or [],
            trace=trace,
        )
    accepted = decision.accepted_spec
    assert accepted is not None
    return summarize({"reply": reply or "Готово.", "spec": accepted,
            "changes": spec_diff(spec, accepted),
            "operations": applied["operations"],
            "resolved_operations": applied.get("resolved_operations") or [], "usage": usage,
            "check_report": decision.report.to_dict(), "trace": trace})


_PROVIDER_META = {          # id → (человекочитаемое имя, env-ключ наличия)
    "gigachat": ("GigaChat (Сбер)", "GIGACHAT_AUTH_KEY"),
    "kimi": ("Kimi (Moonshot)", "KIMI_API_KEY"),
    "glm": ("GLM (Zhipu)", "GLM_API_KEY"),
    "deepseek": ("DeepSeek", "DEEPSEEK_API_KEY"),
    "gemini": ("Gemini (Google)", "GEMINI_API_KEY"),
    "openai": ("OpenAI", "OPENAI_API_KEY"),
}


def available_providers() -> dict[str, Any]:
    """Список доступных нейросетей (по наличию ключей в env) + активная по
    умолчанию — для селектора в UI (AKD-210)."""
    out = [{"id": "mock", "name": "Базовый (правила, без ИИ)"}]
    for pid, (label, envk) in _PROVIDER_META.items():
        if os.environ.get(envk) or os.environ.get("LLM_API_KEY"):
            out.append({"id": pid, "name": label})
    active = (os.environ.get("SPEC_CHAT_PROVIDER") or "").lower()
    if active not in {p["id"] for p in out}:
        active = out[1]["id"] if len(out) > 1 else "mock"
    return {"active": active, "providers": out}


def token_balance(provider: str | None = None) -> dict[str, Any]:
    """Лимиты/баланс выбранного провайдера для счётчика (AKD-210):
    GigaChat — бесплатные токены по моделям; Kimi/OpenAI-совместимые — денежный
    баланс аккаунта (¥/$). Пусто — если провайдер без баланса."""
    try:
        p = get_chat_provider(provider)
    except Exception as e:
        return {"error": str(e)}
    try:
        if isinstance(p, GigaChatProvider):
            bal = p.balance()                         # [{usage, value}]
            items = [{"label": b.get("usage"), "value": b.get("value"), "unit": "ток."}
                     for b in bal]
            return {"provider": "gigachat", "kind": "tokens", "items": items}
        if isinstance(p, OpenAICompatProvider):
            b = p.balance()                           # {value, unit} | None
            if b:
                return {"provider": "openai_compat", "kind": "money",
                        "items": [{"label": "Баланс аккаунта", "value": b["value"],
                                   "unit": b["unit"]}]}
            return {"provider": "openai_compat", "kind": "none", "items": []}
    except Exception as e:
        return {"error": str(e)}
    return {"kind": "none", "items": []}
