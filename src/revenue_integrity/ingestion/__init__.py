"""Governed bulk-ingestion discovery and deterministic adapter execution."""

from .adapter import AdapterRunResult, run_adapter
from .models import AdapterDefinition, IngestionPolicy
from .profiling import BulkProfile, profile_directory

__all__ = [
    "AdapterDefinition",
    "AdapterRunResult",
    "BulkProfile",
    "IngestionPolicy",
    "profile_directory",
    "run_adapter",
]
