"""Payments: user orders, admin review queue, wallet top-up."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging, StaffUser
from app.db.models import Payment, PaymentStatus, Plan, Service, User
from app.schemas import Message, Page, PaymentCreate, PaymentOut, PaymentReview
from app.services.audit import audit
from app.services.billing import (
    BillingError,
    create_order,
    fulfil_payment,
    pay_from_balance,
    pending_payments,
    reject_payment,
    user_payments,
)
from app.services.serializers import payment_to_out

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("", response_model=Page[PaymentOut])
def list_payments(
    db: DbSession,
    paging: Paging,
    user: CurrentUser,
    status_filter: Optional[PaymentStatus] = None,
    user_id: Optional[int] = None,
) -> Page[PaymentOut]:
    stmt = select(Payment)
    if not user.is_staff:
        stmt = stmt.where(Payment.user_id == user.id)
    elif user_id:
        stmt = stmt.where(Payment.user_id == user_id)
    if status_filter:
        stmt = stmt.where(Payment.status == status_filter)
    if paging.q:
        stmt = stmt.where(Payment.reference.ilike(f"%{paging.q}%"))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(db.execute(stmt.order_by(Payment.created_at.desc()).limit(paging.size).offset(paging.offset)).unique().scalars())
    return Page.build([PaymentOut.model_validate(payment_to_out(p)) for p in rows], total, paging.page, paging.size)


@router.get("/pending", response_model=list[PaymentOut])
def review_queue(db: DbSession, _: StaffUser, limit: int = 100) -> list[PaymentOut]:
    return [PaymentOut.model_validate(payment_to_out(p)) for p in pending_payments(db, limit)]


@router.get("/mine", response_model=list[PaymentOut])
def my_payments(db: DbSession, user: CurrentUser, limit: int = 50) -> list[PaymentOut]:
    return [PaymentOut.model_validate(payment_to_out(p)) for p in user_payments(db, user.id, limit)]


@router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create(payload: PaymentCreate, db: DbSession, user: CurrentUser) -> PaymentOut:
    plan = db.get(Plan, payload.plan_id)
    if plan is None or not plan.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not available")
    service = db.get(Service, payload.service_id) if payload.service_id else None
    if service and service.user_id != user.id and not user.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your service")
    if payload.purpose == "renewal" and service is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Renewal requires a service")
    if payload.purpose == "purchase" and service is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Purchase cannot target an existing service")
    if payload.node_id and payload.purpose == "purchase":
        from app.services.provisioning import eligible_inbounds

        if not eligible_inbounds(db, plan, node_id=payload.node_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "No endpoint on the selected location matches this plan")
    try:
        if payload.method.value == "balance":
            payment = pay_from_balance(db, user=user, plan=plan, service=service, node_id=payload.node_id)
        else:
            payment = create_order(
                db,
                user=user,
                plan=plan,
                method=payload.method,
                purpose=payload.purpose,
                service=service,
                node_id=payload.node_id,
                receipt_url=payload.receipt_url,
            )
    except BillingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return PaymentOut.model_validate(payment_to_out(payment))


@router.post("/{payment_id}/approve", response_model=PaymentOut)
def approve(payment_id: int, db: DbSession, actor: AdminUser) -> PaymentOut:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    if payment.status == PaymentStatus.paid:
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment already approved")
    try:
        fulfil_payment(db, payment, actor_id=actor.id)
    except BillingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return PaymentOut.model_validate(payment_to_out(payment))


@router.post("/{payment_id}/reject", response_model=PaymentOut)
def reject(payment_id: int, payload: PaymentReview, db: DbSession, actor: AdminUser) -> PaymentOut:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    if payment.status == PaymentStatus.paid:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot reject a settled payment")
    reject_payment(db, payment, reason=payload.reason or "rejected by admin", actor_id=actor.id)
    return PaymentOut.model_validate(payment_to_out(payment))


@router.post("/{payment_id}/refund", response_model=Message)
def refund(payment_id: int, db: DbSession, actor: AdminUser) -> Message:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    if payment.status != PaymentStatus.paid:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only paid payments can be refunded")

    target = db.get(User, payment.user_id)
    if target:
        target.balance += payment.amount
    payment.status = PaymentStatus.refunded
    audit(
        db,
        action="payment.refund",
        actor_id=actor.id,
        entity_type="payment",
        entity_id=payment.id,
        meta={"reference": payment.reference, "amount": payment.amount},
    )
    db.commit()
    return Message(detail=f"{payment.amount} {payment.currency} credited back to the user's balance")
