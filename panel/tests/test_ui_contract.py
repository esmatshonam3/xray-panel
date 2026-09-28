"""UI contract tests.

The frontend is plain HTML/CSS/JS with no build step, so nothing would catch a
typo in an element id, an icon name or a translation key until a human clicked
the broken screen. These tests lock those contracts down.

They also guard the two deployment-critical invariants:

  * `/sub/<token>` must serve the subscription body, never the SPA shell
  * every asset the shell references must actually be served
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
I18N_JS = (STATIC / "i18n.js").read_text(encoding="utf-8")
ICONS_JS = (STATIC / "icons.js").read_text(encoding="utf-8")
STYLES = (STATIC / "styles.css").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Element contract
# --------------------------------------------------------------------------- #
def test_every_element_id_queried_by_js_exists_in_html():
    html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', HTML))
    queried = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'", APP_JS))
    queried |= set(re.findall(r'getElementById\(.([A-Za-z0-9_-]+).\)', APP_JS))
    created_at_runtime = set(re.findall(r'id="([A-Za-z0-9_-]+)"', APP_JS))

    missing = sorted(queried - html_ids - created_at_runtime)
    assert not missing, f"app.js queries ids that index.html does not define: {missing}"


def test_every_icon_name_is_defined():
    defined = set(re.findall(r"^    ([A-Za-z_]+):", ICONS_JS, re.M))
    used = set(re.findall(r'data-icon="([A-Za-z0-9_]+)"', HTML))
    # icon('name') calls
    used |= set(re.findall(r"icon\('([A-Za-z0-9_]+)'", APP_JS))
    # icon names carried in object literals, e.g. { icon: 'users', ... }
    used |= set(re.findall(r"icon:\s*'([A-Za-z0-9_]+)'", APP_JS))

    missing = sorted(used - defined)
    assert not missing, f"icons referenced but not defined in icons.js: {missing}"
    assert len(used) > 25, "the icon scan found suspiciously few references"


def test_assets_referenced_by_shell_exist_on_disk():
    referenced = set(re.findall(r'(?:href|src)="/(assets/[A-Za-z0-9_.-]+)"', HTML))
    referenced |= set(re.findall(r'(?:href|src)="/(favicon\.svg)"', HTML))
    assert referenced, "index.html references no local assets"

    for rel in referenced:
        path = STATIC / rel.removeprefix("assets/") if rel.startswith("assets/") else STATIC / rel
        assert path.is_file(), f"missing static asset: /{rel}"


def test_shell_references_the_frontend_bundle():
    for name in ("icons.js", "i18n.js", "app.js", "styles.css"):
        assert f"/assets/{name}" in HTML, f"{name} is not linked from index.html"


# --------------------------------------------------------------------------- #
#  Translation contract
# --------------------------------------------------------------------------- #
def _dict_keys(block: str) -> set[str]:
    return set(re.findall(r"^\s{4}'([A-Za-z0-9_.]+)':", block, re.M))


def _fa_en_blocks() -> tuple[set[str], set[str]]:
    _, _, rest = I18N_JS.partition("const FA = {")
    fa_block, _, tail = rest.partition("const EN = {")
    en_block = tail.split("const DICTS")[0]
    return _dict_keys(fa_block), _dict_keys(en_block)


def test_dictionaries_have_identical_keys():
    fa, en = _fa_en_blocks()
    assert len(fa) > 200, "the Persian dictionary looks truncated"
    assert fa - en == set(), f"keys missing from the English dictionary: {sorted(fa - en)}"
    assert en - fa == set(), f"keys missing from the Persian dictionary: {sorted(en - fa)}"


def test_every_referenced_key_exists():
    fa, _ = _fa_en_blocks()
    used = set(re.findall(r"\bt\(\s*'([A-Za-z0-9_.]+)'", APP_JS))
    used |= set(re.findall(r'data-i18n(?:-placeholder|-title)?="([A-Za-z0-9_.]+)"', HTML))

    missing = sorted(used - fa)
    assert not missing, f"translation keys used but not defined: {missing}"


def test_dynamic_status_keys_cover_all_api_enum_values():
    """`status.<x>` is built from API values, so the dictionary must be complete."""
    fa, en = _fa_en_blocks()
    values = [
        "active", "expired", "limited", "disabled", "pending", "deleted",
        "online", "offline", "degraded", "unknown", "maintenance",
        "paid", "failed", "refunded", "canceled", "awaiting_review",
        "ok", "down", "warning", "critical", "info",
        "user", "support", "admin", "owner",
    ]
    gaps = [v for v in values if f"status.{v}" not in fa or f"status.{v}" not in en]
    assert not gaps, f"missing status translations: {gaps}"


# --------------------------------------------------------------------------- #
#  Theme / RTL contract
# --------------------------------------------------------------------------- #
def test_theme_tokens_exist_for_both_modes():
    """Brand tokens live in :root; surface tokens must be redefined per theme."""
    root_block = STYLES.split(":root {", 1)[1].split("}", 1)[0]
    for token in ("--accent", "--accent-2", "--ok", "--warn", "--danger"):
        assert token in root_block, f"{token} missing from :root"

    for token in ("--bg", "--surface", "--text", "--text-dim", "--border", "--shadow", "--skel"):
        for theme in ("dark", "light"):
            pattern = rf"\[data-theme='{theme}'\]\s*\{{[^}}]*{re.escape(token)}\s*:"
            assert re.search(pattern, STYLES, re.S), f"{token} missing from the {theme} theme block"


def test_rtl_uses_logical_properties():
    """Physical left/right would break the Persian layout."""
    assert "inset-inline-start" in STYLES
    assert "margin-inline-start" in STYLES
    # `dir` is flipped by the i18n module
    assert "setAttribute('dir'" in I18N_JS


def test_theme_and_language_are_restored_before_paint():
    """The inline bootstrap must run in <head>, before any body markup."""
    head = HTML.split("</head>")[0]
    assert "xpanel.theme" in head
    assert "xpanel.lang" in head


# --------------------------------------------------------------------------- #
#  Deployment contract
# --------------------------------------------------------------------------- #
def test_subscription_url_uses_the_short_path(db, normal_user, node, inbound, plan):
    from app.services.provisioning import create_service

    service = create_service(db, user=normal_user, plan=plan, inbound=inbound)
    assert service.subscription_url.endswith(f"/sub/{service.sub_token}")


def test_short_sub_path_serves_a_config_not_html(client, auth_headers, normal_user, inbound, plan):
    created = client.post(
        "/api/v1/services",
        headers=auth_headers,
        json={"user_id": normal_user.id, "plan_id": plan.id, "inbound_id": inbound.id},
    ).json()

    response = client.get(created["subscription_url"].replace("http://localhost:8000", ""))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "DOCTYPE" not in response.text  # the SPA shell must never win here

    body = base64.b64decode(response.text + "=" * (-len(response.text) % 4)).decode()
    assert body.startswith(("vless://", "vmess://", "trojan://", "ss://"))


def test_short_sub_path_supports_all_formats(client, auth_headers, normal_user, inbound, plan):
    created = client.post(
        "/api/v1/services",
        headers=auth_headers,
        json={"user_id": normal_user.id, "plan_id": plan.id, "inbound_id": inbound.id},
    ).json()
    short = created["subscription_url"].replace("http://localhost:8000", "")

    assert client.get(f"{short}?format=plain").status_code == 200
    clash = client.get(f"{short}/clash.yaml")
    assert clash.status_code == 200 and "proxies:" in clash.text
    assert client.get(f"{short}/info").status_code == 200
    assert client.get(f"{short}/qr.png").status_code == 200


def test_unknown_paths_under_api_and_sub_return_json_not_html(client):
    for path in ("/api/nope", "/sub/nope"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")


def test_spa_shell_is_served_for_client_routes(client):
    response = client.get("/some/deep/link")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "data-theme" in response.text
