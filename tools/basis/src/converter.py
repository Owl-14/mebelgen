from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from .validate import validate_furniture

# openai — лишь ОДИН из поставщиков ParamSpec. Основной путь
# (paramspec → generate → валидаторы) его не требует, поэтому импорт ленивый.

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_PATH = ROOT / "prompts" / "system_prompt.txt"
DEFAULT_SCHEMA_PATH = ROOT / "schema" / "furniture.schema.json"
PARAMSPEC_PROMPT_PATH = ROOT / "prompts" / "paramspec_prompt.txt"
PARAMSPEC_SCHEMA_PATH = ROOT / "schema" / "paramspec.schema.json"


def load_system_prompt(path: Path | None = None) -> str:
    prompt_path = path or DEFAULT_PROMPT_PATH
    return prompt_path.read_text(encoding="utf-8")


def _encode_image(image_path: Path) -> tuple[str, str]:
    suffix = image_path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/png")
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return mime, data


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


class FurnitureConverter:
    """Конвертация изображения + спецификации в JSON через Vision API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        prompt_path: Path | None = None,
        schema_path: Path | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Укажите OPENAI_API_KEY в .env или передайте api_key. "
                "Скопируйте .env.example в .env и заполните ключ."
            )
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.prompt_path = prompt_path
        self.schema_path = schema_path or DEFAULT_SCHEMA_PATH
        from openai import OpenAI  # ленивый импорт: нужен только для OpenAI-провайдера
        self.client = OpenAI(api_key=self.api_key)

    def convert(
        self,
        image_path: Path,
        *,
        extra_instructions: str = "",
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        image_path = Path(image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Изображение не найдено: {image_path}")

        system_prompt = load_system_prompt(self.prompt_path)
        schema_text = self.schema_path.read_text(encoding="utf-8")
        user_text = (
            "Проанализируй изображение мебельного изделия и верни JSON по схеме.\n\n"
            f"JSON Schema:\n{schema_text}"
        )
        if extra_instructions.strip():
            user_text += f"\n\nДополнительные указания:\n{extra_instructions.strip()}"

        mime, b64 = _encode_image(image_path)
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or "{}"
        data = _extract_json(raw)
        errors = validate_furniture(data, self.schema_path)
        if errors:
            raise ValueError(
                "Модель вернула JSON, не прошедший валидацию:\n" + "\n".join(errors)
            )
        return data

    def convert_paramspec(
        self,
        image_path: Path,
        *,
        extra_instructions: str = "",
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Изображение → ParamSpec (классификация + параметры, без координат)."""
        from .paramspec import validate_paramspec

        image_path = Path(image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Изображение не найдено: {image_path}")

        system_prompt = PARAMSPEC_PROMPT_PATH.read_text(encoding="utf-8")
        schema_text = PARAMSPEC_SCHEMA_PATH.read_text(encoding="utf-8")
        user_text = (
            "Определи archetype и извлеки ParamSpec по схеме.\n\n"
            f"JSON Schema:\n{schema_text}"
        )
        if extra_instructions.strip():
            user_text += f"\n\nДополнительные указания:\n{extra_instructions.strip()}"

        mime, b64 = _encode_image(image_path)
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]},
            ],
            response_format={"type": "json_object"},
        )
        spec = _extract_json(response.choices[0].message.content or "{}")
        errors = validate_paramspec(spec)
        if errors:
            raise ValueError("ParamSpec не прошёл валидацию:\n" + "\n".join(errors))
        return spec

    def convert_to_project(
        self,
        image_path: Path,
        *,
        allow_raw_fallback: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Основной поток: ParamSpec(LLM) → генератор(код) → project.json.

        При неизвестном архетипе / ошибке генератора и allow_raw_fallback —
        откат на прямую LLM-генерацию panels[] (raw, под валидаторами; см. AKD-34).
        """
        from .generators import generate_from_paramspec

        spec = self.convert_paramspec(image_path, **kwargs)
        try:
            project = generate_from_paramspec(spec)
            project.setdefault("warnings", []).append(f"source: generator/{spec['archetype']}")
            return project
        except ValueError:
            if not allow_raw_fallback:
                raise
            project = self.convert(image_path, **kwargs)
            project.setdefault("warnings", []).append("source: raw (нет генератора для архетипа)")
            return project

    def convert_to_file(
        self,
        image_path: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        data = self.convert(image_path, **kwargs)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
