# ResearchPaper Collector

Search trusted academic APIs, collect paper metadata, identify **legally available** open-access PDFs, and download them into a local research library.

The tool is designed for literature reviews on topics such as artificial intelligence, cybersecurity, web application security, machine learning, reinforcement learning, autonomous penetration testing, satellite cybersecurity, and intrusion detection systems.

It uses official APIs and publisher/repository open-access links. It does **not** bypass paywalls, authentication, CAPTCHAs, `robots.txt` rules, or subscription access controls. See [COMPLIANCE.md](COMPLIANCE.md).

## Features

- Multi-source academic search (OpenAlex, Crossref, Semantic Scholar, arXiv, PubMed, Europe PMC, CORE, DOAJ, plus IEEE / Springer / Elsevier / NASA ADS when API keys are present)
- Query expansion with configurable synonyms
- DOI normalization and fuzzy duplicate detection
- Configurable relevance ranking (keyword by default; optional `sentence-transformers`)
- Open-access detection via provider metadata and Unpaywall
- Validated PDF downloads (`Content-Type`, `%PDF-` magic bytes, size limits, SHA-256)
- Resume-safe downloads with retry counts and status tracking
- SQLite library, CSV/JSON/XLSX reports, and per-topic `metadata.csv`
- Interactive CLI and optional FastAPI dashboard
- Optional local full-text index with PyMuPDF
- Scheduled topic updates from `config.yaml`

## Architecture

```text
CLI / Dashboard
        │
        ▼
  SearchService
        │
        ├── Query expansion
        ├── Providers (async, isolated failures)
        ├── Merge + dedup
        ├── Relevance ranking
        ├── Open-access resolver (Unpaywall)
        ├── PDF downloader + validator
        └── Export (CSV / JSON / XLSX)
        │
        ▼
   SQLite  data/research.db
   Files   research_library/  exports/  logs/
```

Providers share a common `ResearchProvider` interface (`search`, `get_paper`, `find_pdf`). Adding a source means implementing that interface and registering it in `app/providers/__init__.py`.

## Supported sources

| Source | API key | Notes |
| --- | --- | --- |
| OpenAlex | No | Polite pool via contact email |
| Crossref | No | Polite pool via contact email |
| Semantic Scholar | Optional | Higher rate limit with key |
| arXiv | No | Preprint PDFs are public |
| PubMed / PMC | Optional NCBI key | PMC PDFs when a PMCID exists |
| Europe PMC | No | OA full-text URLs when provided |
| CORE | **Required** | Institutional-repository OA |
| DOAJ | No | Directory of Open Access Journals |
| IEEE Xplore | **Required** | Metadata; PDF only when the API marks OA |
| Springer Nature | **Required** | Metadata; PDF only for OA records |
| Elsevier / Scopus | **Required** | Metadata; no paywall bypass |
| NASA ADS | **Required** | Useful for space / satellite literature |
| Unpaywall | Email required | OA PDF discovery, not a search index |

MDPI, Frontiers, PLOS, ACM, Wiley, Nature, institutional repositories, NIST, and government reports are covered when they appear in Crossref/OpenAlex/Unpaywall OA metadata. The app never scrapes publisher HTML.

## Installation

Python **3.10+** is required.

```bash
git clone <repository>
cd research-paper-collector

python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

macOS / Linux:

```bash
source venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## API key configuration

Edit `.env` (never commit real keys):

```env
CONTACT_EMAIL=you@university.edu
UNPAYWALL_EMAIL=you@university.edu
SEMANTIC_SCHOLAR_API_KEY=
CORE_API_KEY=
SPRINGER_API_KEY=
ELSEVIER_API_KEY=
IEEE_API_KEY=
NCBI_API_KEY=
NASA_ADS_TOKEN=
```

`CONTACT_EMAIL` should be a real address so Crossref, OpenAlex, and Unpaywall can place you in their polite pools. Ranking weights, provider rate limits, and synonym lists live in `config.yaml`.

## Search examples

```bash
python main.py search "reinforcement learning penetration testing"
```

```bash
python main.py search "web application vulnerability detection" --year-from 2022 --year-to 2026 --max-results 100
```

```bash
python main.py search "machine learning web application security" --year-from 2023 --open-access-only --max-results 200 --sort relevance
```

Search without downloading:

```bash
python main.py search "machine learning IDS" --no-download
```

Only open access:

```bash
python main.py search "satellite cybersecurity" --open-access-only
```

Filters: `--year-from`, `--year-to`, `--authors`, `--journal`, `--publisher`, `--source`, `--open-access-only`, `--max-results`, `--min-citations`, `--sort relevance|citations|newest`, `--download-limit`, `--max-file-size 50MB`.

## Other commands

```bash
python main.py                 # interactive menu
python main.py download
python main.py list
python main.py stats
python main.py retry
python main.py export
python main.py providers
python main.py library-search "SQL injection"
python main.py index-pdfs
python main.py fulltext-search "reinforcement learning reward function"
python main.py update-library
```

`update-library` reads saved topics from `config.yaml` and can be scheduled with cron, Windows Task Scheduler, or a systemd timer.

## Database

SQLite file: `data/research.db`

Tables: `papers`, `authors`, `paper_authors`, `search_queries`, `search_results`, `downloads`, `providers`, `paper_fulltext`.

Paper status values: `FOUND`, `OA_AVAILABLE`, `DOWNLOADING`, `DOWNLOADED`, `PAYWALLED`, `NO_PDF`, `FAILED`, `DUPLICATE`, `SKIPPED`.

Paywalled works keep DOI, title, and publisher URL for lawful follow-up (library access, author request). They are never downloaded.

## Folder structure

```text
research_library/
├── reinforcement_learning/
│   ├── 2026/
│   ├── 2025/
│   └── metadata.csv
exports/
├── web_security_2026-09-02.csv
├── web_security_2026-09-02.json
└── web_security_2026-09-02.xlsx
logs/
├── app.log
├── download.log
└── error.log
```

PDF names look like `2025_Smith_Autonomous_Penetration_Testing_10.1234_abc.pdf`.

Excel workbooks include sheets: Summary, Downloaded, Open Access, Paywalled, Failed, All Papers, Sources.

## Legal / open-access policy

1. Prefer official APIs.
2. Download only public OA publisher PDFs, arXiv, PMC, Europe PMC, CORE, DOAJ, repository deposits, and government publications.
3. Respect rate limits, `Retry-After`, and `robots.txt`.
4. Do not rotate IPs, defeat CAPTCHAs, or use publisher credentials to scrape closed content.
5. Validate every file as a real PDF before storing it.

## Web dashboard (optional)

```bash
uvicorn app.web:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

Pages: Dashboard, Search, Library, Downloads, Sources, Statistics, Settings.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| Zero results | Check network access; try a broader query; confirm providers with `python main.py providers` |
| Unpaywall skipped | Set `CONTACT_EMAIL` / `UNPAYWALL_EMAIL` to a real mailbox |
| CORE / IEEE / Springer unused | Add the matching API key to `.env` |
| 429 errors | Lower `requests_per_second` in `config.yaml`; the client already backs off |
| HTML saved instead of PDF | The validator rejects non-PDF bodies; check `logs/download.log` |
| Semantic ranking | Install `sentence-transformers` and set `ranking.semantic.enabled: true` |

## Development

```bash
pip install -r requirements.txt
python main.py providers
pytest
```

Type hints are used throughout. Provider failures are isolated and do not abort a search.

## Testing

```bash
pytest
```

Unit tests cover DOI/title normalization, deduplication, ranking, merge, filename sanitization, PDF validation, retries, rate limiting, database operations, and provider parsing with mocked payloads. External HTTP APIs are not called in CI.

## Roadmap

- Additional publisher connectors as official APIs allow
- Optional sentence-transformer ranking without a separate extra file
- Saved-search notifications
- Dedup against an existing Zotero / BibTeX library
- Packaging as `pip install researchpaper-collector`
#   p d f _ d o w n l o a d e r  
 