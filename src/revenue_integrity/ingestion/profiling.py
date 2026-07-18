from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import IngestionPolicy, ResourceDefinition
from .readers import ArtifactRef, ReaderRegistry, default_reader_registry, inventory_directory


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    name: str
    inferred_types: tuple[str, ...]
    missing_count: int
    distinct_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inferred_types": list(self.inferred_types),
            "missing_count": self.missing_count,
            "distinct_count": self.distinct_count,
        }


@dataclass(frozen=True, slots=True)
class ArtifactProfile:
    artifact_id: str
    path: str
    format: str
    sheet: str | None
    size_bytes: int
    profiled_rows: int
    truncated: bool
    columns: tuple[ColumnProfile, ...]
    sample_rows: tuple[Mapping[str, Any], ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "path": self.path,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "profiled_rows": self.profiled_rows,
            "truncated": self.truncated,
            "columns": [column.to_dict() for column in self.columns],
            "sample_rows": [dict(row) for row in self.sample_rows],
        }
        if self.sheet is not None:
            result["sheet"] = self.sheet
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True, slots=True)
class BulkProfile:
    schema_fingerprint: str
    input_manifest_digest: str
    artifact_count: int
    total_bytes: int
    artifacts: tuple[ArtifactProfile, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_version": "1.0.0",
            "schema_fingerprint": self.schema_fingerprint,
            "input_manifest_digest": self.input_manifest_digest,
            "artifact_count": self.artifact_count,
            "total_bytes": self.total_bytes,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }


def profile_directory(
    input_directory: str | Path,
    policy: IngestionPolicy | None = None,
    registry: ReaderRegistry | None = None,
) -> BulkProfile:
    root = Path(input_directory).resolve(strict=True)
    resolved_policy = policy or IngestionPolicy()
    resolved_registry = registry or default_reader_registry()
    inventory = inventory_directory(root, resolved_policy)
    profiles = tuple(_profile_artifact(root, artifact, resolved_policy, resolved_registry) for artifact in inventory)
    file_inventory = _unique_files(inventory)
    schema_material = [
        {
            "artifact_id": profile.artifact_id,
            "format": profile.format,
            "columns": [
                {"name": column.name, "inferred_types": list(column.inferred_types)}
                for column in profile.columns
            ],
            "error": profile.error,
        }
        for profile in profiles
        if profile.format != "unsupported"
    ]
    manifest_material = [
        {
            "path": relative,
            "size_bytes": size,
            "sha256": _file_digest(root / relative),
        }
        for relative, size in file_inventory
    ]
    return BulkProfile(
        schema_fingerprint=_digest(schema_material),
        input_manifest_digest=_digest(manifest_material),
        artifact_count=len(profiles),
        total_bytes=sum(size for _, size in file_inventory),
        artifacts=profiles,
    )


def artifact_columns(profile: BulkProfile) -> dict[str, frozenset[str]]:
    return {
        artifact.artifact_id: frozenset(column.name for column in artifact.columns)
        for artifact in profile.artifacts
    }


def _profile_artifact(
    root: Path,
    artifact: ArtifactRef,
    policy: IngestionPolicy,
    registry: ReaderRegistry,
) -> ArtifactProfile:
    if artifact.format == "unsupported":
        return ArtifactProfile(
            artifact.artifact_id, artifact.path, artifact.format, artifact.sheet, artifact.size_bytes,
            0, False, (), (), "unsupported file format",
        )
    resource = ResourceDefinition(path=artifact.path, format=artifact.format, sheet=artifact.sheet)
    rows: list[Mapping[str, Any]] = []
    truncated = False
    try:
        iterator = registry.iter_rows(root, resource, policy)
        for row in iterator:
            if len(rows) >= policy.max_profile_rows_per_artifact:
                truncated = True
                break
            rows.append({key: value for key, value in row.items() if key != "_row_number"})
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return ArtifactProfile(
            artifact.artifact_id, artifact.path, artifact.format, artifact.sheet, artifact.size_bytes,
            len(rows), truncated, (), _bounded_samples(rows, policy), str(error),
        )

    column_names = sorted({key for row in rows for key in row})
    if len(column_names) > policy.max_profile_columns_per_artifact:
        return ArtifactProfile(
            artifact.artifact_id, artifact.path, artifact.format, artifact.sheet, artifact.size_bytes,
            len(rows), truncated, (), _bounded_samples(rows, policy),
            f"artifact exceeds max_profile_columns_per_artifact ({policy.max_profile_columns_per_artifact})",
        )
    columns = tuple(_profile_column(name, rows) for name in column_names)
    return ArtifactProfile(
        artifact.artifact_id,
        artifact.path,
        artifact.format,
        artifact.sheet,
        artifact.size_bytes,
        len(rows),
        truncated,
        columns,
        _bounded_samples(rows, policy),
    )


def _profile_column(name: str, rows: Iterable[Mapping[str, Any]]) -> ColumnProfile:
    missing = 0
    types: set[str] = set()
    distinct: set[str] = set()
    for row in rows:
        value = row.get(name)
        if value is None or value == "":
            missing += 1
            continue
        types.add(_primitive_type(value))
        if len(distinct) < 10_000:
            distinct.add(json.dumps(value, sort_keys=True, default=str))
    return ColumnProfile(name, tuple(sorted(types)) or ("null",), missing, len(distinct))


def _primitive_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "unknown"


def _unique_files(artifacts: tuple[ArtifactRef, ...]) -> list[tuple[str, int]]:
    result: dict[str, int] = {}
    for artifact in artifacts:
        result[artifact.path] = artifact.size_bytes
    return sorted(result.items())


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bounded_samples(rows: list[Mapping[str, Any]], policy: IngestionPolicy) -> tuple[Mapping[str, Any], ...]:
    samples: list[Mapping[str, Any]] = []
    used = 0
    for row in rows[:policy.sample_rows_per_artifact]:
        sample: dict[str, Any] = {}
        truncated = False
        for key, value in row.items():
            rendered = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
            if len(rendered) > policy.max_sample_value_characters:
                rendered = rendered[:policy.max_sample_value_characters] + "…[truncated]"
                value = rendered
                truncated = True
            cost = len(key) + len(rendered)
            if used + cost > policy.max_total_sample_characters_per_artifact:
                truncated = True
                break
            sample[key] = value
            used += cost
        if truncated:
            sample["_sample_truncated"] = True
        samples.append(sample)
        if used >= policy.max_total_sample_characters_per_artifact:
            break
    return tuple(samples)
