"""Node agent FastAPI application."""
from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, WebSocket, status
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field
from websockets.legacy.client import connect as websocket_connect

from agent import system, xray_api
from agent.config import settings
from agent.security import verify_request
from agent.xray_manager import manager

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("node-agent")


# --------------------------------------------------------------------------- #
#  Schemas
# --------------------------------------------------------------------------- #
class UserSpec(BaseModel):
    email: str
    id: Optional[str] = None
    password: Optional[str] = None
    flow: Optional[str] = None
    level: int = 0
    enable: bool = True
    alter_id: int = 0
    security: str = "auto"


class ApplyUsersRequest(BaseModel):
    inbound_tag: str
    protocol: str
    users: list[UserSpec] = []


class AddUserRequest(BaseModel):
    inbound_tag: str
    protocol: str
    user: UserSpec


class InboundSpec(BaseModel):
    tag: str
    protocol: str
    port: int
    listen: str = "0.0.0.0"
    transport: str = "tcp"
    security: str = "none"
    sni: Optional[str] = None
    alpn: list[str] = []
    path: Optional[str] = None
    host_header: Optional[str] = None
    service_name: Optional[str] = None
    flow: Optional[str] = None
    ss_method: Optional[str] = None
    ss_password: Optional[str] = None
    reality_dest: Optional[str] = None
    reality_private_key: Optional[str] = None
    reality_short_ids: list[str] = []
    reality_spider_x: Optional[str] = None
    cert_file: Optional[str] = None
    key_file: Optional[str] = None


class ApplyInboundsRequest(BaseModel):
    inbounds: list[InboundSpec] = Field(default_factory=list)
    drop_orphan_users: bool = True


# --------------------------------------------------------------------------- #
#  App
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("node agent starting", extra={"node": settings.node_name})
    ok, error = manager.start()
    if not ok:
        log.error("initial xray start failed: %s", error)
    else:
        log.info("xray core started")
    yield
    log.info("node agent stopping")
    manager.stop()


app = FastAPI(
    title="Xray Node Agent",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

public = APIRouter()
private = APIRouter(dependencies=[Depends(verify_request)])


# --------------------------------------------------------------------------- #
#  Public (no auth): used for uptime monitoring and Render health checks
# --------------------------------------------------------------------------- #
@public.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "node": settings.node_name, **manager.status()}


@public.get("/ping")
def ping() -> dict[str, str]:
    return {"pong": settings.node_name}


@public.websocket("/ws/{path:path}")
async def railway_websocket_bridge(websocket: WebSocket):
    """Bridge Railway's public HTTPS/WebSocket listener to a local Xray WS inbound.

    Railway's HTTP domain terminates TLS and forwards WebSockets to AGENT_PORT.
    Requests use a dedicated /ws prefix and are mapped back to the configured
    Xray path. The local inbound stays plain WS on its private container port,
    so no Railway TCP proxy or publicly exposed raw port is needed for this mode.
    """
    request_path = "/" + websocket.path_params["path"].lstrip("/")
    spec = next(
        (
            item for item in manager.state.get("inbounds", [])
            if item.get("transport") == "ws"
            and (item.get("path") or "/") == request_path
            and item.get("security", "none") == "none"
        ),
        None,
    )
    if spec is None:
        await websocket.close(code=1008, reason="unknown WebSocket inbound")
        return

    upstream_url = f"ws://127.0.0.1:{int(spec['port'])}{request_path}"
    try:
        async with websocket_connect(
            upstream_url,
            max_size=None,
            max_queue=None,
            ping_interval=None,
            open_timeout=8,
        ) as upstream:
            await websocket.accept()
            async def client_to_xray() -> None:
                while True:
                    frame = await websocket.receive()
                    if frame["type"] == "websocket.disconnect":
                        return
                    if frame.get("bytes") is not None:
                        await upstream.send(frame["bytes"])
                    elif frame.get("text") is not None:
                        await upstream.send(frame["text"])

            async def xray_to_client() -> None:
                async for frame in upstream:
                    if isinstance(frame, bytes):
                        await websocket.send_bytes(frame)
                    else:
                        await websocket.send_text(frame)

            tasks = [
                asyncio.create_task(client_to_xray()),
                asyncio.create_task(xray_to_client()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                error = task.exception() if not task.cancelled() else None
                if error:
                    raise error
    except Exception as exc:
        log.debug("WebSocket bridge closed for %s: %s", request_path, exc)
        try:
            await websocket.close(code=1011, reason="upstream unavailable")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Private (bearer + HMAC)
# --------------------------------------------------------------------------- #
@private.get("/health/deep")
def deep_health() -> dict[str, Any]:
    ok, error = manager.ensure_running()
    return {"ok": ok, "error": error, **manager.status(), **system.snapshot()}


@private.get("/system")
def system_info() -> dict[str, Any]:
    return {"ok": True, "node": settings.node_name, "public_host": settings.public_host, **system.snapshot()}


@private.get("/stats")
def stats(reset: bool = False) -> dict[str, Any]:
    try:
        data = manager.stats(reset=reset)
    except xray_api.XrayApiError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return {"ok": True, "users": data, "count": len(data)}


@private.post("/users")
def add_user(payload: AddUserRequest) -> dict[str, Any]:
    spec = manager._find_inbound(payload.inbound_tag)  # noqa: SLF001 - internal by design
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown inbound '{payload.inbound_tag}'")

    users = [u for u in manager.inbound_clients(payload.inbound_tag) if u["email"] != payload.user.email]
    users.append(payload.user.model_dump())
    result = manager.apply_users(payload.inbound_tag, payload.protocol, users)
    if not result.get("ok"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.get("error") or "apply failed")
    return result


@private.delete("/users/{inbound_tag}/{email}")
def remove_user(inbound_tag: str, email: str) -> dict[str, Any]:
    if manager._find_inbound(inbound_tag) is None:  # noqa: SLF001
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown inbound '{inbound_tag}'")
    users = [u for u in manager.inbound_clients(inbound_tag) if u["email"] != email]
    spec = manager._find_inbound(inbound_tag)  # noqa: SLF001
    result = manager.apply_users(inbound_tag, spec["protocol"], users)
    if not result.get("ok"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.get("error") or "apply failed")
    return result


@private.post("/users/apply")
def apply_users(payload: ApplyUsersRequest) -> dict[str, Any]:
    result = manager.apply_users(
        payload.inbound_tag, payload.protocol, [u.model_dump() for u in payload.users]
    )
    if not result.get("ok"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.get("error") or "apply failed")
    return result


@private.post("/inbounds/apply")
def apply_inbounds(payload: ApplyInboundsRequest) -> dict[str, Any]:
    result = manager.apply_inbounds(
        [i.model_dump() for i in payload.inbounds], drop_orphan_users=payload.drop_orphan_users
    )
    if not result.get("ok"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.get("error") or "reload failed")
    return result


@private.get("/config/{tag}")
def inbound_config(tag: str) -> dict[str, Any]:
    spec = manager._find_inbound(tag)  # noqa: SLF001
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown inbound '{tag}'")
    return {
        "ok": True,
        "inbound": spec,
        "clients": manager.inbound_clients(tag),
        "rendered": manager.render_config(),
    }


@private.post("/restart")
def restart() -> dict[str, Any]:
    ok, error = manager.reload()
    if not ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, error or "restart failed")
    return {"ok": True, **manager.status()}


@private.post("/reality-keys")
def reality_keys() -> dict[str, Any]:
    private, public = xray_api.generate_reality_keys()
    if not private:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "xray x25519 failed")
    return {"ok": True, "private_key": private, "public_key": public}


app.include_router(public)
app.include_router(private)
