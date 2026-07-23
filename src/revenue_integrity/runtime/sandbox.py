"""OS-level sandbox for running agent-authored code during the authoring phase.

The agent may write arbitrary Python (a `transform(row)` function) to parse/normalize/transform
data. We never `exec` that text in-process. Instead each run is a **fresh, isolated subprocess**:
`python -I -S` (ignore env + user site), an empty environment, a throwaway working directory, OS
resource limits (`RLIMIT_CPU/AS/FSIZE/NOFILE`), and a wall-clock kill. Input goes in as one JSON
document on stdin; exactly one JSON result comes back on stdout. A runaway loop, a memory bomb, or a
crash is contained and reported — it cannot hang or corrupt the host process.

Honesty about scope: this bounds CPU, memory, file size, open files, and wall time, and isolates the
interpreter and environment. It does **not** by itself guarantee network/syscall isolation — a
production deployment must additionally jail the worker (container / seccomp / `sandbox-exec`). That
hardening is orthogonal to this interface.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Sequence

try:  # resource limits are POSIX-only; the sandbox requires them.
    import resource
except ImportError:  # pragma: no cover - non-POSIX
    resource = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    cpu_seconds: int = 2
    memory_mb: int = 512
    wall_seconds: float = 5.0
    file_size_mb: int = 8
    open_files: int = 64

    def __post_init__(self) -> None:
        for name in ("cpu_seconds", "memory_mb", "file_size_mb", "open_files"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"sandbox limit {name} must be a positive integer")
        if self.wall_seconds <= 0:
            raise ValueError("wall_seconds must be positive")


@dataclass(frozen=True, slots=True)
class RowResult:
    ok: bool
    value: Any = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class SandboxResult:
    ok: bool                       # did the worker start and the code define the entrypoint
    results: tuple[RowResult, ...] = ()
    error: str = ""               # setup/compile/timeout error (whole-run failure)
    timed_out: bool = False


# Trusted worker executed inside the subprocess. The UNTRUSTED part is the agent code it `exec`s,
# which is contained by the OS limits applied before this runs.
_WORKER = r"""
import sys, json, traceback
try:
    data = json.load(sys.stdin)
    namespace = {}
    exec(data["code"], namespace)
    entrypoint = data.get("entrypoint", "transform")
    if entrypoint not in namespace or not callable(namespace[entrypoint]):
        print(json.dumps({"ok": False, "error": "code must define a callable " + entrypoint}))
        sys.exit(0)
    fn = namespace[entrypoint]
    results = []
    for row in data.get("inputs", []):
        try:
            value = fn(row)
            json.dumps(value)  # enforce JSON-serializable, deterministic output
            results.append({"ok": True, "value": value})
        except Exception:
            results.append({"ok": False, "error": traceback.format_exc(limit=2)})
    print(json.dumps({"ok": True, "results": results}))
except Exception:
    print(json.dumps({"ok": False, "error": traceback.format_exc(limit=2)}))
"""


def _apply_limits(limits: SandboxLimits):  # pragma: no cover - runs in child pre-exec
    def _try(rlimit: int, soft: int, hard: int) -> None:
        # Best-effort per limit: never raise out of preexec (a raise aborts the child spawn).
        try:
            resource.setrlimit(rlimit, (soft, hard))
        except (ValueError, OSError):
            pass

    def preexec() -> None:
        _try(resource.RLIMIT_CPU, limits.cpu_seconds, limits.cpu_seconds)
        file_size = limits.file_size_mb * 1024 * 1024
        _try(resource.RLIMIT_FSIZE, file_size, file_size)
        _try(resource.RLIMIT_NOFILE, limits.open_files, limits.open_files)
        # RLIMIT_AS (virtual address space) is only reliable on Linux; on macOS/arm64 Python
        # reserves multi-GB of virtual space, so a low cap prevents the interpreter from starting.
        # CPU limit + wall-clock timeout are the portable runaway backstops; hard memory isolation
        # in production should come from a container/cgroup.
        if sys.platform.startswith("linux"):
            memory = limits.memory_mb * 1024 * 1024
            _try(resource.RLIMIT_AS, memory, memory)
    return preexec


def run_sandboxed(
    code: str,
    inputs: Sequence[Any],
    *,
    entrypoint: str = "transform",
    limits: SandboxLimits | None = None,
) -> SandboxResult:
    """Run agent-authored ``code`` over ``inputs`` in an isolated subprocess and return the results."""
    if resource is None:  # pragma: no cover
        raise RuntimeError("the code sandbox requires a POSIX platform with resource limits")
    limits = limits or SandboxLimits()
    payload = json.dumps({"code": code, "inputs": list(inputs), "entrypoint": entrypoint})
    with tempfile.TemporaryDirectory(prefix="ri-sandbox-") as workdir:
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-c", _WORKER],
                input=payload,
                capture_output=True,
                text=True,
                cwd=workdir,
                env={},                     # empty environment
                preexec_fn=_apply_limits(limits),
                timeout=limits.wall_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(ok=False, error="sandbox wall-clock timeout exceeded", timed_out=True)

    if not completed.stdout.strip():
        detail = (completed.stderr or "no output").strip()[:500]
        return SandboxResult(ok=False, error=f"sandbox produced no result (limit hit or crash): {detail}")
    try:
        parsed = json.loads(completed.stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return SandboxResult(ok=False, error="sandbox returned malformed output")
    if not parsed.get("ok"):
        return SandboxResult(ok=False, error=str(parsed.get("error", "unknown sandbox error")))
    results = tuple(
        RowResult(ok=bool(item.get("ok")), value=item.get("value"), error=str(item.get("error", "")))
        for item in parsed.get("results", [])
    )
    return SandboxResult(ok=True, results=results)
