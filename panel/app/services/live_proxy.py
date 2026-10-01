"""VLESS-over-WebSocket data plane hosted by the panel process itself."""
from __future__ import annotations

import asyncio
import ipaddress
import struct
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.db.base import as_utc, utcnow
from app.db.models import Protocol, Service, ServiceStatus, TrafficDaily
from app.db.session import SessionLocal

router = APIRouter()
_lock = asyncio.Lock()
_active_by_service: dict[int, int] = {}
_active_ip_counts: dict[int, dict[str, int]] = {}


class InvalidVless(ValueError):
    pass


def _parse_request(data: bytes):
    """Parse VLESS request header and return (uuid, host, port, payload)."""
    if len(data) < 22:
        raise InvalidVless("incomplete request")
    version = data[0]
    try:
        user_id = str(uuid.UUID(bytes=data[1:17]))
    except ValueError as exc:
        raise InvalidVless("invalid user id") from exc
    addon_len = data[17]
    offset = 18 + addon_len
    if len(data) < offset + 4:
        raise InvalidVless("incomplete request")
    command = data[offset]
    if command != 1:  # VLESS TCP; UDP requires a distinct framing protocol.
        raise InvalidVless("only TCP commands are supported")
    port = struct.unpack("!H", data[offset + 1 : offset + 3])[0]
    address_type = data[offset + 3]
    offset += 4
    if address_type == 1:
        size = 4
        if len(data) < offset + size:
            raise InvalidVless("incomplete IPv4 address")
        host = str(ipaddress.IPv4Address(data[offset : offset + size]))
    elif address_type == 2:
        if len(data) < offset + 1:
            raise InvalidVless("incomplete domain address")
        size = data[offset]
        offset += 1
        if not size or len(data) < offset + size:
            raise InvalidVless("incomplete domain address")
        try:
            host = data[offset : offset + size].decode("idna")
        except UnicodeError as exc:
            raise InvalidVless("invalid domain") from exc
    elif address_type == 3:
        size = 16
        if len(data) < offset + size:
            raise InvalidVless("incomplete IPv6 address")
        host = str(ipaddress.IPv6Address(data[offset : offset + size]))
    else:
        raise InvalidVless("unknown address type")
    offset += size
    return version, user_id, host, port, data[offset:]


def _find_service(user_id: str) -> dict | None:
    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(Service).where(
                    Service.uuid == user_id,
                    Service.protocol == Protocol.vless,
                )
            ).scalars()
        )
        now = utcnow()
        for row in rows:
            if row.status != ServiceStatus.active:
                continue
            expiry = as_utc(row.expires_at)
            if expiry and expiry <= now:
                continue
            if row.traffic_limit_bytes and row.used_up_bytes + row.used_down_bytes >= row.traffic_limit_bytes:
                continue
            if row.inbound.transport.value != "ws":
                continue
            return {
                "id": row.id,
                "limit": row.traffic_limit_bytes,
                "used": row.used_up_bytes + row.used_down_bytes,
                "connection_limit": _note_int(row.note, "connection_limit"),
                "ip_limit": _note_int(row.note, "ip_limit"),
                "speed_limit": _note_int(row.note, "speed_limit_bytes"),
            }
    return None


def _note_int(note: str | None, key: str) -> int:
    marker = f"{key}="
    for item in (note or "").split():
        if item.startswith(marker):
            try:
                return max(int(item[len(marker) :]), 0)
            except ValueError:
                return 0
    return 0


async def _reserve(service_id: int, client_ip: str, connection_limit: int, ip_limit: int) -> bool:
    async with _lock:
        count = _active_by_service.get(service_id, 0)
        ips = _active_ip_counts.get(service_id, {})
        if connection_limit and count >= connection_limit:
            return False
        if ip_limit and client_ip not in ips and len(ips) >= ip_limit:
            return False
        _active_by_service[service_id] = count + 1
        _active_ip_counts.setdefault(service_id, {})[client_ip] = ips.get(client_ip, 0) + 1
        return True


async def _release(service_id: int, client_ip: str) -> None:
    async with _lock:
        count = _active_by_service.get(service_id, 0) - 1
        if count > 0:
            _active_by_service[service_id] = count
        else:
            _active_by_service.pop(service_id, None)
        ips = _active_ip_counts.get(service_id)
        if ips and client_ip in ips:
            ips[client_ip] -= 1
            if ips[client_ip] <= 0:
                ips.pop(client_ip, None)
            if not ips:
                _active_ip_counts.pop(service_id, None)


def _record_usage(service_id: int, upload: int, download: int) -> bool:
    with SessionLocal() as db:
        try:
            row = db.get(Service, service_id)
            if row is None or row.status != ServiceStatus.active:
                return False
            row.used_up_bytes += upload
            row.used_down_bytes += download
            row.last_connected_at = utcnow()
            if upload or download:
                today = datetime.now(timezone.utc).date()
                daily = db.execute(
                    select(TrafficDaily).where(
                        TrafficDaily.service_id == row.id,
                        TrafficDaily.day == today,
                    )
                ).scalar_one_or_none()
                if daily is None:
                    daily = TrafficDaily(
                        service_id=row.id,
                        user_id=row.user_id,
                        node_id=row.node_id,
                        day=today,
                        up_bytes=0,
                        down_bytes=0,
                    )
                    db.add(daily)
                daily.up_bytes += upload
                daily.down_bytes += download
            if row.traffic_limit_bytes and row.used_up_bytes + row.used_down_bytes >= row.traffic_limit_bytes:
                row.status = ServiceStatus.limited
            row.is_synced = True
            row.sync_error = None
            row.last_synced_at = utcnow()
            db.commit()
            return row.status == ServiceStatus.active
        except Exception:
            db.rollback()
            return False


def _client_ip(websocket: WebSocket) -> str:
    forwarded = websocket.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded[:64]
    if websocket.client:
        return websocket.client.host[:64]
    return "unknown"


@router.websocket("/ws/{user_id}")
async def vless_ws(websocket: WebSocket, user_id: str):
    if not settings.live_proxy_enabled:
        await websocket.close(code=1008)
        return
    try:
        uuid.UUID(user_id)
    except ValueError:
        await websocket.close(code=1008)
        return

    service = _find_service(user_id)
    if not service:
        await websocket.close(code=1008)
        return
    client_ip = _client_ip(websocket)
    if not await _reserve(service["id"], client_ip, service["connection_limit"], service["ip_limit"]):
        await websocket.close(code=1008)
        return

    remote = None
    try:
        await websocket.accept()
        first = await websocket.receive_bytes()
        version = first[0] if first else 0
        for _ in range(64):
            try:
                version, requested_id, host, port, initial_payload = _parse_request(first)
                break
            except InvalidVless:
                if len(first) >= 1024:
                    await websocket.close(code=1002)
                    return
                try:
                    first += await asyncio.wait_for(websocket.receive_bytes(), timeout=5)
                except (WebSocketDisconnect, asyncio.TimeoutError):
                    await websocket.close(code=1002)
                    return
        else:
            await websocket.close(code=1002)
            return
        if requested_id != user_id or not (1 <= port <= 65535):
            await websocket.close(code=1008)
            return
        try:
            remote = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=15)
        except (OSError, asyncio.TimeoutError, ValueError):
            await websocket.close(code=1011)
            return
        reader, writer = remote
        await websocket.send_bytes(bytes((version, 0)))
        started = asyncio.get_running_loop().time()
        transferred = 0
        speed_limit = service["speed_limit"]

        async def throttle(size: int):
            nonlocal transferred
            if not speed_limit:
                return
            transferred += size
            expected = transferred / speed_limit
            delay = expected - (asyncio.get_running_loop().time() - started)
            if delay > 0:
                await asyncio.sleep(delay)

        if initial_payload:
            writer.write(initial_payload)
            await writer.drain()
            await throttle(len(initial_payload))
            _record_usage(service["id"], len(initial_payload), 0)

        async def ws_to_tcp():
            while True:
                data = await websocket.receive_bytes()
                if not data:
                    return
                writer.write(data)
                await writer.drain()
                await throttle(len(data))
                if not _record_usage(service["id"], len(data), 0):
                    try:
                        await websocket.close(code=1008)
                    except RuntimeError:
                        pass
                    return

        async def tcp_to_ws():
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                if not _record_usage(service["id"], 0, len(data)):
                    try:
                        await websocket.close(code=1008)
                    except RuntimeError:
                        pass
                    return
                await throttle(len(data))
                await websocket.send_bytes(data)

        tasks = [asyncio.create_task(ws_to_tcp()), asyncio.create_task(tcp_to_ws())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except (WebSocketDisconnect, OSError, RuntimeError):
                pass
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass
    finally:
        if remote:
            remote[1].close()
            try:
                await remote[1].wait_closed()
            except OSError:
                pass
        # A reservation was made before accept so the per-service cap is
        # atomic; release it whether the handshake completes or is rejected.
        await _release(service["id"], client_ip)


def ensure_default_relay_endpoint(db) -> None:
    """Create a virtual location/inbound on first boot without creating a node process."""
    from app.core.security import encrypt
    from app.db.models import Inbound, Node, NodeStatus, Security, Transport
    from sqlalchemy import select

    node = db.execute(select(Node).where(Node.name == "panel-websocket-relay")).scalar_one_or_none()
    if node is None:
        node = Node(
            name="panel-websocket-relay",
            address="https://" + (settings.live_proxy_host or "panel.invalid"),
            public_host=settings.live_proxy_host or "panel.invalid",
            region="Railway",
            tags=["websocket", "relay"],
            status=NodeStatus.online,
            is_active=True,
        )
        node.api_token = "built-in-panel-relay"
        db.add(node)
        db.flush()

    inbound = db.execute(
        select(Inbound).where(Inbound.node_id == node.id, Inbound.tag == "panel-vless-ws")
    ).scalar_one_or_none()
    if inbound is None:
        inbound = Inbound(
                node_id=node.id,
                tag="panel-vless-ws",
                remark="VLESS · WebSocket",
                protocol=Protocol.vless,
                port=443,
                listen="0.0.0.0",
                transport=Transport.ws,
                security=Security.none,
                path="/ws",
                public_host=settings.live_proxy_host or None,
                public_port=443,
                is_active=True,
                is_default=True,
        )
        db.add(inbound)
        # Services below require the generated inbound primary key. Flush
        # here so the migration never writes a transient NULL inbound_id.
        db.flush()
    else:
        inbound.is_active = True
        inbound.is_default = True
        inbound.transport = Transport.ws
        inbound.protocol = Protocol.vless
        inbound.security = Security.none
        inbound.path = "/ws"
        inbound.public_host = settings.live_proxy_host or None
        inbound.public_port = 443
        db.flush()
    # Earlier rollout may already have moved services to the virtual node
    # while leaving them attached to an older WS inbound. Normalize all
    # persisted VLESS services into the single live endpoint on every boot.
    from app.db.models import Plan
    for service in db.execute(select(Service).where(Service.protocol == Protocol.vless)).scalars():
        if service.inbound_id != inbound.id or service.node_id != node.id:
            service.inbound_id = inbound.id
            service.node_id = node.id
        service.flow = None
        service.is_synced = True
        service.sync_error = None
        service.last_synced_at = utcnow()
    # Preserve existing customer IDs/subscription tokens while switching
    # eligible services to the new in-process VLESS/WS endpoint.
    for plan in db.execute(select(Plan)).scalars():
        plan.inbound_ids = [inbound.id]
        plan.allowed_protocols = [Protocol.vless.value]
    db.commit()
