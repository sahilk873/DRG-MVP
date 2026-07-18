"""Explicit model/data-plane controls for production deployments."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ModelAccessPolicy:
    provider_allowlist: tuple[str, ...]
    require_zero_retention: bool = True
    require_deidentified_input: bool = True
    allow_prompt_telemetry: bool = False
    max_input_characters: int = 250_000

    def __post_init__(self) -> None:
        if not self.provider_allowlist or any(not item.strip() for item in self.provider_allowlist):
            raise ValueError("model policy requires a non-empty provider allowlist")
        if isinstance(self.max_input_characters, bool) or self.max_input_characters <= 0:
            raise ValueError("max_input_characters must be positive")

    def authorize(self, *, provider: str, deidentified: bool, input_characters: int, zero_retention: bool, telemetry_enabled: bool) -> None:
        if provider not in self.provider_allowlist:
            raise PermissionError("model provider is not allowlisted")
        if self.require_deidentified_input and not deidentified:
            raise PermissionError("model access requires verified deidentified input")
        if self.require_zero_retention and not zero_retention:
            raise PermissionError("model access requires zero-retention configuration")
        if not self.allow_prompt_telemetry and telemetry_enabled:
            raise PermissionError("prompt telemetry is disabled by policy")
        if isinstance(input_characters, bool) or input_characters < 0 or input_characters > self.max_input_characters:
            raise PermissionError("input exceeds the configured model context policy")


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    derived_artifact_days: int = 30
    raw_input_retained: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.derived_artifact_days, bool) or self.derived_artifact_days < 0:
            raise ValueError("derived_artifact_days must be non-negative")

    def validate_artifact(self, artifact: Mapping[str, Any]) -> None:
        if not self.raw_input_retained and artifact.get("contains_raw_input") is True:
            raise PermissionError("retention policy forbids raw clinical input in derived artifacts")
