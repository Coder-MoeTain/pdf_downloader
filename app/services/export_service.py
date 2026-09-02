"""CSV, JSON, and Excel export of search results."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

from app.config import AppConfig, load_config
from app.models.paper import PaperRecord, PaperStatus
from app.models.search import SearchStats
from app.utils.filename import slugify
from app.utils.time import utc_now


EXPORT_COLUMNS = [
    "Rank",
    "Title",
    "Authors",
    "Year",
    "Journal",
    "Publisher",
    "DOI",
    "Citation Count",
    "Relevance Score",
    "Open Access",
    "PDF Status",
    "PDF Path",
    "Source",
    "URL",
]


def papers_to_rows(papers: list[PaperRecord]) -> list[dict]:
    rows = []
    for idx, paper in enumerate(papers, start=1):
        rows.append(
            {
                "Rank": idx,
                "Title": paper.title,
                "Authors": paper.author_names,
                "Year": paper.publication_year or "",
                "Journal": paper.journal or paper.conference or "",
                "Publisher": paper.publisher or "",
                "DOI": paper.doi or "",
                "Citation Count": paper.citation_count if paper.citation_count is not None else "",
                "Relevance Score": paper.relevance_score,
                "Open Access": bool(paper.open_access),
                "PDF Status": paper.status.value,
                "PDF Path": paper.extra.get("local_path", ""),
                "Source": paper.source_provider,
                "URL": paper.url or "",
            }
        )
    return rows


class ExportService:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or load_config()

    def export_all(self, papers: list[PaperRecord], stats: SearchStats) -> dict[str, Path]:
        folder = self.config.resolve_path(self.config.exports_dir)
        slug = slugify(stats.query or "search")
        stamp = date.today().isoformat()
        base = folder / f"{slug}_{stamp}"
        rows = papers_to_rows(papers)
        csv_path = Path(str(base) + ".csv")
        json_path = Path(str(base) + ".json")
        xlsx_path = Path(str(base) + ".xlsx")
        self._write_csv(csv_path, rows)
        self._write_json(json_path, papers, stats)
        self._write_xlsx(xlsx_path, papers, rows, stats)
        stats.report_path = str(xlsx_path)
        return {"csv": csv_path, "json": json_path, "xlsx": xlsx_path}

    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        pd.DataFrame(rows, columns=EXPORT_COLUMNS).to_csv(path, index=False, encoding="utf-8")

    def _write_json(self, path: Path, papers: list[PaperRecord], stats: SearchStats) -> None:
        payload = {
            "stats": stats.__dict__,
            "exported_at": utc_now().isoformat() + "Z",
            "papers": [
                {
                    **row,
                    "abstract": paper.abstract,
                    "pmid": paper.pmid,
                    "arxiv_id": paper.arxiv_id,
                    "license": paper.license,
                    "keywords": paper.keywords,
                }
                for row, paper in zip(papers_to_rows(papers), papers, strict=False)
            ],
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _write_xlsx(self, path: Path, papers: list[PaperRecord], rows: list[dict], stats: SearchStats) -> None:
        df = pd.DataFrame(rows, columns=EXPORT_COLUMNS)
        downloaded = df[df["PDF Status"] == PaperStatus.DOWNLOADED.value]
        oa = df[df["Open Access"] == True]  # noqa: E712
        paywalled = df[df["PDF Status"] == PaperStatus.PAYWALLED.value]
        failed = df[df["PDF Status"] == PaperStatus.FAILED.value]
        years = [p.publication_year for p in papers if p.publication_year]
        journals = Counter(p.journal for p in papers if p.journal)
        authors = Counter(a.name for p in papers for a in p.authors)
        publishers = Counter(p.publisher for p in papers if p.publisher)
        summary = pd.DataFrame(
            [
                ["Search topic", stats.query],
                ["Search date", utc_now().strftime("%Y-%m-%d %H:%M UTC")],
                ["Total discovered", stats.raw_records],
                ["Unique papers", stats.unique_papers],
                ["Open-access papers", stats.open_access_papers],
                ["PDFs downloaded", stats.pdfs_downloaded],
                ["Paywalled papers", stats.paywalled],
                ["Failed downloads", stats.failed_downloads],
                ["Average publication year", round(sum(years) / len(years), 1) if years else ""],
                ["Top journals", "; ".join(f"{n} ({c})" for n, c in journals.most_common(8))],
                ["Top authors", "; ".join(f"{n} ({c})" for n, c in authors.most_common(8))],
                ["Top publishers", "; ".join(f"{n} ({c})" for n, c in publishers.most_common(8))],
            ],
            columns=["Metric", "Value"],
        )
        sources = pd.DataFrame(
            [{"Source": name, "Results": count} for name, count in (stats.provider_counts or {}).items()]
        )
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            summary.to_excel(writer, sheet_name="Summary", index=False)
            downloaded.to_excel(writer, sheet_name="Downloaded", index=False)
            oa.to_excel(writer, sheet_name="Open Access", index=False)
            paywalled.to_excel(writer, sheet_name="Paywalled", index=False)
            failed.to_excel(writer, sheet_name="Failed", index=False)
            df.to_excel(writer, sheet_name="All Papers", index=False)
            sources.to_excel(writer, sheet_name="Sources", index=False)
