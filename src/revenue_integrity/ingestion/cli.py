from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..ontology import load_builtin_ontology, load_ontology_definition
from .adapter import load_adapter, run_adapter
from .profiling import profile_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile a deidentified provider bulk folder or execute an approved deterministic adapter."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    profile = subparsers.add_parser("profile", help="Create a bounded schema and sample profile")
    profile.add_argument("input_directory", type=Path)
    profile.add_argument("--output", type=Path, required=True)

    run = subparsers.add_parser("run", help="Transform bulk input into canonical source bundles")
    run.add_argument("input_directory", type=Path)
    run.add_argument("adapter", type=Path)
    run.add_argument("--ontology-definition", type=Path)
    run.add_argument("--output-directory", type=Path, required=True)
    run.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "profile":
            profile = profile_directory(args.input_directory)
            _atomic_json(args.output, profile.to_dict())
            return 0

        adapter = load_adapter(args.adapter)
        ontology = (
            load_ontology_definition(args.ontology_definition)
            if args.ontology_definition
            else load_builtin_ontology(adapter.ontology.ontology_id, adapter.ontology.version)
        )
        result = run_adapter(args.input_directory, adapter, ontology)
        output_directory: Path = args.output_directory
        output_directory.mkdir(parents=True, exist_ok=True)
        manifest: list[dict[str, Any]] = []
        for index, bundle in enumerate(result.source_bundles, start=1):
            case_id = str(bundle["case_id"])
            suffix = hashlib.sha256(case_id.encode()).hexdigest()[:12]
            filename = f"{index:06d}-{suffix}.source-bundle.json"
            _atomic_json(output_directory / filename, bundle)
            manifest.append({"case_id": case_id, "encounter_id": bundle["encounter_id"], "file": filename})
        _atomic_json(output_directory / "manifest.json", {"cases": manifest, "run": result.report.to_dict()})
        if args.report:
            _atomic_json(args.report, result.report.to_dict())
        return 0
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
