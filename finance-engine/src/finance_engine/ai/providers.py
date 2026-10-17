"""Camada de IA independente de fornecedor: Claude (Anthropic), ChatGPT (OpenAI) e Gemini (Google).

Todos os conectores implementam a mesma operação — ler um documento (PDF opcional) com uma
instrução e devolver JSON validado contra um esquema — para que o restante do sistema não dependa
de nenhum fornecedor. A escolha é por configuração:

    FIN_AI_PROVIDER=claude|openai|gemini|none
    FIN_AI_MODEL_CLAUDE=claude-opus-5-5   (padrão)
    FIN_AI_MODEL_OPENAI=<modelo da sua conta OpenAI>
    FIN_AI_MODEL_GEMINI=<modelo da sua conta Google>

Credenciais ficam nas variáveis de ambiente de cada SDK (ANTHROPIC_API_KEY, OPENAI_API_KEY,
GEMINI_API_KEY) — nunca no código nem no banco. Os SDKs são dependências opcionais
(``pip install finance-engine[ai]``).
"""

from __future__ import annotations

import base64
import json
from typing import Any, Protocol

from finance_engine.core.config import get_settings
from finance_engine.core.exceptions import FinanceEngineError


class AIProviderError(FinanceEngineError):
    pass


class AIProvider(Protocol):
    name: str

    def extract_json(
        self, prompt: str, schema: dict[str, Any], *, pdf: bytes | None = None
    ) -> dict[str, Any]: ...


def _parse(text: str | None, provider: str) -> dict[str, Any]:
    if not text:
        raise AIProviderError(f"{provider}: resposta vazia")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"{provider}: resposta não é JSON válido") from exc
    if not isinstance(data, dict):
        raise AIProviderError(f"{provider}: resposta não é um objeto JSON")
    return data


class ClaudeProvider:
    name = "claude"

    def __init__(self, client: Any | None = None, model: str = "claude-opus-5-5") -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client: Any = client
        self.model = model

    def extract_json(
        self, prompt: str, schema: dict[str, Any], *, pdf: bytes | None = None
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if pdf is not None:
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf).decode(),
                    },
                }
            )
        content.append({"type": "text", "text": prompt})
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            # Recusa por política: a API refaz a chamada em outro modelo automaticamente.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason == "refusal":
            raise AIProviderError("claude: a IA recusou processar este documento")
        if response.stop_reason == "max_tokens":
            raise AIProviderError("claude: resposta longa demais; divida o documento")
        return _parse(next((b.text for b in response.content if b.type == "text"), None), self.name)


class OpenAIProvider:
    name = "openai"

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if not model:
            raise AIProviderError("defina FIN_AI_MODEL_OPENAI com o modelo da sua conta OpenAI")
        if client is None:
            import openai

            client = openai.OpenAI()
        self.client: Any = client
        self.model = model

    def extract_json(
        self, prompt: str, schema: dict[str, Any], *, pdf: bytes | None = None
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if pdf is not None:
            content.append(
                {
                    "type": "file",
                    "file": {
                        "filename": "documento.pdf",
                        "file_data": "data:application/pdf;base64,"
                        + base64.standard_b64encode(pdf).decode(),
                    },
                }
            )
        content.append({"type": "text", "text": prompt})
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "extracao", "schema": schema, "strict": True},
            },
        )
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise AIProviderError(f"openai: recusou ({choice.message.refusal})")
        if choice.finish_reason == "length":
            raise AIProviderError("openai: resposta longa demais; divida o documento")
        return _parse(choice.message.content, self.name)


class GeminiProvider:
    name = "gemini"

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if not model:
            raise AIProviderError("defina FIN_AI_MODEL_GEMINI com o modelo da sua conta Google")
        if client is None:
            from google import genai

            client = genai.Client()
        self.client: Any = client
        self.model = model

    def extract_json(
        self, prompt: str, schema: dict[str, Any], *, pdf: bytes | None = None
    ) -> dict[str, Any]:
        from google.genai import types

        contents: list[Any] = []
        if pdf is not None:
            contents.append(types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
        contents.append(prompt)
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_json_schema=schema
            ),
        )
        return _parse(response.text, self.name)


def get_ai_provider(name: str | None = None) -> AIProvider | None:
    """Fornecedor configurado, ou ``None`` quando a IA está desligada."""
    s = get_settings()
    choice = (name or s.ai_provider).lower()
    if choice in ("", "none"):
        return None
    if choice == "claude":
        return ClaudeProvider(model=s.ai_model_claude)
    if choice == "openai":
        return OpenAIProvider(model=s.ai_model_openai)
    if choice == "gemini":
        return GeminiProvider(model=s.ai_model_gemini)
    raise AIProviderError(
        f"fornecedor de IA desconhecido: {choice} (use claude, openai, gemini ou none)"
    )
