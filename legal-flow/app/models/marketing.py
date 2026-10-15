import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UUIDPk


class EarlyAccessRequest(Base, UUIDPk):
    """Заявка на ранний доступ с лендинга — проверка спроса (гипотеза: юристам и редакциям нужен такой сервис).
    Один email — одна заявка."""

    __tablename__ = "early_access_requests"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    organization: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    role: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProductEvent(Base, UUIDPk):
    """Анонимное событие воронки лендинга (просмотр, нажатие кнопки, открытие формы, отправка заявки).
    visitor_id — случайный идентификатор браузера, без персональных данных; считаются уникальные посетители."""

    __tablename__ = "product_events"

    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
