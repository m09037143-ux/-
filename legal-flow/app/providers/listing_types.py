from dataclasses import dataclass


@dataclass
class ParsedListItem:
    """Одна запись списка новостей (из HTML, RSS/Atom или sitemap) до отбора по теме."""

    url: str
    title: str
    lead: str = ""
    published: str = ""
