<p align="center">
  <img src="docs/images/logo.png" alt="ResearchPaper Collector" width="120" height="120">
</p>

<h1 align="center">ResearchPaper Collector</h1>

<p align="center">
  <strong>Search trusted academic APIs, collect paper metadata, and download only legally available open-access PDFs.</strong>
</p>

<p align="center">
  Built for literature reviews in AI, cybersecurity, machine learning, satellite security, and intrusion detection — without paywall bypass.
</p>

<p align="center">
  <a href="https://github.com/Coder-MoeTain/pdf_downloader/stargazers"><img src="https://img.shields.io/github/stars/Coder-MoeTain/pdf_downloader?style=flat&color=1f6f8b" alt="Stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2f7d4a" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/FastAPI-dashboard-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/open%20access-legal%20PDFs%20only-7eb89a" alt="Open access only">
  <a href="COMPLIANCE.md"><img src="https://img.shields.io/badge/compliance-documented-10243d" alt="Compliance"></a>
</p>

<p align="center">
  <img src="docs/images/banner.png" alt="ResearchPaper Collector — open-access academic search and PDF library" width="100%">
</p>

<p align="center">
  <img src="docs/images/dashboard.png" alt="Dashboard with library KPIs, papers-by-year chart, and recent searches" width="100%">
</p>

<p align="center">
  <sub>Dashboard · live collection stats, year coverage, and recent topic searches</sub>
</p>

---

## What it does

ResearchPaper Collector talks to **official academic APIs**, merges and ranks results, finds **open-access full text** (including Unpaywall), then stores validated PDFs in a local library.

It does **not** scrape Google Scholar, ResearchGate, or Academia.edu. It does **not** bypass paywalls, CAPTCHAs, `robots.txt`, or subscription gates. Paywalled works keep their DOI and publisher URL for lawful follow-up. See [COMPLIANCE.md](COMPLIANCE.md).

## Features

- **Multi-source search** — OpenAlex, Crossref, Semantic Scholar, arXiv, PubMed, Europe PMC, CORE, DOAJ, NASA NTRS, plus IEEE / Springer / Elsevier / NASA ADS when keys are present
- **Query expansion** with configurable synonyms
- **DOI normalization** and fuzzy duplicate detection
- **Relevance ranking** (keyword by default; optional `sentence-transformers`)
- **Open-access detection** via provider metadata and Unpaywall
- **Validated PDF downloads** (`Content-Type`, `%PDF-` magic bytes, size limits, SHA-256)
- **Resume-safe downloads** with retries and status tracking
- **SQLite paper library**, CSV / JSON / XLSX reports, and per-topic `metadata.csv`
- **Interactive CLI** and an optional **FastAPI dashboard**
- **Optional local full-text index** with PyMuPDF
- **Scheduled topic updates** from `config.yaml`

## Screenshots

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/search.png" alt="Search academic sources">
      <p align="center"><sub>Search official APIs with year, source, and OA filters</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/library.png" alt="Paper library">
      <p align="center"><sub>Library with ratings, status chips, and legal PDF actions</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/images/statistics.png" alt="Collection analytics">
      <p align="center"><sub>Analytics: coverage, citations, journals, and authors</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/sources.png" alt="Academic sources">
      <p align="center"><sub>Enable, rate-limit, and key the academic providers</sub></p>
    </td>
  </tr>
</table>

## Architecture

```mermaid
flowchart TD
  UI["CLI / Dashboard"] --> S[SearchService]
  S --> Q[Query expansion]
  S --> P["Providers — async, isolated failures"]
  S --> M[Merge + dedup]
  S --> R[Relevance ranking]
  S --> OA[Unpaywall OA resolver]
  S --> D[PDF downloader + validator]
  S --> E["Export CSV / JSON / XLSX"]
  D --> DB[("SQLite  data/research.db")]
  D --> FS["research_library/  exports/  logs/"]
```

Providers share a common `ResearchProvider` interface (`search`, `get_paper`, `find_pdf`). Adding a source means implementing that interface and registering it in `app/providers/__init__.py`.

## Supported sources

| Source | API key | Notes |
| --- | --- | --- |
| OpenAlex | No | Polite pool via contact email |
| Crossref | No | Polite pool via contact email |
| Semantic Scholar | Optional | Higher rate limit with a key |
| arXiv | No | Preprint PDFs are public |
| PubMed / PMC | Optional NCBI key | PMC PDFs when a PMCID exists |
| Europe PMC | No | OA full-text URLs when provided |
| CORE | **Required** | Institutional-repository OA |
| DOAJ | No | Directory of Open Access Journals |
| IEEE Xplore | **Required** | Metadata; PDF only when the API marks OA |
| Springer Nature | **Required** | Metadata; PDF only for OA records |
| Elsevier / Scopus | **Required** | Metadata; no paywall bypass |
| NASA ADS | **Required** | Useful for space / satellite literature |
| NASA NTRS | No | NASA technical reports; public PDFs when listed |
| Unpaywall | Email required | OA PDF discovery, not a search index |

ResearchGate, Google Scholar, and Academia.edu have no public search APIs and prohibit automated access, so they are not connectors. Papers that also appear on those sites are still found through Crossref, OpenAlex, Unpaywall, and CORE when a DOI or repository copy exists.

MDPI, Frontiers, PLOS, ACM, Wiley, Nature, institutional repositories, NIST, and government reports are covered when they appear in Crossref / OpenAlex / Unpaywall OA metadata. The app never scrapes publisher HTML.

## Installation

Python **3.10+** is required.

```bash
git clone https://github.com/Coder-MoeTain/pdf_downloader.git
cd pdf_downloader

python -m venv venv
```

**Windows**

```bash
venv\Scripts\activate
```

**macOS / Linux**

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

Optional Google sign-in for the dashboard uses `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ADMIN_EMAILS`, and `SESSION_SECRET`. Settings and the source catalog can use MySQL when `MYSQL_HOST` is set.

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

## Web dashboard

```bash
uvicorn app.web:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

Pages: **Dashboard**, **Search**, **Library**, **Downloads**, **Sources**, **Statistics**, **Settings**.

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

Full rules: [COMPLIANCE.md](COMPLIANCE.md).

## Troubleshooting

| Problem | What to try |
| --- | --- |
| Zero results | Check network access; try a broader query; confirm providers with `python main.py providers` |
| Unpaywall skipped | Set `CONTACT_EMAIL` / `UNPAYWALL_EMAIL` to a real mailbox |
| CORE / IEEE / Springer unused | Add the matching API key to `.env` |
| NASA ADS unused | Create a free token at [NASA ADS API help](https://ui.adsabs.harvard.edu/help/api/) and store it as `NASA_ADS_TOKEN` |
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

Unit tests cover DOI/title normalization, deduplication, ranking, merge, filename sanitization, PDF validation, retries, rate limiting, database operations, and provider parsing with mocked payloads. External HTTP APIs are not called in CI.

## Roadmap

- Additional publisher connectors as official APIs allow
- Optional sentence-transformer ranking without a separate extra file
- Saved-search notifications
- Dedup against an existing Zotero / BibTeX library
- Packaging as `pip install researchpaper-collector`

## License

[MIT](LICENSE) © ResearchPaper Collector contributors

---

<p align="center">
  <img src="docs/images/favicon.svg" alt="" width="28" height="28">
</p>

<p align="center">
  <strong>Make with ❤️ by <a href="https://github.com/Coder-MoeTain">Coder-MoeTain</a></strong>
</p>
