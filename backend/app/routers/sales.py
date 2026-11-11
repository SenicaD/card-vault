from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from ..db import get_session
from ..models import CardStatus, ListingSeen, Sale, utcnow
from ..services.grading import normalize_grade
from .common import get_card_or_404

router = APIRouter(prefix="/api/cards/{card_id}", tags=["sales"])


class SaleCreate(BaseModel):
    price: float = Field(gt=0)
    sold_at: datetime | None = None  # defaults to now
    note: str | None = Field(default=None, max_length=500)


@router.post("/sales", response_model=Sale, status_code=201)
def record_sale(card_id: int, body: SaleCreate, session: Session = Depends(get_session)):
    """Record what you actually sold the card for, and mark it sold."""
    card = get_card_or_404(session, card_id)
    sale = Sale(card_id=card.id, price=round(body.price, 2), note=body.note,
                grade=normalize_grade(card.grade), sold_at=body.sold_at or utcnow())
    session.add(sale)
    card.status = CardStatus.sold
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    session.refresh(sale)
    return sale


@router.get("/sales", response_model=list[Sale])
def list_sales(card_id: int, session: Session = Depends(get_session)):
    get_card_or_404(session, card_id)
    return session.exec(select(Sale).where(Sale.card_id == card_id).order_by(col(Sale.sold_at).desc())).all()


@router.delete("/sales/{sale_id}", status_code=204)
def delete_sale(card_id: int, sale_id: int, session: Session = Depends(get_session)):
    sale = session.get(Sale, sale_id)
    if not sale or sale.card_id != card_id:
        raise HTTPException(404, "Sale not found")
    session.delete(sale)
    session.commit()


@router.get("/gone", response_model=list[ListingSeen])
def gone_listings(card_id: int, limit: int = 20, session: Session = Depends(get_session)):
    """Listings that disappeared from eBay, newest first. Their last asking price is our estimate of a sale."""
    get_card_or_404(session, card_id)
    return session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id, ListingSeen.status == "gone")
                        .order_by(col(ListingSeen.gone_at).desc()).limit(min(max(limit, 1), 100))).all()
