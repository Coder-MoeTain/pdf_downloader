"""Build APA, MLA, Chicago, and BibTeX citations from paper metadata."""

from __future__ import annotations

import json
import re
from typing import Any

from app.utils.doi import doi_url, normalize_doi

_NAME_PARTICLES = {"da", "de", "del", "della", "der", "di", "la", "le", "van", "von"}
_WORD = re.compile(r"[^A-Za-z0-9]+")


def paper_citations(paper: Any) -> dict[str, str]:
    """Return copyable citation strings for library Cite previews."""
    names = _author_names(paper)
    title = (getattr(paper, "title", None) or "Untitled").strip()
    year = getattr(paper, "publication_year", None)
    year_text = str(year) if year else "n.d."
    venue = (getattr(paper, "journal", None) or getattr(paper, "conference", None) or "").strip()
    volume = (getattr(paper, "volume", None) or "").strip()
    issue = (getattr(paper, "issue", None) or "").strip()
    pages = (getattr(paper, "pages", None) or "").strip()
    publisher = (getattr(paper, "publisher", None) or "").strip()
    link = _citation_link(paper)
    return {
        "apa": _apa(names, year_text, title, venue, volume, issue, pages, publisher, link),
        "mla": _mla(names, year_text, title, venue, volume, issue, pages, publisher, link),
        "chicago": _chicago(names, year_text, title, venue, volume, issue, pages, publisher, link),
        "ieee": _ieee(names, year_text, title, venue, volume, issue, pages, link),
        "harvard": _harvard(names, year_text, title, venue, volume, issue, pages, publisher, link),
        "vancouver": _vancouver(names, year_text, title, venue, volume, issue, pages, link),
        "bibtex": _bibtex(names, year, title, venue, volume, issue, pages, publisher, paper, link),
        "ris": _ris(names, year, title, venue, volume, issue, pages, publisher, paper, link),
        "csl_json": _csl_json(names, year, title, venue, volume, issue, pages, publisher, paper, link),
        "endnote": _endnote(names, year, title, venue, volume, issue, pages, publisher, paper, link),
    }


def _author_names(paper: Any) -> list[str]:
    authors = list(getattr(paper, "authors", None) or [])
    if not authors:
        return []
    first = authors[0]
    if hasattr(first, "author"):
        links = sorted(authors, key=lambda item: getattr(item, "position", 0) or 0)
        return [
            link.author.name.strip()
            for link in links
            if getattr(link, "author", None) and (link.author.name or "").strip()
        ]
    if hasattr(first, "name"):
        return [item.name.strip() for item in authors if (item.name or "").strip()]
    return [str(item).strip() for item in authors if str(item).strip()]


def _split_name(name: str) -> tuple[str, str]:
    text = " ".join(name.split())
    if "," in text:
        last, given = text.split(",", 1)
        return last.strip(), given.strip()
    parts = text.split()
    if len(parts) == 1:
        return parts[0], ""
    if len(parts) >= 3 and parts[-2].lower() in _NAME_PARTICLES:
        return " ".join(parts[-2:]), " ".join(parts[:-2])
    return parts[-1], " ".join(parts[:-1])


def _initials(given: str) -> str:
    chunks = [chunk for chunk in given.replace(".", " ").split() if chunk]
    out: list[str] = []
    for chunk in chunks:
        bits = [bit for bit in chunk.split("-") if bit]
        out.append("-".join(f"{bit[0].upper()}." for bit in bits))
    return " ".join(out)


def _apa_name(name: str) -> str:
    last, given = _split_name(name)
    initials = _initials(given)
    return f"{last}, {initials}".rstrip(", ")


def _apa_authors(names: list[str]) -> str:
    if not names:
        return ""
    formatted = [_apa_name(name) for name in names]
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    if len(formatted) <= 20:
        return f"{', '.join(formatted[:-1])}, & {formatted[-1]}"
    return f"{', '.join(formatted[:19])}, ... {formatted[-1]}"


def _mla_authors(names: list[str]) -> str:
    if not names:
        return ""
    last, given = _split_name(names[0])
    first = f"{last}, {given}".rstrip(", ") if given else last
    if len(names) == 1:
        return first
    if len(names) == 2:
        second_last, second_given = _split_name(names[1])
        second = f"{second_given} {second_last}".strip()
        return f"{first}, and {second}"
    return f"{first}, et al."


def _chicago_authors(names: list[str]) -> str:
    if not names:
        return ""
    last, given = _split_name(names[0])
    first = f"{last}, {given}".rstrip(", ") if given else last
    if len(names) == 1:
        return first
    rest = []
    for name in names[1:]:
        last_name, given_name = _split_name(name)
        rest.append(f"{given_name} {last_name}".strip())
    if len(names) == 2:
        return f"{first}, and {rest[0]}"
    return f"{first}, {', '.join(rest[:-1])}, and {rest[-1]}"


def _end_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text if text[-1] in ".!?:" else f"{text}."


def _citation_link(paper: Any) -> str:
    link = doi_url(getattr(paper, "doi", None))
    if link:
        return link
    arxiv_id = (getattr(paper, "arxiv_id", None) or "").strip()
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    return (getattr(paper, "url", None) or "").strip()


def _join_parts(parts: list[str]) -> str:
    return " ".join(part for part in parts if part).strip()


def _apa(
    names: list[str],
    year_text: str,
    title: str,
    venue: str,
    volume: str,
    issue: str,
    pages: str,
    publisher: str,
    link: str,
) -> str:
    authors = _apa_authors(names)
    who = f"{authors} " if authors else ""
    vol = volume
    if volume and issue:
        vol = f"{volume}({issue})"
    elif issue and not volume:
        vol = f"({issue})"
    locator = ", ".join(bit for bit in (vol, pages) if bit)
    source = " ".join(bit for bit in (venue, locator) if bit)
    parts = [
        f"{who}({year_text})." if who or year_text else "",
        _end_sentence(title),
        _end_sentence(source),
        _end_sentence(publisher) if publisher and not venue else "",
        link,
    ]
    return _join_parts(parts)


def _mla(
    names: list[str],
    year_text: str,
    title: str,
    venue: str,
    volume: str,
    issue: str,
    pages: str,
    publisher: str,
    link: str,
) -> str:
    authors = _mla_authors(names)
    quoted = f'"{_end_sentence(title)}"'
    bits = [
        _end_sentence(authors) if authors else "",
        quoted,
        _end_sentence(venue) if venue else "",
    ]
    details = []
    if volume:
        details.append(f"vol. {volume}")
    if issue:
        details.append(f"no. {issue}")
    if year_text:
        details.append(year_text if year_text != "n.d." else "n.d.")
    if pages:
        details.append(f"pp. {pages}" if "-" in pages or "–" in pages else f"p. {pages}")
    if publisher and not venue:
        details.append(publisher)
    if details:
        bits.append(_end_sentence(", ".join(details)))
    if link:
        bits.append(link)
    return _join_parts(bits)


def _chicago(
    names: list[str],
    year_text: str,
    title: str,
    venue: str,
    volume: str,
    issue: str,
    pages: str,
    publisher: str,
    link: str,
) -> str:
    authors = _chicago_authors(names)
    quoted = f'"{_end_sentence(title)}"'
    year_bit = year_text if year_text != "n.d." else "n.d."
    if venue:
        loc = venue
        if volume:
            loc = f"{venue} {volume}"
        if issue:
            loc = f"{loc}, no. {issue}"
        loc = f"{loc} ({year_bit})"
        if pages:
            loc = f"{loc}: {pages}"
        parts = [
            _end_sentence(authors) if authors else "",
            quoted,
            _end_sentence(loc),
            link,
        ]
        return _join_parts(parts)
    parts = [
        _end_sentence(authors) if authors else "",
        quoted,
        _end_sentence(publisher) if publisher else "",
        _end_sentence(year_bit),
        link,
    ]
    return _join_parts(parts)


def _bib_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("$", "\\$")
        .replace("#", "\\#")
    )


def _cite_key(names: list[str], year: int | None, title: str) -> str:
    last = _WORD.sub("", _split_name(names[0])[0]) if names else "anon"
    word = next((bit for bit in _WORD.sub(" ", title).split() if bit), "paper")
    year_part = str(year) if year else "nd"
    return f"{(last or 'anon').lower()}{year_part}{word.lower()}"


def _bibtex(
    names: list[str],
    year: int | None,
    title: str,
    venue: str,
    volume: str,
    issue: str,
    pages: str,
    publisher: str,
    paper: Any,
    link: str,
) -> str:
    conference = (getattr(paper, "conference", None) or "").strip()
    arxiv_id = (getattr(paper, "arxiv_id", None) or "").strip()
    if conference and not getattr(paper, "journal", None):
        entry = "inproceedings"
    elif arxiv_id and not venue:
        entry = "misc"
    else:
        entry = "article"
    bib_authors = " and ".join(
        f"{last}, {given}".rstrip(", ") if given else last for last, given in (_split_name(name) for name in names)
    )
    fields: list[tuple[str, str]] = [("title", title)]
    if bib_authors:
        fields.append(("author", bib_authors))
    if year:
        fields.append(("year", str(year)))
    if venue:
        fields.append(("booktitle" if entry == "inproceedings" else "journal", venue))
    if volume:
        fields.append(("volume", volume))
    if issue:
        fields.append(("number", issue))
    if pages:
        fields.append(("pages", pages))
    if publisher:
        fields.append(("publisher", publisher))
    doi = normalize_doi(getattr(paper, "doi", None))
    if doi:
        fields.append(("doi", doi))
    if arxiv_id:
        fields.append(("eprint", arxiv_id))
        fields.append(("archivePrefix", "arXiv"))
    if link:
        fields.append(("url", link))
    body = ",\n".join(f"  {key} = {{{_bib_escape(value)}}}" for key, value in fields)
    return f"@{entry}{{{_cite_key(names, year, title)},\n{body}\n}}"


def _ieee(names: list[str], year: str, title: str, venue: str, volume: str, issue: str, pages: str, link: str) -> str:
    if not names:
        authors = "Unknown"
    elif len(names) == 1:
        last, given = _split_name(names[0])
        authors = f"{_initials(given)} {last}".strip()
    else:
        formatted = []
        for name in names[:6]:
            last, given = _split_name(name)
            formatted.append(f"{_initials(given)} {last}".strip())
        authors = ", ".join(formatted)
        if len(names) > 6:
            authors += ", et al."
    parts = [f'{authors}, "{title},"']
    if venue:
        detail = f" {venue}"
        if volume:
            detail += f", vol. {volume}"
        if issue:
            detail += f", no. {issue}"
        if pages:
            detail += f", pp. {pages}"
        detail += f", {year}."
        parts.append(detail.strip())
    else:
        parts.append(f" {year}.")
    if link:
        parts.append(f" {link}")
    return "".join(parts).strip()


def _harvard(
    names: list[str], year: str, title: str, venue: str, volume: str, issue: str, pages: str, publisher: str, link: str
) -> str:
    if not names:
        authors = "Unknown"
    elif len(names) == 1:
        last, given = _split_name(names[0])
        authors = f"{last}, {_initials(given)}".rstrip(", ")
    else:
        last, given = _split_name(names[0])
        authors = f"{last}, {_initials(given)} et al.".rstrip()
    core = f"{authors} ({year}) '{title}'"
    if venue:
        core += f", {venue}"
        if volume:
            core += f", {volume}"
            if issue:
                core += f"({issue})"
        if pages:
            core += f", pp. {pages}"
    elif publisher:
        core += f", {publisher}"
    if link:
        core += f". {link}"
    else:
        core += "."
    return core


def _vancouver(
    names: list[str], year: str, title: str, venue: str, volume: str, issue: str, pages: str, link: str
) -> str:
    formatted = []
    for name in names[:6]:
        last, given = _split_name(name)
        initials = "".join(ch for ch in _initials(given) if ch.isalpha()).upper()
        formatted.append(f"{last} {initials}".strip())
    if len(names) > 6:
        formatted.append("et al")
    authors = ", ".join(formatted) or "Unknown"
    out = f"{authors}. {title}. "
    if venue:
        out += f"{venue}. {year}"
        if volume:
            out += f";{volume}"
            if issue:
                out += f"({issue})"
        if pages:
            out += f":{pages}"
        out += "."
    else:
        out += f"{year}."
    if link:
        out += f" Available from: {link}"
    return out


def _ris(
    names: list[str],
    year,
    title: str,
    venue: str,
    volume: str,
    issue: str,
    pages: str,
    publisher: str,
    paper: Any,
    link: str,
) -> str:
    lines = ["TY  - JOUR", f"TI  - {title}"]
    for name in names:
        last, given = _split_name(name)
        lines.append(f"AU  - {last}, {given}".rstrip(", "))
    if year:
        lines.append(f"PY  - {year}")
    if venue:
        lines.append(f"JO  - {venue}")
    if volume:
        lines.append(f"VL  - {volume}")
    if issue:
        lines.append(f"IS  - {issue}")
    if pages:
        start, _, end = pages.partition("-")
        lines.append(f"SP  - {start.strip()}")
        if end.strip():
            lines.append(f"EP  - {end.strip()}")
    if publisher:
        lines.append(f"PB  - {publisher}")
    doi = normalize_doi(getattr(paper, "doi", None))
    if doi:
        lines.append(f"DO  - {doi}")
    if link:
        lines.append(f"UR  - {link}")
    lines.append("ER  - ")
    return "\n".join(lines)


def _csl_json(
    names: list[str],
    year,
    title: str,
    venue: str,
    volume: str,
    issue: str,
    pages: str,
    publisher: str,
    paper: Any,
    link: str,
) -> str:
    authors = []
    for name in names:
        last, given = _split_name(name)
        item = {"family": last}
        if given:
            item["given"] = given
        authors.append(item)
    payload: dict[str, Any] = {
        "type": "article-journal" if venue else "article",
        "title": title,
        "author": authors,
    }
    if year:
        payload["issued"] = {"date-parts": [[int(year) if str(year).isdigit() else year]]}
    if venue:
        payload["container-title"] = venue
    if volume:
        payload["volume"] = volume
    if issue:
        payload["issue"] = issue
    if pages:
        payload["page"] = pages
    if publisher:
        payload["publisher"] = publisher
    doi = normalize_doi(getattr(paper, "doi", None))
    if doi:
        payload["DOI"] = doi
    if link:
        payload["URL"] = link
    orcid = None
    first = (getattr(paper, "authors", None) or [None])[0]
    if first is not None:
        orcid = getattr(first, "orcid", None) or getattr(getattr(first, "author", None), "orcid", None)
    if orcid and authors:
        authors[0]["ORCID"] = orcid
    return json.dumps(payload, ensure_ascii=False)


def _endnote(
    names: list[str],
    year,
    title: str,
    venue: str,
    volume: str,
    issue: str,
    pages: str,
    publisher: str,
    paper: Any,
    link: str,
) -> str:
    lines = ["%0 Journal Article", f"%T {title}"]
    for name in names:
        last, given = _split_name(name)
        lines.append(f"%A {last}, {given}".rstrip(", "))
    if year:
        lines.append(f"%D {year}")
    if venue:
        lines.append(f"%J {venue}")
    if volume:
        lines.append(f"%V {volume}")
    if issue:
        lines.append(f"%N {issue}")
    if pages:
        lines.append(f"%P {pages}")
    if publisher:
        lines.append(f"%I {publisher}")
    doi = normalize_doi(getattr(paper, "doi", None))
    if doi:
        lines.append(f"%R {doi}")
    if link:
        lines.append(f"%U {link}")
    return "\n".join(lines)
