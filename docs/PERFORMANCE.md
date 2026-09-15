# Performance notes

Measurements from local pytest and targeted checks on Windows (Python 3.12). These are approximate.

| Scenario | Observation |
| --- | --- |
| Authenticated HTML page | Typically < 200 ms in TestClient (includes SQLite init per test) |
| `/api/download-progress` during PDF fetch | Event-loop wait stayed under 0.35 s (see `tests/test_pdf_route_nonblocking.py`) |
| Provider timeout | Individual providers are bounded by `provider_timeout_seconds`; the wave uses a semaphore |
| Failed provider | Search continues with partial results; circuit opens after repeated 5xx/429 |
| Full-text query | FTS5 MATCH when the virtual table exists; LIKE fallback otherwise |

## Memory

Sentence-transformers (`all-MiniLM-L6-v2`) must not load unless semantic ranking is enabled. Large XLSX exports should stream; avoid retaining extra copies of `PaperRecord` lists after persist.

Optional RSS inspection:

```
python -c "import psutil,os; print(psutil.Process(os.getpid()).memory_info().rss)"
```

PM2 `max_memory_restart` is a safety net, not a substitute for bounded queues and streaming downloads.
