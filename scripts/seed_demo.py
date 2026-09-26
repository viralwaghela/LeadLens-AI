"""Seed the public demo tenant. Dry run unless --apply.

Idempotent: does nothing if the demo tenant is already seeded.

Reads the target ONLY from LEADLENS_DEMO_DATABASE_URL (never DATABASE_URL, never
.env). See demo/operator_cli.py for every safety check, and
docs/V2_DEMO_ENVIRONMENT.md for the runbook.

    LEADLENS_DEMO_DATABASE_URL=postgresql+psycopg://... python scripts/seed_demo.py
    LEADLENS_DEMO_DATABASE_URL=... python scripts/seed_demo.py --apply --confirm-host <demo-db-host>
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Deliberately NO load_dotenv(): a .env file may hold production values.
from demo.operator_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main("seed", sys.argv[1:]))
