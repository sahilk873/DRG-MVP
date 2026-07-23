"""Authored readers: the agent can teach the system a new file format.

Formats the built-in readers don't cover (FHIR NDJSON, HL7 v2, X12, fixed-width, pipe-delimited …)
no longer require new hand-written parsers. The agent authors a `read(raw)` function that flattens a
raw document into a list of row objects; it is sandbox-verified and scored against golden samples,
then promoted as a frozen, hash-pinned `AuthoredReaderDefinition`. At execution time the promoted
reader runs **only in the sandbox** and only if its code hash matches what was scored — so a novel
format is supported without ever executing unverified model code on the deterministic data plane.
Rows produced flow into the existing profiler/adapter pipeline unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..audit import canonical_hash
from .knowledge import Exemplar, KnowledgeStore
from .promotion import EXECUTABLE_STATUSES, ArtifactScore
from .sandbox import SandboxLimits, run_sandboxed
from .self_eval import GoldenSample, score_artifact


def _rows_valid(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(row, dict) for row in value)


@dataclass(frozen=True, slots=True)
class AuthoredReaderDefinition:
    reader_id: str
    version: str
    status: str
    format_name: str
    code: str
    entrypoint: str = "read"

    def __post_init__(self) -> None:
        for name in ("reader_id", "version", "format_name", "code", "entrypoint"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"authored reader {name} must be a non-empty string")

    @property
    def code_hash(self) -> str:
        return canonical_hash({"code": self.code, "entrypoint": self.entrypoint})

    def to_dict(self) -> dict[str, Any]:
        return {
            "reader_id": self.reader_id, "version": self.version, "status": self.status,
            "format_name": self.format_name, "code": self.code, "entrypoint": self.entrypoint,
            "code_hash": self.code_hash,
        }


def score_reader(
    code: str,
    samples: Sequence[GoldenSample],
    *,
    entrypoint: str = "read",
    limits: SandboxLimits | None = None,
) -> ArtifactScore:
    """Score an authored reader: it must run, return a list of row objects, and match golden rows."""
    score, _ = score_artifact(code, samples, entrypoint=entrypoint, validator=_rows_valid, limits=limits)
    return score


def run_authored_reader(
    definition: AuthoredReaderDefinition,
    raw_documents: Sequence[str],
    *,
    expected_hash: str | None = None,
    limits: SandboxLimits | None = None,
) -> list[dict[str, Any]]:
    """Run a promoted reader over raw documents in the sandbox and return flattened rows. Fail-closed."""
    if definition.status not in EXECUTABLE_STATUSES:
        raise ValueError(f"authored reader status {definition.status!r} is not executable")
    if expected_hash is not None and definition.code_hash != expected_hash:
        raise ValueError("authored reader code hash does not match the promoted artifact")
    result = run_sandboxed(definition.code, list(raw_documents), entrypoint=definition.entrypoint, limits=limits)
    if not result.ok:
        raise ValueError(f"authored reader failed in sandbox: {result.error}")
    rows: list[dict[str, Any]] = []
    for index, row_result in enumerate(result.results):
        if not row_result.ok:
            raise ValueError(f"authored reader errored on document {index}: {row_result.error}")
        if not _rows_valid(row_result.value):
            raise ValueError("authored reader must return a list of row objects")
        rows.extend(row_result.value)
    return rows


def promote_reader(
    store: KnowledgeStore,
    code: str,
    *,
    reader_id: str,
    version: str,
    format_name: str,
    samples: Sequence[GoldenSample],
    status: str,
    entrypoint: str = "read",
    min_parse_rate: float = 1.0,
    min_conformance: float = 1.0,
    require_exact: bool = False,
    limits: SandboxLimits | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> tuple[bool, AuthoredReaderDefinition | None, ArtifactScore, str]:
    """Sandbox-score an authored reader and, if it passes, promote it as a governed definition."""
    score = score_reader(code, samples, entrypoint=entrypoint, limits=limits)
    if status not in EXECUTABLE_STATUSES:
        return False, None, score, f"status {status!r} is not executable"
    if not score.meets(min_parse_rate=min_parse_rate, min_conformance=min_conformance, require_exact=require_exact):
        return False, None, score, "reader score did not meet promotion thresholds"
    definition = AuthoredReaderDefinition(reader_id, version, status, format_name, code, entrypoint)
    store.record(Exemplar(
        exemplar_id=f"reader:{reader_id}@{version}",
        kind="reader",
        features=[f"format:{format_name}", *format_name.lower().replace("-", " ").split()],
        payload={"code": code, "entrypoint": entrypoint, "format": format_name, "code_hash": definition.code_hash},
        label="approved",
        provenance={**(provenance or {}), "score": score.to_dict(), "status": status},
    ))
    return True, definition, score, "promoted"
