# Legal and Open-Access Compliance

ResearchPaper Collector searches official academic APIs and downloads **only legally
available full-text PDFs**. It is an open-access discovery tool, not a paywall
bypass.

## What this software will do

1. Query official APIs (Crossref, OpenAlex, Semantic Scholar, arXiv, PubMed,
   PubMed Central, Europe PMC, CORE, DOAJ, NASA NTRS, OpenAIRE, HAL, Zenodo,
   PLOS, ERIC, OSTI, eLife, OpenReview, USGS, FAO, WHO, CERN CDS, NDL Search,
   and publisher APIs when you supply credentials).
2. Detect open-access copies using provider metadata and Unpaywall.
3. Download PDFs that publishers, repositories, or authors have made public
   (gold/hybrid OA publisher PDFs, arXiv, PubMed Central, Europe PMC, CORE,
   DOAJ, NASA NTRS, HAL, Zenodo, PLOS, eLife, PeerJ, SciPost, OpenReview,
   institutional repositories, and public government repositories).
4. Record paywalled items with DOI, title, and publisher URL for later lawful
   access (library subscription, author request, etc.).

## What this software will not do

1. Circumvent authentication, SSO, campus VPN gates, or publisher login walls.
2. Defeat CAPTCHAs or other bot-prevention systems.
3. Rotate IP addresses, use open proxies, or otherwise evade rate limits.
4. Scrape HTML search result pages when an official API exists.
5. Retrieve content from sites whose terms or `robots.txt` prohibit automated
   access (including ResearchGate, Google Scholar, and Academia.edu).
6. Bypass subscription, institutional, or individual access restrictions.
7. Execute downloaded files or run arbitrary commands from paper content.

## Rules of operation

| Rule | Implementation |
| --- | --- |
| Prefer APIs | All search connectors call documented HTTP APIs. |
| Rate limits | Per-provider requests/second, 429/`Retry-After`, exponential backoff with jitter. |
| robots.txt | Optional (on by default) check before PDF download. |
| HTTPS | Prefer HTTPS; reject unsafe or malformed URLs. |
| Size and timeout | Configurable download timeout, redirect cap, and maximum file size. |
| Validation | PDFs must have HTTP success, plausible `Content-Type`, `%PDF-` magic bytes, and a minimum size. |
| Credentials | API keys live in `.env` and are never hard-coded or logged. |

## Paywalled papers

If a work is not legally available as open access, the application stores:

- `status = PAYWALLED`
- DOI
- publisher URL
- title

No attempt is made to obtain the publisher PDF.

## User responsibilities

- Provide a real contact email for polite API pools and Unpaywall.
- Honor publisher and API terms of use for any keys you add.
- Use downloaded PDFs in accordance with their licenses (CC-BY, publisher OA
  terms, repository deposit licenses, etc.).
- Do not point this tool at sources you are not allowed to access.

## Reporting issues

If a connector appears to retrieve restricted content, treat it as a bug: stop
using that provider and report it. The intended behavior is always
API-first, open-access-only downloads.
