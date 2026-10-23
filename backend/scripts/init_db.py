"""Create foot8 tables in the database named by DATABASE_URL (backend/.env).

    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect  # noqa: E402

from app.db import DatabaseNotConfiguredError, init_db  # noqa: E402


def main() -> None:
    try:
        engine = init_db()
    except DatabaseNotConfiguredError as e:
        sys.exit(str(e))
    print("tables:", ", ".join(sorted(inspect(engine).get_table_names())))


if __name__ == "__main__":
    main()
