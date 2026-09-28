"""API v1 router aggregation."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, monitoring, nodes, payments, plans, reports, services, settings, subscription, telegram, users

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(plans.router)
api_router.include_router(services.router)
api_router.include_router(nodes.router)
api_router.include_router(nodes.inbound_router)
api_router.include_router(subscription.router)
api_router.include_router(payments.router)
api_router.include_router(reports.router)
api_router.include_router(monitoring.router)
api_router.include_router(settings.router)
api_router.include_router(telegram.router)

__all__ = ["api_router"]
