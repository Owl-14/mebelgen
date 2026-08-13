"""Version boundary for persisted ParamSpec documents.

ParamSpec currently has one data-format version: ``paramspec-v1``.  The
Pydantic major version used to validate it is an implementation detail and is
not a ParamSpec schema version.

The on-disk v1 layout stays flat for backwards compatibility.  This adapter
nevertheless exposes its two logical layers explicitly:

* ``payload`` contains the domain input consumed by generators;
* ``catalog`` contains server-owned catalog/ownership metadata.

Reads may ignore and report unknown extension fields, while writes always run
the existing strict Pydantic contract and emit only its canonical shape.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError

from .paramspec import CatalogMetadata, ParamSpec


PARAMSPEC_V1 = "paramspec-v1"
_UNION_TAGS = {
    "corpus", "shelving", "drawer_unit", "door_unit", "cabinet",
    "wardrobe", "desk", "table", "round_table", "composite",
    "__legacy_draft__", "shelves", "drawers", "door", "open",
}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _remove_extra_at_location(candidate: Any, location: tuple[Any, ...]) -> str:
    """Remove one Pydantic ``extra_forbidden`` path and return its JSON path."""

    current = candidate
    parent: Any = None
    parent_key: Any = None
    visible: list[str] = []
    for part in location:
        if part == "root":
            continue
        if isinstance(current, dict) and part in current:
            parent, parent_key = current, part
            current = current[part]
            visible.append(str(part))
            continue
        if isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            parent, parent_key = current, part
            current = current[part]
            visible.append(str(part))
            continue
        # Discriminated-union branch names appear in Pydantic locations but are
        # not keys in the persisted JSON document.
        if str(part) in _UNION_TAGS:
            continue
        raise ValueError(f"Cannot resolve unknown ParamSpec field at {location!r}")
    if isinstance(parent, dict):
        del parent[parent_key]
    elif isinstance(parent, list) and isinstance(parent_key, int):
        del parent[parent_key]
    else:
        raise ValueError(f"Cannot remove unknown ParamSpec field at {location!r}")
    return ".".join(visible) or "(root)"


def _tolerant_model(data: Mapping[str, Any]) -> tuple[ParamSpec, tuple[str, ...]]:
    """Strip only reported extras, compatible with every supported Pydantic v2."""

    candidate = _json_copy(dict(data))
    unknown: list[str] = []
    for _attempt in range(256):
        try:
            return ParamSpec.model_validate(candidate), tuple(sorted(set(unknown)))
        except ValidationError as error:
            details = error.errors(include_url=False)
            extras = [item for item in details if item.get("type") == "extra_forbidden"]
            # Unknown extensions must never mask a missing/invalid known field.
            if not extras or len(extras) != len(details):
                raise
            for detail in extras:
                unknown.append(
                    _remove_extra_at_location(candidate, tuple(detail.get("loc") or ()))
                )
    raise ValueError("Too many unknown ParamSpec extension fields")


@dataclass(frozen=True)
class ParamSpecEnvelopeV1:
    """Typed in-memory envelope for the backwards-compatible flat v1 file."""

    payload: ParamSpec
    catalog: CatalogMetadata | None
    unknown_fields: tuple[str, ...]
    source_hash: str
    canonical_changed: bool

    @property
    def schema_version(self) -> str:
        return PARAMSPEC_V1

    def payload_dict(self) -> dict[str, Any]:
        """Return generator input without catalog/ownership metadata."""

        value = self.payload.to_generator_dict()
        value.pop("catalog", None)
        return value

    def canonical_document(self) -> dict[str, Any]:
        """Return the strict persisted v1 shape (flat for compatibility)."""

        value = self.payload_dict()
        if self.catalog is not None:
            value["catalog"] = self.catalog.model_dump(
                mode="json", exclude_none=True, exclude_unset=True
            )
        # A programming error in the split/merge boundary must never create a
        # document that the strict writer itself would reject.
        return ParamSpec.model_validate(value).to_generator_dict()

    def metrics(self) -> dict[str, Any]:
        """Privacy-safe read metrics; no ParamSpec values are exposed."""

        return {
            "schema_version": self.schema_version,
            "source_hash": self.source_hash,
            "unknown_field_count": len(self.unknown_fields),
            "unknown_fields": list(self.unknown_fields),
            "canonical_changed": self.canonical_changed,
            "catalog_metadata_present": self.catalog is not None,
        }


def read_paramspec_v1(data: Mapping[str, Any]) -> ParamSpecEnvelopeV1:
    """Tolerantly read v1 while keeping all known fields strictly typed.

    Only unknown fields are tolerated.  Missing fields, invalid values and an
    unsupported ``schemaVersion`` still fail validation.
    """

    if not isinstance(data, Mapping):
        raise TypeError("ParamSpec document must be a mapping")
    source = _json_copy(dict(data))
    tolerant, unknown_fields = _tolerant_model(source)
    canonical = tolerant.to_generator_dict()
    catalog_value = canonical.pop("catalog", None)
    catalog = (
        CatalogMetadata.model_validate(catalog_value)
        if isinstance(catalog_value, Mapping)
        else None
    )
    payload = ParamSpec.model_validate(canonical)
    envelope = ParamSpecEnvelopeV1(
        payload=payload,
        catalog=catalog,
        unknown_fields=unknown_fields,
        source_hash=_digest(source),
        canonical_changed=False,
    )
    canonical_document = envelope.canonical_document()
    return ParamSpecEnvelopeV1(
        payload=envelope.payload,
        catalog=envelope.catalog,
        unknown_fields=envelope.unknown_fields,
        source_hash=envelope.source_hash,
        # JSON numeric spelling is part of the persisted representation even
        # though Python considers 1200 and 1200.0 equal.
        canonical_changed=_digest(source) != _digest(canonical_document),
    )


def strict_paramspec_v1_for_write(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a write candidate strictly and return canonical JSON data."""

    if not isinstance(data, Mapping):
        raise TypeError("ParamSpec document must be a mapping")
    typed = ParamSpec.model_validate(dict(data))
    return typed.to_generator_dict()
