# Security Policy

## Supported versions

The `main` branch of Cyber Scholar is the supported release line.

## Reporting a vulnerability

Email the maintainers privately. Do not open a public issue for exploitable flaws until a fix is available.

Please include:

- affected version / commit
- reproduction steps
- impact (auth bypass, SSRF, CSRF, data leak)

We will acknowledge receipt and coordinate a fix.

## Threat model

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Authentication

- Zero users never implies anonymous admin.
- First administrator is created only with a one-time bootstrap token from server logs, or `python main.py create-admin`.
- Production refuses to start without a strong `SESSION_SECRET`.
- Session cookies are HttpOnly, SameSite=Lax, and Secure in production.
- Logout is POST-only with CSRF validation.

## CSRF

State-changing browser requests require a session CSRF token via form field or `X-CSRF-Token`.

## SSRF

Outbound HTTP resolves DNS and rejects private, loopback, link-local, multicast, and cloud metadata addresses. Redirects are followed manually and re-validated.

## File handling

PDFs are streamed to `.part` files, checked for size, magic bytes, and SHA-256, then renamed atomically. Only legally available open-access URLs are fetched.

## API keys

Keys are stored in environment / settings DB, masked in the UI, and redacted from logs. They must never appear in git.

## Open-access compliance

Cyber Scholar does not bypass paywalls, CAPTCHAs, authentication walls, or publisher access controls.

## Dependency management

CI runs `pip-audit` and `bandit`. Review upgrades before production deploy.
