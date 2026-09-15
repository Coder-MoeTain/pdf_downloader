"""Open-access status helpers. Never treat “no PDF found” as paywalled."""

from __future__ import annotations

from app.models.paper import PaperRecord, PaperStatus

CONFIRMED_OA_STATUSES = {
    PaperStatus.OA_AVAILABLE,
    PaperStatus.DOWNLOADING,
    PaperStatus.DOWNLOADED,
}

RESTRICTED_EVIDENCE = frozenset({"closed", "restricted", "paywalled", "subscription"})


def is_confirmed_oa(paper: PaperRecord) -> bool:
    if paper.status in CONFIRMED_OA_STATUSES:
        return True
    if paper.open_access is True and (paper.pdf_url or "").strip():
        return True
    return False


def has_restricted_access_evidence(paper: PaperRecord) -> bool:
    extra = paper.extra if isinstance(getattr(paper, "extra", None), dict) else {}
    access = str(extra.get("access") or extra.get("oa_status") or "").strip().lower()
    if access in RESTRICTED_EVIDENCE:
        return True
    if extra.get("is_oa") is False and extra.get("oa_status") in {"closed", "bronze", "hybrid"}:
        return False
    return False
