"""Repo-wide pytest setup.

Offline safety net for the ``VA_CONFIG_DIR`` env-leak. The ``claude -p`` children
this project spawns — the Role-11 reasoner subprocess and the post-commit reviewer
that ``scripts/agent-review.sh`` launches — inherit the parent's environment. When
the parent runs under a real-model config (``VA_CONFIG_DIR=run-claude/config``), a
pytest suite the child spawns would load the real models instead of the stub
backends: glacial, flaky, and prone to piling into pytest "storms".

So at collection time we strip ``VA_CONFIG_DIR`` from ``os.environ``, forcing the
offline suite to always run the stub backends even under a leaked env. This is
read at call time by ``va.configuration._config_dir``, so removing it here (before
any test triggers a config load) is sufficient.

The golden harnesses legitimately need the real config
(``RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config``), so the strip is skipped when
``RUN_GOLDEN`` is set — see ``_should_strip``.

This is defense in depth alongside the reasoner's own
``_sanitized_child_env`` (``va.adapters.reasoner.claude_cli_inproc``): the adapter
stops the leak at the source (the child it spawns), and this stops it at the
offline suite (a child spawned with an already-leaked env).
"""
import os


def _should_strip(environ) -> bool:
    """Strip ``VA_CONFIG_DIR`` for offline runs, but PRESERVE it under RUN_GOLDEN.

    Pure so the golden-preservation decision is unit-testable without a GPU.
    """
    return not environ.get("RUN_GOLDEN")


if _should_strip(os.environ):
    os.environ.pop("VA_CONFIG_DIR", None)
