"""Trace utility (Traceability plan, TR.1/TR.3) — a leaf observability sink.

Call-based, NOT inherited: the pipeline calls `trace(role, action, summary, …)`;
nothing extends or implements anything here, and this module imports nothing from
the pipeline (dependency is one-way: `pipeline -> runtime/trace`).

Gated by the `VA_TRACE` env var, **default OFF** — when off, `traced_run` yields a
None tracer, the contextvar stays None, and every `trace()` call short-circuits, so
a normal run does literally nothing and writes no files.

On-disk format is a **single human-readable `.trace` file** (one per run): a header,
then one block per event. Small structured details are an indented JSON line; big
verbatim payloads (the reasoner input/output, tracebacks) are written as REAL-newline
blocks delimited by `----- <field> -----` … `----- end <field> -----`, so you can just
open the file and read it top to bottom. It is still lightly parseable (`load_events`)
for tooling, and written incrementally (append-per-event) so a mid-run crash still
leaves a usable partial trace.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import time
import traceback as _tb
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# The active run for the current context (thread/task). None = tracing inactive.
_current: contextvars.ContextVar[Optional["Tracer"]] = contextvars.ContextVar(
    "va_tracer", default=None
)

_OFF = {"", "0", "false", "off", "no"}
_EXT = ".trace"

# Detail keys whose value is always written as a verbatim block (real newlines).
_BLOB_KEYS = {"reasoner_input", "reasoner_output", "traceback"}
_MARK = {"error": "✗ ", "warn": "! "}
_UNMARK = {"✗": "error", "!": "warn"}

_HEADER_RE = re.compile(r"^# trace · (\S+) · (\S+) · (\S+)")
_EVENT_RE = re.compile(r"^\[(\d+)\] (?:(✗|!) )?(\S+?)/(\S+?)(?: · (.*))?$")
_BLOCK_OPEN_RE = re.compile(r"^----- (\w+) -----$")


def tracing_enabled() -> bool:
    return os.environ.get("VA_TRACE", "0").strip().lower() not in _OFF


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    """Readable, sortable, unique: 20260617-043105-a1b2."""
    return _utc_now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def traces_dir(workdir: str | Path) -> Path:
    return Path(workdir) / "traces"


def _is_blob(key: str, value: Any) -> bool:
    """A string detail that should be its own real-newline block (vs an inline JSON value)."""
    return isinstance(value, str) and (key in _BLOB_KEYS or "\n" in value or len(value) > 200)


def _format_event(seq: int, role: str, action: str, summary: str,
                  level: str, details: Dict[str, Any]) -> str:
    head = f"[{seq:02d}] {_MARK.get(level, '')}{role}/{action}"
    if summary:
        head += f" · {summary}"
    out = [head]

    blobs = {k: v for k, v in (details or {}).items() if _is_blob(k, v)}
    small = {k: v for k, v in (details or {}).items() if k not in blobs}
    if small:
        js = json.dumps(small, default=str, ensure_ascii=False)
        if len(js) > 100:   # long structured detail -> pretty, indented, multi-line
            js = json.dumps(small, default=str, ensure_ascii=False, indent=2)
            out += ["     " + ln for ln in js.splitlines()]
        else:
            out.append("     " + js)

    text = "\n".join(out) + "\n"
    for k, v in blobs.items():
        text += f"----- {k} -----\n{v}\n----- end {k} -----\n"
    return text


class Tracer:
    """Appends readable event blocks for one run. Best-effort: methods NEVER raise."""

    def __init__(self, run_id: str, kind: str, path: str | Path):
        self.run_id = run_id
        self.kind = kind
        self.path = Path(path)
        self._seq = 0
        self._fh = None
        self._t0 = time.perf_counter()

    def event(self, role: str, action: str, summary: str = "", *,
              level: str = "info", **details: Any) -> None:
        try:
            if self._fh is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.path.open("a", encoding="utf-8")
                self._fh.write(f"# trace · {self.kind} · {self.run_id} · "
                               f"{_utc_now().isoformat(timespec='seconds')}\n")
                self._fh.write("# legend: [NN]=event  [EN]=evidence item  "
                               "✗=error  !=warn\n\n")
            self._seq += 1
            self._fh.write(_format_event(self._seq, role, action, summary, level, details))
            self._fh.flush()
        except Exception:
            pass  # tracing must never break the pipeline

    def close(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass
        finally:
            self._fh = None


@contextmanager
def traced_run(kind: str, workdir: str | Path, *, enabled: Optional[bool] = None,
               run_id: Optional[str] = None) -> Iterator[Optional[Tracer]]:
    """Bracket one ingest/query/ask run. No-op (yields None) when tracing is off.

    `enabled` overrides the `VA_TRACE` env check (used by tests). The run is
    established INSIDE the calling thread, so the contextvar is correct even when
    the web job queue runs the work on a worker thread.
    """
    on = tracing_enabled() if enabled is None else enabled
    if not on:
        yield None
        return
    rid = run_id or new_run_id()
    tr = Tracer(rid, kind, traces_dir(workdir) / f"{kind}-{rid}{_EXT}")
    token = _current.set(tr)
    try:
        tr.event("pipeline", "start", kind)
        yield tr
    except Exception as e:  # the run itself blew up — record before unwinding
        tr.event("pipeline", "aborted", f"{type(e).__name__}: {e}",
                 level="error", traceback=_tb.format_exc())
        raise
    finally:
        tr.event("pipeline", "end", kind,
                 elapsed_ms=int((time.perf_counter() - tr._t0) * 1000))
        tr.close()
        _current.reset(token)


def current_tracer() -> Optional["Tracer"]:
    return _current.get()


def current_run_id() -> Optional[str]:
    tr = _current.get()
    return tr.run_id if tr is not None else None


def trace(role: str, action: str, summary: str = "", *,
          level: str = "info", **details: Any) -> None:
    """Record one event to the active run. No-op when no run is active."""
    tr = _current.get()
    if tr is not None:
        tr.event(role, action, summary, level=level, **details)


@contextmanager
def trace_stage(role: str, **start_details: Any) -> Iterator[None]:
    """Time a block; on exception emit a level=error event (with traceback) and
    RE-RAISE — the caller's own try/except decides best-effort vs abort. On
    success the caller emits the summary event (it knows the counts)."""
    t0 = time.perf_counter()
    try:
        yield
    except Exception as e:
        trace(role, "failed", f"{type(e).__name__}: {e}", level="error",
              elapsed_ms=int((time.perf_counter() - t0) * 1000),
              error=str(e), traceback=_tb.format_exc(), **start_details)
        raise


# --- reading / rendering (used by `va trace`) -------------------------------

def load_events(path: str | Path) -> List[dict]:
    """Parse a readable `.trace` file back into event dicts (for tooling/tests)."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    run_id = kind = ts = None
    events: List[dict] = []
    cur: Optional[dict] = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        h = _HEADER_RE.match(line)
        if h:
            kind, run_id, ts = h.group(1), h.group(2), h.group(3)
            i += 1
            continue
        e = _EVENT_RE.match(line)
        if e:
            if cur:
                events.append(cur)
            cur = {"run_id": run_id, "kind": kind, "ts": ts, "seq": int(e.group(1)),
                   "level": _UNMARK.get(e.group(2), "info"),
                   "role": e.group(3), "action": e.group(4),
                   "summary": e.group(5) or "", "details": {}}
            i += 1
            continue
        b = _BLOCK_OPEN_RE.match(line)
        if b and not line.startswith("----- end") and cur is not None:
            field = b.group(1)
            buf: List[str] = []
            i += 1
            while i < n and lines[i] != f"----- end {field} -----":
                buf.append(lines[i])
                i += 1
            i += 1  # skip the closing marker
            cur["details"][field] = "\n".join(buf)
            continue
        if line.strip().startswith("{") and cur is not None:
            buf = [line]
            i += 1
            while True:                       # accumulate a (possibly multi-line) JSON detail
                try:
                    cur["details"].update(json.loads("\n".join(buf)))
                    break
                except Exception:
                    if i < n and lines[i][:1] in (" ", "\t"):
                        buf.append(lines[i])
                        i += 1
                    else:
                        break
            continue
        i += 1
    if cur:
        events.append(cur)
    return events


def list_runs(workdir: str | Path) -> List[dict]:
    """Summaries of all trace files in a workdir, newest first."""
    d = traces_dir(workdir)
    runs: List[dict] = []
    if not d.is_dir():
        return runs
    for p in sorted(d.glob(f"*{_EXT}"), reverse=True):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        h = _HEADER_RE.search(text)
        if not h:
            continue
        ev = [ln for ln in text.splitlines() if re.match(r"^\[\d+\]", ln)]
        runs.append({
            "run_id": h.group(2), "kind": h.group(1), "ts": h.group(3),
            "events": len(ev),
            "warnings": sum(1 for ln in ev if re.match(r"^\[\d+\] (✗|!) ", ln)),
            "path": str(p),
        })
    return runs


def find_run(workdir: str | Path, run_id: str) -> Optional[Path]:
    d = traces_dir(workdir)
    if not d.is_dir():
        return None
    matches = sorted(d.glob(f"*{run_id}*{_EXT}"))
    return matches[0] if matches else None


def render_trace(path: str | Path) -> str:
    """The file IS the human view — return it, with a degradations summary on top
    if any stage warned/failed (so issues surface without scrolling)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return f"(empty or unreadable trace: {path})"
    bad = [ln for ln in text.splitlines() if re.match(r"^\[\d+\] (✗|!) ", ln)]
    if bad:
        banner = (f"⚠ {len(bad)} degradation(s):\n"
                  + "\n".join("  " + ln for ln in bad) + "\n\n")
        return banner + text
    return text


def prune_traces(workdir: str | Path, *, keep: Optional[int] = None,
                 older_than_days: Optional[float] = None, clear_all: bool = False) -> int:
    """Delete trace files; return how many were removed. `keep` retains the N
    newest; `older_than_days` removes files older than that; `clear_all` removes all."""
    d = traces_dir(workdir)
    if not d.is_dir():
        return 0
    files = sorted(d.glob(f"*{_EXT}"), reverse=True)   # newest first
    doomed: set = set()
    if clear_all:
        doomed = set(files)
    else:
        if keep is not None:
            doomed |= set(files[keep:])
        if older_than_days is not None:
            cutoff = time.time() - older_than_days * 86400
            doomed |= {p for p in files if p.stat().st_mtime < cutoff}
    removed = 0
    for p in doomed:
        try:
            p.unlink()
            removed += 1
        except Exception:
            pass
    return removed
