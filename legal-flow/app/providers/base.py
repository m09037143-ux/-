from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CandidateItem:
    """One thing a SourceProvider found. Everything here is internal-only until an
    editor writes an official document and an independent text — see ТЗ §3, §9."""

    title: str
    discovery_domain: str
    normalized_url: str
    original_fragment: str
    story_key: str
    content_hash: str | None = None
    label: str = "DEMO_FIXTURE"
    metadata: dict = field(default_factory=dict)


class SourceProvider(Protocol):
    name: str

    async def discover(
        self,
        *,
        domains: list[str],
        theme: str,
        limit: int,
        keywords: list[str] | None = None,
        stop_words: list[str] | None = None,
    ) -> list[CandidateItem]:
        ...
