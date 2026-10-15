"""Проверка гипотезы на лендинге: заявки на ранний доступ и простая воронка (просмотр → кнопка → форма → заявка).

Заявка — это явный интерес пользователя; метрика конверсии = уникальные заявки / уникальные посетители лендинга.
События анонимны: случайный идентификатор браузера, без IP, cookie и персональных данных."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EarlyAccessRequest, ProductEvent
from app.schemas.marketing import EarlyAccessIn, EarlyAccessOut, FunnelOut, LeadOut

THANKS = "Спасибо! Заявка принята. Мы напишем на указанный адрес, когда откроем доступ."
ALREADY = "Заявка с этим адресом уже принята — повторно отправлять не нужно. Мы напишем, когда откроем доступ."


def _pct(part: int, whole: int) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


def build_funnel(counts: dict[str, int], leads_total: int) -> FunnelOut:
    """counts — число уникальных посетителей по имени события; leads_total — число сохранённых заявок."""
    visitors = counts.get("landing_view", 0)
    clicks = counts.get("cta_click", 0)
    return FunnelOut(
        landing_visitors=visitors,
        cta_clicks=clicks,
        demo_clicks=counts.get("demo_click", 0),
        form_opens=counts.get("lead_form_open", 0),
        leads=leads_total,
        cta_rate_pct=_pct(clicks, visitors),
        lead_rate_pct=_pct(leads_total, visitors),
    )


async def record_event(db: AsyncSession, *, visitor_id: str, name: str) -> None:
    db.add(ProductEvent(visitor_id=visitor_id, name=name))
    await db.commit()


async def submit_lead(db: AsyncSession, payload: EarlyAccessIn) -> EarlyAccessOut:
    if payload.website:  # заполнено поле-ловушка — это бот: делаем вид, что приняли, ничего не сохраняем
        return EarlyAccessOut(message=THANKS)
    email = str(payload.email).strip().lower()
    existing = await db.scalar(select(EarlyAccessRequest.id).where(EarlyAccessRequest.email == email))
    if existing is not None:
        return EarlyAccessOut(already=True, message=ALREADY)
    db.add(
        EarlyAccessRequest(
            name=payload.name, email=email, organization=payload.organization, role=payload.role,
            comment=payload.comment, consent=True, visitor_id=payload.visitor_id,
        )
    )
    if payload.visitor_id:
        db.add(ProductEvent(visitor_id=payload.visitor_id, name="lead_submit"))
    try:
        await db.commit()
    except IntegrityError:  # две одинаковые заявки одновременно — вторая уже не новая
        await db.rollback()
        return EarlyAccessOut(already=True, message=ALREADY)
    return EarlyAccessOut(message=THANKS)


async def funnel(db: AsyncSession) -> FunnelOut:
    rows = (
        await db.execute(select(ProductEvent.name, func.count(func.distinct(ProductEvent.visitor_id))).group_by(ProductEvent.name))
    ).all()
    leads_total = await db.scalar(select(func.count()).select_from(EarlyAccessRequest)) or 0
    return build_funnel({name: int(n) for name, n in rows}, int(leads_total))


async def admin_overview(db: AsyncSession, *, limit: int = 200) -> tuple[FunnelOut, int, list[LeadOut]]:
    f = await funnel(db)
    rows = (await db.scalars(select(EarlyAccessRequest).order_by(EarlyAccessRequest.created_at.desc()).limit(limit))).all()
    leads = [
        LeadOut(id=r.id, name=r.name, email=r.email, organization=r.organization, role=r.role, comment=r.comment, created_at=r.created_at)
        for r in rows
    ]
    return f, f.leads, leads
