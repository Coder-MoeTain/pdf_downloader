"""Store ebook cover images from OA metadata or the first PDF page."""

from __future__ import annotations

from pathlib import Path

from app.models.paper import PaperRecord
from app.utils.filename import sanitize_component
from app.utils.http import AsyncHttpClient
from app.utils.logger import get_logger

logger = get_logger("app.cover")

IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def cover_dir(library_root: Path) -> Path:
    path = library_root / "covers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_pdf_cover(pdf_path: Path, dest: Path) -> bool:
    try:
        import fitz
    except ImportError:
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        try:
            if doc.page_count < 1:
                return False
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            dest.write_bytes(pix.tobytes("png"))
        finally:
            doc.close()
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        logger.warning("Cover render failed for %s: %s", pdf_path.name, exc)
        return False


async def download_remote_cover(client: AsyncHttpClient, url: str, dest: Path) -> Path | None:
    try:
        response, body = await client.get_bytes(url)
    except Exception as exc:
        logger.info("Cover download skipped for %s: %s", url, exc)
        return None
    if response.status_code >= 400 or not body:
        return None
    ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    suffix = IMAGE_TYPES.get(ctype)
    if suffix is None:
        if body[:8] == b"\x89PNG\r\n\x1a\n":
            suffix = ".png"
        elif body[:3] == b"\xff\xd8\xff":
            suffix = ".jpg"
        else:
            return None
    target = dest.with_suffix(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target if target.is_file() and target.stat().st_size > 0 else None


async def store_cover_for_paper(
    client: AsyncHttpClient,
    paper: PaperRecord,
    pdf_path: Path | None,
    library_root: Path,
    *,
    paper_id: int,
) -> Path | None:
    stem = sanitize_component(paper.title or f"ebook-{paper_id}", max_length=60, fallback=f"ebook-{paper_id}")
    dest = cover_dir(library_root) / f"{paper_id}-{stem}.png"
    if paper.cover_url:
        saved = await download_remote_cover(client, paper.cover_url, dest)
        if saved is not None:
            return saved
    if pdf_path and pdf_path.is_file():
        if render_pdf_cover(pdf_path, dest):
            return dest
    return None
