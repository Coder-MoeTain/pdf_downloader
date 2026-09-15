# Threat model

Cyber Scholar is a self-hosted research workstation. The primary assets are the SQLite library, settings (including API keys), user accounts, and downloaded PDFs.

## Threats and mitigations

| Threat | Mitigation |
| --- | --- |
| Unauthorized admin access | Fail-closed auth, first-run bootstrap token, no default passwords |
| Session theft | HttpOnly cookies, production Secure flag, session rotation after login |
| CSRF | Synchronizer token on POST/PUT/PATCH/DELETE |
| SSRF / malicious redirect | DNS + IP validation, manual redirects, blocked metadata IPs |
| Hostile PDF | Magic-byte and size checks, no execution of PDF content |
| Path traversal | `safe_join` for library paths; backup restore rejects `..` members |
| API-key leakage | Secret redaction, masked settings fields, no keys in query strings |
| Malicious provider response | Parser bounds, OA URL allow-list, SSRF on PDF fetches |
| Database corruption | WAL mode, transactional upserts, identifier uniqueness |
| Duplicate records | `paper_identifiers` unique `(scheme, normalized_value)` |
| Request flooding | Login rate limits, provider rate-limit groups, circuit breaker |

## Trust boundaries

- Browser users are untrusted until authenticated.
- Provider APIs are untrusted input.
- Reverse proxies are trusted only when listed in `TRUSTED_PROXY_IPS`.
- Host headers must match `ALLOWED_HOSTS`.
