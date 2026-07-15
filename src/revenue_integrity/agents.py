from __future__ import annotations

from typing import Any, Protocol

from .models import EncounterCase


class ExtractionAgent(Protocol):
    """An LLM/NLP implementation must return JSON matching the encounter schema."""

    def extract(self, source_bundle: dict[str, Any]) -> dict[str, Any]: ...


def accept_agent_output(payload: dict[str, Any]) -> EncounterCase:
    """Trust boundary: reject malformed, ungrounded, or dangling agent assertions."""
    return EncounterCase.from_dict(payload)

