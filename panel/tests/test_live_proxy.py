from __future__ import annotations

import struct
import uuid

import pytest

from app.services.live_proxy import InvalidVless, _parse_request


def _header(user_id: str, host: str, port: int = 443, payload: bytes = b"") -> bytes:
    raw_id = uuid.UUID(user_id).bytes
    encoded_host = host.encode("idna")
    return (
        b"\x00" + raw_id + b"\x00\x01" + struct.pack("!H", port)
        + b"\x02" + bytes([len(encoded_host)]) + encoded_host + payload
    )


def test_parse_vless_domain_request():
    user_id = str(uuid.uuid4())
    version, parsed_id, host, port, payload = _parse_request(_header(user_id, "example.com", payload=b"hello"))
    assert version == 0
    assert parsed_id == user_id
    assert host == "example.com"
    assert port == 443
    assert payload == b"hello"


def test_reject_vless_udp_request():
    user_id = str(uuid.uuid4())
    data = bytearray(_header(user_id, "example.com"))
    data[18] = 2
    with pytest.raises(InvalidVless):
        _parse_request(bytes(data))
