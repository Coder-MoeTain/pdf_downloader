"""Build Crossref-filter provider classes from the batch source specs."""

from __future__ import annotations

from app.database.batch_sources import BATCH_CROSSREF_SOURCES
from app.providers.more import _CrossrefFilterProvider


def _class_name(slug: str) -> str:
    return "".join(part.title() for part in slug.replace("-", "_").split("_")) + "Provider"


def _build_provider(spec: dict[str, object]) -> type[_CrossrefFilterProvider]:
    attrs = {
        "name": str(spec["slug"]),
        "display_name": str(spec["display_name"]),
        "container": str(spec.get("container") or ""),
        "prefix": str(spec.get("prefix") or ""),
        "work_type": str(spec.get("work_type") or ""),
        "assume_oa": bool(spec.get("assume_oa")),
        "publisher_name": str(spec.get("publisher_name") or spec["display_name"]),
    }
    return type(_class_name(str(spec["slug"])), (_CrossrefFilterProvider,), attrs)


BATCH_PROVIDER_CLASSES: list[type[_CrossrefFilterProvider]] = [
    _build_provider(spec) for spec in BATCH_CROSSREF_SOURCES
]
