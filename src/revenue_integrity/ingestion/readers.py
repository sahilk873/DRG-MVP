from __future__ import annotations

import csv
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping
from xml.etree import ElementTree

from .models import IngestionPolicy, ResourceDefinition


_SUPPORTED_EXTENSIONS = {".csv": "csv", ".json": "json", ".jsonl": "jsonl", ".xlsx": "xlsx"}
_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    path: str
    format: str
    size_bytes: int
    sheet: str | None = None

    @property
    def artifact_id(self) -> str:
        return f"{self.path}#{self.sheet}" if self.sheet is not None else self.path


RowReader = Callable[[Path, ResourceDefinition], Iterator[dict[str, Any]]]


class ReaderRegistry:
    """Explicit extension point for deterministic source readers."""

    def __init__(self) -> None:
        self._readers: dict[str, RowReader] = {}

    def register(self, format_name: str, reader: RowReader) -> None:
        if not format_name or format_name in self._readers:
            raise ValueError(f"reader already registered or invalid: {format_name!r}")
        self._readers[format_name] = reader

    def iter_rows(
        self,
        root: Path,
        resource: ResourceDefinition,
        policy: IngestionPolicy,
    ) -> Iterator[dict[str, Any]]:
        path = resolve_source_path(root, resource.path, policy)
        try:
            reader = self._readers[resource.format]
        except KeyError as error:
            raise ValueError(f"no reader registered for {resource.format}") from error
        if resource.format == "xlsx":
            _validate_xlsx_archive(path, policy)
        for row_number, row in enumerate(reader(path, resource), start=1):
            if row_number > policy.max_runtime_rows_per_resource:
                raise ValueError(
                    f"resource {resource.path} exceeds max_runtime_rows_per_resource "
                    f"({policy.max_runtime_rows_per_resource})"
                )
            if not isinstance(row, Mapping):
                raise ValueError(f"resource {resource.path} row {row_number} is not an object")
            normalized = {str(key): value for key, value in row.items()}
            normalized["_row_number"] = row_number
            yield normalized


def default_reader_registry() -> ReaderRegistry:
    registry = ReaderRegistry()
    registry.register("csv", _read_csv)
    registry.register("json", _read_json)
    registry.register("jsonl", _read_jsonl)
    registry.register("xlsx", _read_xlsx)
    return registry


def inventory_directory(root: Path, policy: IngestionPolicy) -> tuple[ArtifactRef, ...]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"bulk input is not a directory: {root}")
    files: list[Path] = []
    total_bytes = 0
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"symbolic links are not permitted in bulk input: {candidate.relative_to(root)}")
        if not candidate.is_file():
            continue
        files.append(candidate)
        if len(files) > policy.max_files:
            raise ValueError(f"bulk input exceeds max_files ({policy.max_files})")
        size = candidate.stat().st_size
        if size > policy.max_file_bytes:
            raise ValueError(f"input file exceeds max_file_bytes: {candidate.relative_to(root)}")
        total_bytes += size
        if total_bytes > policy.max_total_bytes:
            raise ValueError(f"bulk input exceeds max_total_bytes ({policy.max_total_bytes})")

    artifacts: list[ArtifactRef] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        format_name = _SUPPORTED_EXTENSIONS.get(path.suffix.lower(), "unsupported")
        if format_name == "xlsx":
            _validate_xlsx_archive(path, policy)
            sheets = xlsx_sheet_names(path)
            if not sheets:
                raise ValueError(f"workbook contains no visible worksheets: {relative}")
            artifacts.extend(ArtifactRef(relative, format_name, path.stat().st_size, sheet) for sheet in sheets)
        else:
            artifacts.append(ArtifactRef(relative, format_name, path.stat().st_size))
    return tuple(artifacts)


def resolve_source_path(root: Path, relative_path: str, policy: IngestionPolicy) -> Path:
    root = root.resolve(strict=True)
    candidate = root.joinpath(relative_path)
    if candidate.is_symlink():
        raise ValueError(f"symbolic links are not permitted: {relative_path}")
    path = candidate.resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"resource escapes bulk input root: {relative_path}") from error
    if not path.is_file():
        raise ValueError(f"resource is not a file: {relative_path}")
    if path.stat().st_size > policy.max_file_bytes:
        raise ValueError(f"resource exceeds max_file_bytes: {relative_path}")
    return path


def xlsx_sheet_names(path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(path) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        return tuple(
            sheet.attrib["name"]
            for sheet in workbook.findall(f".//{{{_SPREADSHEET_NS}}}sheet")
            if sheet.attrib.get("state", "visible") == "visible"
        )


def _read_csv(path: Path, resource: ResourceDefinition) -> Iterator[dict[str, Any]]:
    del resource
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return
        _validate_headers(reader.fieldnames, path.name)
        yield from reader


def _read_json(path: Path, resource: ResourceDefinition) -> Iterator[dict[str, Any]]:
    del resource
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"JSON resource must contain an object or array of objects: {path.name}")
        yield dict(row)


def _read_jsonl(path: Path, resource: ResourceDefinition) -> Iterator[dict[str, Any]]:
    del resource
    with path.open("r", encoding="utf-8-sig") as handle:
        for physical_line, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"JSONL line {physical_line} must be an object: {path.name}")
            yield dict(row)


def _read_xlsx(path: Path, resource: ResourceDefinition) -> Iterator[dict[str, Any]]:
    if resource.sheet is None:
        raise ValueError("xlsx resource requires a sheet")
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_path = _xlsx_sheet_path(archive, resource.sheet)
        headers: list[str] | None = None
        for cells in _xlsx_rows(archive, sheet_path, shared_strings):
            if headers is None:
                headers = [str(value).strip() if value is not None else "" for value in cells]
                _validate_headers(headers, f"{path.name}#{resource.sheet}")
                continue
            padded = cells + [None] * max(0, len(headers) - len(cells))
            yield {header: padded[index] for index, header in enumerate(headers)}


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{{{_SPREADSHEET_NS}}}t")) for item in root]


def _xlsx_sheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationship_id: str | None = None
    for sheet in workbook.findall(f".//{{{_SPREADSHEET_NS}}}sheet"):
        if sheet.attrib.get("name") == sheet_name:
            relationship_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
            break
    if relationship_id is None:
        raise ValueError(f"unknown workbook sheet: {sheet_name}")
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship"):
        if relation.attrib.get("Id") == relationship_id:
            target = relation.attrib["Target"].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError(f"workbook sheet relationship is missing: {sheet_name}")


def _xlsx_rows(archive: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> Iterable[list[Any]]:
    root = ElementTree.fromstring(archive.read(sheet_path))
    for row in root.findall(f".//{{{_SPREADSHEET_NS}}}sheetData/{{{_SPREADSHEET_NS}}}row"):
        values: list[Any] = []
        for cell in row.findall(f"{{{_SPREADSHEET_NS}}}c"):
            reference = cell.attrib.get("r", "A1")
            column = _column_index(reference)
            values.extend([None] * max(0, column - len(values)))
            values.append(_xlsx_cell_value(cell, shared_strings))
        yield values


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if letters is None:
        raise ValueError(f"invalid spreadsheet cell reference: {reference}")
    index = 0
    for character in letters.group(0):
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> Any:
    type_name = cell.attrib.get("t")
    if type_name == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{_SPREADSHEET_NS}}}t"))
    value_node = cell.find(f"{{{_SPREADSHEET_NS}}}v")
    if value_node is None or value_node.text is None:
        return None
    raw = value_node.text
    if type_name == "s":
        return shared_strings[int(raw)]
    if type_name == "b":
        return raw == "1"
    if type_name in {"str", "e"}:
        return raw
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def _validate_headers(headers: list[str], artifact: str) -> None:
    if not headers or any(not header for header in headers):
        raise ValueError(f"artifact has empty headers: {artifact}")
    if len(headers) != len(set(headers)):
        raise ValueError(f"artifact has duplicate headers: {artifact}")
    if "_row_number" in headers:
        raise ValueError(f"artifact uses reserved header _row_number: {artifact}")


def _validate_xlsx_archive(path: Path, policy: IngestionPolicy) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > policy.max_archive_entries:
            raise ValueError(f"workbook exceeds max_archive_entries: {path.name}")
        expanded = 0
        names: set[str] = set()
        for info in entries:
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or info.filename in names:
                raise ValueError(f"workbook contains an unsafe or duplicate member: {path.name}")
            names.add(info.filename)
            if info.flag_bits & 0x1:
                raise ValueError(f"encrypted workbooks are not supported: {path.name}")
            expanded += info.file_size
            if expanded > policy.max_archive_expanded_bytes:
                raise ValueError(f"workbook exceeds max_archive_expanded_bytes: {path.name}")
            if info.file_size and (
                info.compress_size == 0
                or info.file_size / info.compress_size > policy.max_archive_compression_ratio
            ):
                raise ValueError(f"workbook member exceeds max_archive_compression_ratio: {path.name}")
