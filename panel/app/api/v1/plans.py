"""Plan catalogue management."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging
from app.db.models import Plan, Protocol, Service
from app.schemas import Message, Page, PlanCreate, PlanOut, PlanUpdate
from app.services.audit import audit
from app.services.serializers import count_services_by_user  # noqa: F401  (kept for symmetry)

router = APIRouter(prefix="/plans", tags=["plans"])


def _serialize(plan: Plan) -> dict:
    return {
        "id": plan.id,
        "code": plan.code,
        "name": plan.name,
        "description": plan.description,
        "price": plan.price,
        "currency": plan.currency,
        "duration_days": plan.duration_days,
        "traffic_gb": plan.traffic_gb,
        "max_devices": plan.max_devices,
        "allowed_protocols": [p.value if isinstance(p, Protocol) else p for p in (plan.allowed_protocols or [])],
        "node_group": plan.node_group,
        "configs_included": plan.configs_included,
        "is_active": plan.is_active,
        "is_public": plan.is_public,
        "sort_order": plan.sort_order,
        "features": plan.features or [],
    }


@router.get("", response_model=Page[PlanOut])
def list_plans(db: DbSession, paging: Paging, user: CurrentUser, include_inactive: bool = False) -> Page[PlanOut]:
    stmt = select(Plan)
    if not user.is_staff or not include_inactive:
        stmt = stmt.where(Plan.is_active.is_(True))
    if paging.q:
        like = f"%{paging.q}%"
        stmt = stmt.where(Plan.name.ilike(like) | Plan.code.ilike(like))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(
            stmt.order_by(Plan.sort_order.asc(), Plan.price.asc()).limit(paging.size).offset(paging.offset)
        ).scalars()
    )
    return Page.build([PlanOut.model_validate(_serialize(p)) for p in rows], total, paging.page, paging.size)


@router.get("/public", response_model=list[PlanOut])
def public_plans(db: DbSession) -> list[PlanOut]:
    rows = list(
        db.execute(
            select(Plan).where(Plan.is_active.is_(True), Plan.is_public.is_(True)).order_by(Plan.sort_order, Plan.price)
        ).scalars()
    )
    return [PlanOut.model_validate(_serialize(p)) for p in rows]


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(payload: PlanCreate, db: DbSession, actor: AdminUser) -> PlanOut:
    if db.execute(select(Plan).where(Plan.code == payload.code)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Plan code already exists")
    data = payload.model_dump()
    data["allowed_protocols"] = [p.value if isinstance(p, Protocol) else p for p in payload.allowed_protocols]
    plan = Plan(**data)
    db.add(plan)
    db.flush()
    audit(db, action="plan.create", actor_id=actor.id, entity_type="plan", entity_id=plan.id, meta={"code": plan.code})
    db.commit()
    return PlanOut.model_validate(_serialize(plan))


@router.patch("/{plan_id}", response_model=PlanOut)
def update_plan(plan_id: int, payload: PlanUpdate, db: DbSession, actor: AdminUser) -> PlanOut:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    changes = payload.model_dump(exclude_unset=True)
    if "allowed_protocols" in changes and changes["allowed_protocols"] is not None:
        changes["allowed_protocols"] = [p.value if isinstance(p, Protocol) else p for p in changes["allowed_protocols"]]
    for key, value in changes.items():
        setattr(plan, key, value)
    audit(db, action="plan.update", actor_id=actor.id, entity_type="plan", entity_id=plan.id, meta={"changes": changes})
    db.commit()
    return PlanOut.model_validate(_serialize(plan))


@router.delete("/{plan_id}", response_model=Message)
def delete_plan(plan_id: int, db: DbSession, actor: AdminUser) -> Message:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    in_use = db.execute(select(func.count(Service.id)).where(Service.plan_id == plan.id)).scalar_one()
    if in_use:
        plan.is_active = False
        plan.is_public = False
        audit(db, action="plan.update", actor_id=actor.id, entity_type="plan", entity_id=plan.id, meta={"soft_delete": True, "services": in_use})
        db.commit()
        return Message(detail=f"Plan is used by {in_use} service(s) - deactivated instead of deleted")
    audit(db, action="plan.delete", actor_id=actor.id, entity_type="plan", entity_id=plan.id, meta={"code": plan.code})
    db.delete(plan)
    db.commit()
    return Message(detail="Plan deleted")
