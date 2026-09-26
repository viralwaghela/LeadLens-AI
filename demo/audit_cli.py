"""Command line for demo/audit.py (used by scripts/audit_demo_deployment.py).

Same target safety as the seed/reset commands: the target comes ONLY from
LEADLENS_DEMO_DATABASE_URL, provider secrets in the shell are refused, and
the audit itself refuses to attack any database that is not a seeded,
demo-only one. It writes nothing except short-lived LLM-counter rows in a
1999 window, which it deletes again.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping

from demo.audit import run_audit, summarize
from pathlib import Path

from demo.operator_cli import (
    TARGET_ENV, _migration_state, _point_app_at, dotenv_problems, operator_environment_problems, parse_target,
)


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
    *,
    engine_factory: Callable[[str], object] | None = None,
    set_environment: Callable[[str], None] | None = None,
    migration_state=None,
    root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Attack the demo deployment and report PASS/FAIL.")
    parser.add_argument("--live-llm", action="store_true", help="also make one tiny real OpenAI call through the limiter")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    problems = dotenv_problems(root) + operator_environment_problems(env)
    if problems:
        for problem in problems:
            out(f"REFUSING: {problem}")
        return 2
    target = parse_target(env[TARGET_ENV])
    out(f"Auditing: host={target.host} database={target.database or '(default)'}")

    (set_environment or _point_app_at)(env[TARGET_ENV].strip())
    from core.db.session import make_engine

    engine = (engine_factory or make_engine)(env[TARGET_ENV].strip())
    owns = engine_factory is None
    try:
        results = run_audit(engine, out=out, live_llm=args.live_llm, migration_state=migration_state or _migration_state)
    finally:
        if owns:
            engine.dispose()
    ok, summary = summarize(results)
    out("")
    out(f"DEPLOYMENT AUDIT: {'PASS' if ok else 'FAIL'} ({summary})")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
