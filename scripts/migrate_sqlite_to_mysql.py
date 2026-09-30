#!/usr/bin/env python3
"""Copy SQLite research.db + settings.db into MySQL.

Stop PM2 first so SQLite is not mid-write:

    pm2 stop research
    python scripts/migrate_sqlite_to_mysql.py --replace --yes
    pm2 start research
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
