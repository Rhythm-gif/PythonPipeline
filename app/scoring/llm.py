"""
PACR Pipeline - LLM Scoring Service
Evaluates papers using a configurable LLM provider.
Supports: OpenAI, Gemini, OpenRouter, Ollama
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

import httpx

from app.config.settings import get_settings
from app.common.logging import get_logger
from app.papers.models import AIReview

logger = get_logger(__name__)

SCORING_PROMPT = """\
You are a rigorous academic peer reviewer for PACR, a trusted research platform.
Evaluate the research paper below and decide whether it should be APPROVED or REJECTED
for publication on the platform.

Paper:
Title: {title}
Abstract: {abstract}
Authors: {authors}
Journal/Venue: {journal}
Institutional Affiliations: {affiliations}
Funding Sources: {funding_sources}

Score each dimension from 0 to 25:
- novelty: How original and innovative is this work?
- credibility: How credible are the authors, journal, and methodology signals?
- methodology: How rigorous and sound is the methodology described?
- impact: How significant could the potential impact be?

Then decide:
- verdict: Must be exactly "approved" if total_score >= 80, otherwise "rejected"

Return ONLY valid JSON - no markdown, no extra text:
{{
  "novelty": <0-25>,
  "credibility": <0-25>,
  "methodology": <0-25>,
  "impact": <0-25>,
  "total_score": <sum of the four, 0-100>,
  "decision": "approved" or "rejected"
}}"""


class BaseLLMProvider(ABC):
    @abstractmethod
    async def complete(self, prompt: str) -> str:
        ...


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def complete(self, prompt: str) -> str:
        import openai
        client = openai.AsyncOpenAI(api_key=self._api_key)
        resp = await client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


class GeminiProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model or "gemini-1.5-flash"

    async def complete(self, prompt: str) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(self._model)
        resp = await model.generate_content_async(
            prompt,
            generation_config={"temperature": 0.1, "max_output_tokens": 300},
        )
        return resp.text or ""


class OpenRouterProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model or "openai/gpt-4o-mini"

    async def complete(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model or "llama3"

    async def complete(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.1
                    }
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")


def _build_provider() -> BaseLLMProvider:
    settings = get_settings()
    provider = settings.llm_provider
    key = settings.active_llm_key
    model = settings.llm_model

    if provider == "openai":
        return OpenAIProvider(api_key=key, model=model)
    elif provider == "gemini":
        return GeminiProvider(api_key=key, model=model)
    elif provider == "openrouter":
        return OpenRouterProvider(api_key=key, model=model)
    elif provider == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


async def score_paper(
    title: str,
    abstract: str,
    authors: list[str],
    journal: str,
    citation_count: int,
    affiliations: list[str],
    funding_sources: list[str],
) -> AIReview:
    """
    Send paper to LLM and get a credibility score.
    Returns AIReview with sub-scores and total.
    """
    prompt = SCORING_PROMPT.format(
        title=title[:500],
        abstract=(abstract or "Not available")[:2000],
        authors=", ".join(authors[:10]) if authors else "Unknown",
        journal=journal or "Unknown",
        citation_count=citation_count,
        affiliations=", ".join(affiliations[:10]) if affiliations else "None specified",
        funding_sources=", ".join(funding_sources) if funding_sources else "None specified",
    )

    provider = _build_provider()

    try:
        raw = await provider.complete(prompt)
        return _parse_response(raw)
    except Exception as exc:
        logger.error("LLM scoring failed", error=str(exc), title=title[:60])
        return AIReview(
            novelty=0,
            credibility=0,
            methodology=0,
            impact=0,
            total_score=0,
            decision="rejected",
        )


def _parse_response(raw: str) -> AIReview:
    """Parse LLM JSON response, stripping any markdown fences."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise ValueError(f"Cannot parse LLM response: {text[:200]}")

    return AIReview(
        novelty=float(data.get("novelty", 0)),
        credibility=float(data.get("credibility", 0)),
        methodology=float(data.get("methodology", 0)),
        impact=float(data.get("impact", 0)),
        total_score=float(data.get("total_score", 0)),
        decision=str(data.get("decision", "rejected")).lower().strip(),
    )
