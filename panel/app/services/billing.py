"""Orders, manual payment review, wallet balance and referrals."""
from __future__ import annotations

import secrets
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    Plan,
    Service,
    User,
)
from app.services.audit import audit
from app.services.provisioning import ProvisioningError, create_service, renew_service

log = get_logger(__name__)


class BillingError(Exception):
    pass


def _reference() -> str:
    return f"XP{utcnow().strftime('%y%m%d')}{secrets.token_hex(4).upper()}"


def create_order(
    db: Session,
    *,
    user: User,
    plan: Plan,
    method: PaymentMethod = PaymentMethod.manual,
    purpose: str = "purchase",
    service: Optional[Service] = None,
    receipt_url: Optional[str] = None,
    receipt_file_id: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    auto_complete: bool = False,
    commit: bool = True,
) -> Payment:
    if purpose == "renewal" and service is None:
        raise BillingError("renewal requires a target service")

    payment = Payment(
        reference=_reference(),
        user_id=user.id,
        plan_id=plan.id,
        service_id=service.id if service else None,
        purpose=purpose,
        amount=plan.price,
        currency=plan.currency or settings.default_currency,
        method=method,
        status=PaymentStatus.paid if auto_complete else PaymentStatus.pending,
        receipt_url=receipt_url,
        receipt_file_id=receipt_file_id,
        paid_at=utcnow() if auto_complete else None,
        meta=meta or {},
    )
    if method == PaymentMethod.manual and receipt_url and not auto_complete:
        payment.status = PaymentStatus.awaiting_review

    db.add(payment)
    db.flush()

    audit(
        db,
        action="payment.create",
        actor_id=user.id,
        actor_type="user",
        entity_type="payment",
        entity_id=payment.id,
        meta={"reference": payment.reference, "amount": payment.amount, "plan": plan.code, "method": method.value},
    )
    if commit:
        db.commit()

    if auto_complete:
        fulfil_payment(db, payment, actor_id=user.id, actor_type="system")
    return payment


def fulfil_payment(
    db: Session,
    payment: Payment,
    *,
    actor_id: Optional[int] = None,
    actor_type: str = "system",
) -> Optional[Service]:
    """Provision / renew the service once a payment is confirmed."""
    payment.status = PaymentStatus.paid
    payment.paid_at = utcnow()
    if actor_id:
        payment.reviewed_by_id = actor_id
        payment.reviewed_at = utcnow()

    user = db.get(User, payment.user_id)
    if user is None:
        raise BillingError("payment owner no longer exists")
    plan = db.get(Plan, payment.plan_id) if payment.plan_id else None

    service: Optional[Service] = None
    if payment.purpose == "topup":
        user.balance += payment.amount
    elif payment.purpose == "renewal" and payment.service_id:
        service = db.get(Service, payment.service_id)
        if service is None:
            raise BillingError("service to renew no longer exists")
        service.auto_renew = True
        renew_service(
            db,
            service,
            days=plan.duration_days if plan else 30,
            reset_traffic=True,
            add_traffic_gb=0,
            actor_id=actor_id,
            commit=False,
        )
    else:
        try:
            service = create_service(
                db,
                user=user,
                plan=plan,
                duration_days=plan.duration_days if plan else None,
                traffic_gb=plan.traffic_gb if plan else None,
                actor_id=actor_id,
                commit=False,
            )
        except ProvisioningError as exc:
            payment.status = PaymentStatus.paid  # money is in; provisioning is retried by ops
            payment.meta = {**(payment.meta or {}), "provision_error": str(exc)}
            db.commit()
            log.error("provisioning failed after payment", extra={"payment": payment.reference, "error": str(exc)})
            raise

    _apply_referral_bonus(db, user, payment)

    audit(
        db,
        action="payment.approve",
        actor_id=actor_id,
        actor_type=actor_type,
        entity_type="payment",
        entity_id=payment.id,
        meta={"reference": payment.reference, "service_id": service.id if service else None},
    )
    db.commit()
    return service


def reject_payment(
    db: Session,
    payment: Payment,
    *,
    reason: str,
    actor_id: Optional[int] = None,
) -> Payment:
    payment.status = PaymentStatus.failed
    payment.reject_reason = reason
    payment.reviewed_by_id = actor_id
    payment.reviewed_at = utcnow()
    audit(
        db,
        action="payment.reject",
        actor_id=actor_id,
        entity_type="payment",
        entity_id=payment.id,
        meta={"reference": payment.reference, "reason": reason},
    )
    db.commit()
    return payment


def pay_from_balance(db: Session, *, user: User, plan: Plan, service: Optional[Service] = None) -> Payment:
    if user.balance < plan.price:
        raise BillingError("insufficient balance")
    user.balance -= plan.price
    return create_order(
        db,
        user=user,
        plan=plan,
        method=PaymentMethod.balance,
        purpose="renewal" if service else "purchase",
        service=service,
        auto_complete=True,
    )


def _apply_referral_bonus(db: Session, user: User, payment: Payment) -> None:
    if not user.referred_by_id or payment.purpose == "topup":
        return
    percent = max(settings.referral_bonus_percent, 0)
    if percent <= 0:
        return
    referrer = db.get(User, user.referred_by_id)
    if referrer is None:
        return
    bonus = round(payment.amount * percent / 100, 2)
    if bonus <= 0:
        return
    referrer.balance += bonus
    referrer.referral_earnings += bonus
    audit(
        db,
        action="referral.bonus",
        actor_type="system",
        entity_type="user",
        entity_id=referrer.id,
        meta={"from_user": user.id, "payment": payment.reference, "amount": bonus},
    )


def pending_payments(db: Session, limit: int = 100) -> list[Payment]:
    return list(
        db.execute(
            select(Payment)
            .where(Payment.status.in_([PaymentStatus.pending, PaymentStatus.awaiting_review]))
            .order_by(Payment.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def user_payments(db: Session, user_id: int, limit: int = 50) -> list[Payment]:
    return list(
        db.execute(
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def ensure_referral_code(db: Session, user: User) -> str:
    if user.referral_code:
        return user.referral_code
    user.referral_code = secrets.token_hex(4).upper()
    db.commit()
    return user.referral_code


def apply_referral(db: Session, user: User, code: str) -> bool:
    if user.referred_by_id or not code:
        return False
    referrer = db.execute(select(User).where(User.referral_code == code.strip().upper())).scalar_one_or_none()
    if referrer is None or referrer.id == user.id:
        return False
    user.referred_by_id = referrer.id
    db.commit()
    return True
