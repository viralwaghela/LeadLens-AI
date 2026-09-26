"""Attack the demo deployment (its real database and environment) and report PASS/FAIL.

    LEADLENS_DEMO_DATABASE_URL=... python scripts/audit_demo_deployment.py [--live-llm]

Run it from a shell whose environment mirrors the demo app's secrets (see
docs/V2_DEMO_ENVIRONMENT.md). Deliberately NO load_dotenv().
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.audit_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
