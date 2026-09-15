# Changelog

## 2.0.0 — 2026-09-15

### Security
- Fail-closed authentication and explicit first-run setup with a one-time bootstrap token
- Removed default admin credentials
- Mandatory production `SESSION_SECRET`, secure cookies, CSRF, POST-only logout
- Trusted hosts and restricted proxy IPs
- SSRF protection with DNS resolution and manual redirects
- Login rate limiting and Argon2id password hashing with PBKDF2 migration

### Research / data
- OA-only search filters the persisted result set
- `OA_UNKNOWN` and `NO_OA_COPY_FOUND` distinct from `PAYWALLED`
- Canonical `paper_identifiers` uniqueness
- Alembic migrations
- Per-user notes, collections, and saved searches
- Crossref upstream rate-limit grouping and provider circuit breaker
- Citation exports: IEEE, Harvard, Vancouver, RIS, CSL-JSON, EndNote
- CFP estimated vs verified deadline labels
- SQLite FTS5 full-text index

### Operations
- `/health/live`, `/health/ready`, `/metrics`
- Backup/restore CLI
- GitHub Actions CI
