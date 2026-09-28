"""Bearer + HMAC authentication for the node agent API."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from agent.config import settings

MAX_DRIFT = 300


def _signature(token: str, timestamp: str, body: bytes) -> str:
    return hmac.new(token.encode("utf-8"), timestamp.encode("ascii") + body, hashlib.sha256).hexdigest()


async def verify_request(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_node_timestamp: Optional[str] = Header(default=None),
    x_node_signature: Optional[str] = Header(default=None),
) -> str:
    """Returns the matched token. Raises 401/403 on failure."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()

    if token not in settings.token_list:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid node token")

    # HMAC is mandatory unless explicitly relaxed (useful for local debugging).
    if x_node_signature:
        if not x_node_timestamp:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing timestamp")
        try:
            drift = abs(time.time() - int(x_node_timestamp))
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad timestamp")
        if drift > settings.signature_ttl_seconds:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Expired request signature")

        body = await request.body()
        expected = _signature(token, x_node_timestamp, body)
        if not hmac.compare_digest(expected, x_node_signature):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bad signature")
    elif not settings.allow_insecure_signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing request signature")

    return token
