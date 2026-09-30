#!/usr/bin/env python3
"""Copy SQLite research.db + settings.db into MySQL.

Debian/Ubuntu has python3, not python. Prefer the project venv.
PM2 process name is researchpaper (see ecosystem.config.cjs).

    pm2 list
    pm2 stop researchpaper
    ./venv/bin/python scripts/migrate_sqlite_to_mysql.py --replace --yes
    pm2 restart researchpaper
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database.sqlite_to_mysql import main  # noqa: E402
from app.utils.logger import setup_logging  # noqa: E402


if __name__ == "__main__":
    setup_logging()
    raise SystemExit(main())
