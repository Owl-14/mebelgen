"""Versioned, node-scoped prompts for the furniture assistant.

The registry is intentionally independent from the Studio orchestrator and audit
storage.  Callers receive immutable prompt metadata in ``PromptRequest.trace``;
an orchestrator (MEB-145) can use the requests directly and an audit writer
(MEB-147) can persist the trace without importing either feature here.

Engineering calculations do not belong in prompts.  ParamSpec validation and
operation application remain deterministic Python contracts.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent.parent
PROMPT_ROOT = ROOT / "prompts" / "spec_chat"
PARAMSPEC_SCHEMA_PATH = ROOT / "schema" / "paramspec.schema.json"

MAX_MESSAGE_CHARS = 4_000
MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_ITEM_CHARS = 1_500
MAX_CONTEXT_CHARS = 16_000
MAX_PANELS = 40

_PROMPTS: dict[str, tuple[str, str]] = {
    "intent_routing": ("furniture.intent-routing", "1.0.0"),
    "vision_facts": ("furniture.vision-facts", "1.0.0"),
    "create_paramspec": ("furniture.create-paramspec", "1.0.0"),
    "edit_operations": ("furniture.edit-operations", "1.1.0"),
    "part_edit": ("furniture.part-edit", "1.0.0"),
    "diagnosis": ("furniture.diagnosis", "1.0.0"),
    "repair": ("furniture.repair", "1.0.0"),
    "answer_query": ("furniture.answer-query", "1.0.0"),
}

_READ_CAPABILITY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply"],
    "properties": {"reply": {"type": "string"}},
}

_ROUTING_CAPABILITY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent"],
    "properties": {
        "intent": {"enum": list(_PROMPTS)[1:]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


@dataclass(frozen=True)
class PromptRequest:
    node: str
    prompt_id: str
    prompt_version: str
    system: str
    user: str
    history: tuple[dict[str, str], ...] = ()
    context_chars: int = 0
    context_limit_chars: int = MAX_CONTEXT_CHARS

    @property
    def approximate_tokens(self) -> int:
        # Stable, provider-independent regression metric; deliberately not billed
        # token accounting (providers return real usage separately).
        chars = len(self.system) + len(self.user) + sum(
            len(item.get("text", "")) for item in self.history
        )
        return (chars + 3) // 4

    @property
    def trace(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "capability_version": (
                "2.0.0" if self.node in {"edit_operations", "part_edit", "repair"}
                else "1.0.0"
            ),
            "node": self.node,
            "context_chars": self.context_chars,
            "context_limit_chars": self.context_limit_chars,
            "approximate_prompt_tokens": self.approximate_tokens,
        }


def prompt_manifest() -> dict[str, dict[str, str]]:
    """Return a copy so callers cannot mutate the process-wide registry."""
    return {
        node: {"prompt_id": prompt_id, "prompt_version": version}
        for node, (prompt_id, version) in _PROMPTS.items()
    }


def _compact_schema(value: Any) -> Any:
    """Strip annotation-only JSON Schema fields while preserving validation."""
    if isinstance(value, dict):
        return {
            key: _compact_schema(item)
            for key, item in value.items()
            if key not in {"title", "description", "default", "examples"}
        }
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    return value


def _allowed_param_roots(schema: Mapping[str, Any]) -> list[str]:
    roots: set[str] = set()
    for definition in (schema.get("$defs") or {}).values():
        if isinstance(definition, dict):
            roots.update((definition.get("properties") or {}).keys())
    return sorted(roots)


def capability_schema(node: str) -> dict[str, Any]:
    if node == "intent_routing":
        return copy.deepcopy(_ROUTING_CAPABILITY)
    if node == "create_paramspec":
        schema = json.loads(PARAMSPEC_SCHEMA_PATH.read_text(encoding="utf-8"))
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["reply", "spec"],
            "properties": {
                "reply": {"type": "string"},
                "created": {"const": True},
                "spec": _compact_schema(schema),
            },
        }
    if node in {"edit_operations", "part_edit", "repair"}:
        from .edit_operations import edit_operation_json_schema

        operations_schema = edit_operation_json_schema()
        definitions = operations_schema.pop("$defs", {})
        # The provider chooses semantic actions. Concurrency guards and targets
        # that are implied by typed fields belong to the trusted compiler, not
        # to natural-language generation. This prevents invented section ids or
        # stale value_equals conditions from rejecting otherwise valid edits.
        inferred_targets = {
            "SetDimension", "SetMaterial", "ChangeArchetype", "DuplicateModel",
            "AddSection", "QueryModel", "DiagnoseModel",
        }
        for definition_name, definition in definitions.items():
            if not isinstance(definition, dict):
                continue
            properties = definition.get("properties") or {}
            required = list(definition.get("required") or [])
            required = [field for field in required if field != "preconditions"]
            if definition_name in inferred_targets:
                required = [field for field in required if field != "target_id"]
            definition["required"] = required
        result = {
            "type": "object",
            "additionalProperties": False,
            "required": ["reply", "operations"],
            "properties": {
                "reply": {"type": "string"},
                "operations": operations_schema,
            },
            "$defs": definitions,
        }
        schema = json.loads(PARAMSPEC_SCHEMA_PATH.read_text(encoding="utf-8"))
        result["x-paramspec-allowed-root-fields"] = _allowed_param_roots(schema)
        result["x-paramspec-protected-paths"] = ["/schemaVersion"]
        result["x-operation-contract-version"] = "2.0.0"
        result["x-operation-semantics"] = {
            "DuplicateModel": "copy the whole current furniture model left or right",
            "AddSection": "add an internal compartment inside the current model",
            "SetDimension": "change an overall furniture dimension",
            "UpdateSection": "change an existing internal compartment",
            "AddPanel": "add one shelf or vertical partition",
            "MovePanel": "move one shelf or vertical partition",
        }
        if node == "part_edit":
            result["x-allowed-operation-tags"] = ["AddPanel", "MovePanel", "DeletePart"]
        elif node == "repair":
            result["x-read-only-operation-tags-forbidden"] = ["QueryModel", "DiagnoseModel"]
        return result
    if node in {"vision_facts", "diagnosis", "answer_query"}:
        return copy.deepcopy(_READ_CAPABILITY)
    raise KeyError(f"Unknown prompt node: {node}")


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)] + "\n[context truncated]"


def _bounded_history(history: list[dict[str, str]] | None) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    for item in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        result.append({"role": role, "text": _bounded_text(item.get("text"), MAX_HISTORY_ITEM_CHARS)})
    return tuple(result)


def _pick(source: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: copy.deepcopy(source[key]) for key in keys if key in source}


def _context_for(node: str, context: Mapping[str, Any] | None) -> dict[str, Any]:
    source = context or {}
    if node == "answer_query":
        return _pick(source, ("n_panels", "n_holes", "dims", "estimate_total"))
    if node == "diagnosis":
        return _pick(source, ("check_errors", "base_unresolved"))
    if node == "repair":
        result = _pick(source, ("check_errors", "base_unresolved", "base_candidates"))
        if isinstance(source.get("panels"), list):
            result["panels"] = copy.deepcopy(source["panels"][:MAX_PANELS])
        return result
    if node == "part_edit":
        result = _pick(source, ("selected_part",))
        if isinstance(source.get("panels"), list):
            result["panels"] = copy.deepcopy(source["panels"][:MAX_PANELS])
        return result
    if node == "edit_operations":
        # Факты считает детерминированный генератор (spec_chat._drawer_facts),
        # модель лишь переводит относительные просьбы в значения поля (MEB-166).
        return _pick(source, ("drawer_facts",))
    return {}


def _spec_for(node: str, spec: Mapping[str, Any] | None) -> dict[str, Any] | None:
    source = spec or {}
    if node in {"create_paramspec", "answer_query", "diagnosis", "vision_facts", "intent_routing"}:
        return None
    if node == "part_edit":
        return _pick(source, ("schemaVersion", "materials", "overrides"))
    if node == "repair":
        return _pick(source, (
            "schemaVersion", "archetype", "dimensions", "materials", "legs",
            "sections", "hardware", "overrides", "warnings", "estimated_values",
        ))
    return copy.deepcopy(dict(source))


def classify_intent(message: str, spec: Mapping[str, Any] | None = None,
                    context: Mapping[str, Any] | None = None,
                    *, has_images: bool = False) -> str:
    """Conservative deterministic router; ambiguous mutations go to edit."""
    text = (message or "").strip().lower()
    ctx = context or {}
    if has_images or not spec or bool((spec or {}).get("draft")):
        return "create_paramspec"
    if ctx.get("selected_part") and re.search(
        r"\b(эт(?:у|ой|а)|детал|полк|фасад|панел|перегород|подвин|удал)\w*\b", text
    ):
        return "part_edit"
    if re.search(r"\b(почин|исправ|устран|восстанов)\w*\b", text):
        return "repair"
    if re.search(r"\b(диагност|ошиб|проблем|провер|что не так)\w*\b", text):
        return "diagnosis"
    if re.search(r"\b(создай|нов(?:ое|ую|ый)|с нуля|спроектируй|построй)\b", text):
        return "create_paramspec"
    if re.search(r"\bсделай\b", text) and re.search(
        r"\b(тумб|шкаф|гардероб|стеллаж|стол|стойк)\w*\b", text
    ) and re.search(r"\d{2,4}\s*[x×х*]\s*\d{2,4}", text):
        return "create_paramspec"
    if "?" in text or re.search(
        r"^(как|что|где|когда|почему|зачем|сколько|какие|какой|привет|здравств)", text
    ):
        return "answer_query"
    return "edit_operations"


_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|system)\b", re.IGNORECASE),
    re.compile(r"\b(?:system|developer)\s+prompt\b", re.IGNORECASE),
    re.compile(r"(?:игнорир|забуд)\w*\s+(?:все\s+)?(?:правил|инструкц)", re.IGNORECASE),
    re.compile(r"(?:системн\w*\s+(?:prompt|промпт)|покажи\w*\s+инструкц)", re.IGNORECASE),
)


def evaluate_request_policy(message: str) -> dict[str, Any]:
    """Return the production fail-closed policy decision for untrusted requests."""
    text = str(message or "")
    if any(pattern.search(text) for pattern in _PROMPT_INJECTION_PATTERNS):
        return {"allowed": False, "code": "prompt_injection"}
    return {"allowed": True, "code": None}


def build_prompt_request(node: str, *, message: str = "",
                         spec: Mapping[str, Any] | None = None,
                         history: list[dict[str, str]] | None = None,
                         context: Mapping[str, Any] | None = None) -> PromptRequest:
    if node not in _PROMPTS:
        raise KeyError(f"Unknown prompt node: {node}")
    prompt_id, version = _PROMPTS[node]
    template = (PROMPT_ROOT / f"{node}.txt").read_text(encoding="utf-8").strip()
    schema_text = json.dumps(capability_schema(node), ensure_ascii=False, separators=(",", ":"))
    system = template.replace("__CAPABILITY_SCHEMA__", schema_text)
    payload: dict[str, Any] = {"request": _bounded_text(message, MAX_MESSAGE_CHARS)}
    relevant_spec = _spec_for(node, spec)
    relevant_context = _context_for(node, context)
    if relevant_spec is not None:
        payload["paramspec"] = relevant_spec
    if relevant_context:
        payload["context"] = relevant_context
    user = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(user) > MAX_CONTEXT_CHARS:
        # Never emit invalid half-JSON.  The bounded representation is data, not
        # executable instructions, and makes truncation explicit to the model.
        user = json.dumps({
            "request": _bounded_text(message, MAX_MESSAGE_CHARS),
            "context_truncated": True,
            "context_preview": _bounded_text(user, MAX_CONTEXT_CHARS - 256),
        }, ensure_ascii=False, separators=(",", ":"))
    return PromptRequest(
        node=node,
        prompt_id=prompt_id,
        prompt_version=version,
        system=system,
        user=user,
        history=_bounded_history(history),
        context_chars=len(user),
    )


def build_chat_prompt_request(spec: Mapping[str, Any], message: str,
                              history: list[dict[str, str]] | None = None,
                              context: Mapping[str, Any] | None = None,
                              *, has_images: bool = False) -> PromptRequest:
    node = classify_intent(message, spec, context, has_images=has_images)
    return build_prompt_request(
        node, message=message, spec=spec, history=history, context=context
    )
