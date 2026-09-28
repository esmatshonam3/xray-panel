"""Authentication, RBAC and rate limiting."""
from __future__ import annotations


def test_login_success(client, admin_user):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin12345"})
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_login_rejects_bad_password(client, admin_user):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-pass"})
    assert response.status_code == 401


def test_login_rejects_unknown_user(client):
    response = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "whatever123"})
    assert response.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_profile(client, auth_headers):
    response = client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "admin"
    assert response.json()["role"] == "owner"


def test_refresh_token_flow(client, admin_user):
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin12345"}).json()
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_access_token_cannot_be_used_as_refresh(client, admin_user):
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin12345"}).json()
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": login["access_token"]})
    assert response.status_code == 401


def test_register_creates_user(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "newbie", "password": "sup3rsecret", "email": "newbie@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]

    duplicate = client.post(
        "/api/v1/auth/register", json={"username": "newbie", "password": "sup3rsecret"}
    )
    assert duplicate.status_code == 409


def test_regular_user_cannot_list_users(client, user_headers):
    assert client.get("/api/v1/users", headers=user_headers).status_code == 403


def test_admin_can_list_users(client, auth_headers, normal_user):
    response = client.get("/api/v1/users", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_change_password(client, user_headers):
    response = client.post(
        "/api/v1/auth/change-password",
        headers=user_headers,
        json={"current_password": "customer123", "new_password": "brandnew123"},
    )
    assert response.status_code == 200
    assert client.post(
        "/api/v1/auth/login", json={"username": "customer", "password": "brandnew123"}
    ).status_code == 200


def test_weak_password_rejected(client, user_headers):
    response = client.post(
        "/api/v1/auth/change-password",
        headers=user_headers,
        json={"current_password": "customer123", "new_password": "12345678"},
    )
    assert response.status_code == 422


def test_api_key_rotation_and_usage(client, auth_headers):
    rotated = client.post("/api/v1/auth/api-key", headers=auth_headers)
    assert rotated.status_code == 200
    api_key = rotated.json()["api_key"]

    response = client.get("/api/v1/auth/me", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_login_is_rate_limited(client, admin_user):
    # LOGIN_MAX_ATTEMPTS defaults to 8 in the window.
    codes = [
        client.post("/api/v1/auth/login", json={"username": "admin", "password": "bad-password"}).status_code
        for _ in range(12)
    ]
    assert 429 in codes
