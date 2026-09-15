# Database migrations

Cyber Scholar uses Alembic for the SQLite research database. Application settings may live in a separate MySQL/MariaDB database (`academic_sources`, `app_settings`) and are created by SQLAlchemy `create_all` plus guarded `ALTER TABLE` helpers in the settings store.

## Upgrade

```bash
alembic upgrade head
```

## Downgrade one revision

```bash
alembic downgrade -1
```

Existing installations remain upgradeable: `init_db()` still runs `create_all` and additive `PRAGMA`/`ALTER TABLE` for older files, then attempts `alembic upgrade head` outside the test environment.

Always take a backup first:

```bash
python main.py backup
```
