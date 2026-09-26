import hashlib

from app.providers.base import CandidateItem

# Вымышленные учебные примеры (ТЗ §8) — ни один из этих сайтов фактически не
# анализируется. Заголовки и домены совпадают с редакционным прототипом, чтобы
# отличие DEMO_FIXTURE от настоящего сбора было очевидно на глаз.
_HEADLINES = [
    ("Ответственность руководителя по долгам компании: что необходимо учитывать", "garant.ru"),
    ("Взыскание убытков с бывшего директора: обстоятельства спора", "pravo.ru"),
    ("Оспаривание сделок при банкротстве: основания для проверки", "garant.ru"),
]


class FixtureSourceProvider:
    """DEMO_FIXTURE-only provider: никогда не обращается в сеть. Стабильный вывод
    делает его пригодным для тестов антидублей и идемпотентности job'ов."""

    name = "fixture"

    async def discover(self, *, domains: list[str], theme: str, limit: int) -> list[CandidateItem]:
        allowed = set(domains)
        items: list[CandidateItem] = []
        for idx, (title, domain) in enumerate(_HEADLINES):
            if domain not in allowed:
                continue
            if len(items) >= limit:
                break
            story_key = f"fixture:{domain}:{idx}:{theme.strip().lower()}"
            url = f"https://{domain}/demo-fixture/{idx}"
            fragment = (
                f"Учебный пример исходного фрагмента по теме «{title}». "
                "Это не текст с сайта-источника: сбор не подключён."
            )
            items.append(
                CandidateItem(
                    title=title,
                    discovery_domain=domain,
                    normalized_url=url,
                    original_fragment=fragment,
                    story_key=story_key,
                    content_hash=hashlib.sha256(fragment.encode("utf-8")).hexdigest(),
                    label="DEMO_FIXTURE",
                )
            )
        return items
