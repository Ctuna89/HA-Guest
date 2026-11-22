"""Expiring guest links, and the «Guest QR» sidebar page where household members create them.

A link carries a token made of its expiry time and a signature over it. Nothing is stored per link: a token is
valid while its signature matches the entry's secret and its expiry is still ahead, so replacing the secret
revokes every link at once.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import secrets
import time
from typing import Any

import segno
import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import (
    ICON_URL,
    CONF_CODES,
    CONF_DAYS,
    CONF_KEY,
    CONF_NAME,
    DEFAULT_CODE_DAYS,
    CONF_ENABLED,
    CONF_LINK_HOURS,
    CONF_PANEL,
    CONF_SECRET,
    CONF_USERS,
    DOMAIN,
    INVITE_PANEL_PATH,
    PANEL_URL,
    STATIC_URL,
)


def new_secret() -> str:
    return secrets.token_hex(32)


def link_seconds(panel: dict[str, Any]) -> int:
    """How long a link lasts, or 0 when links do not expire and no token is needed."""
    return int(float(panel.get(CONF_LINK_HOURS) or 0) * 3600)


def _sign(secret: str, expires: int, ref: str | None = None) -> str:
    message = str(expires) if ref is None else f"{expires}.{ref}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[:32]


def invite_token(secret: str, ref: str) -> str:
    """The token in a named invitation's link: «i.ref.signature». Its expiry is stored with the invitation."""
    mac = hmac.new(secret.encode(), f"invite.{ref}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"i.{ref}.{mac}"


def new_code() -> tuple[str, str]:
    """(public ref, secret key) for a permanent QR code."""
    return secrets.token_hex(4), secrets.token_urlsafe(16)


def find_code(codes: dict[str, dict[str, Any]], key: Any) -> str | None:
    """The ref of the permanent code whose secret key this is."""
    if not isinstance(key, str) or not key:
        return None
    found = None
    for ref, code in codes.items():
        # Compare against every code, so the time taken says nothing about which one nearly matched.
        if hmac.compare_digest(key.encode(), str(code.get(CONF_KEY, "")).encode()):
            found = ref
    return found


def code_seconds(code: dict[str, Any]) -> int:
    return int(float(code.get(CONF_DAYS) or DEFAULT_CODE_DAYS) * 86400)


def create_session(secret: str, ref: str, lifetime: int, now: float | None = None) -> tuple[str, int]:
    """A token for one phone that scanned a permanent code: «expires.ref.signature»."""
    expires = int(now if now is not None else time.time()) + lifetime
    return f"{expires}.{ref}.{_sign(secret, expires, ref)}", expires


def check_token(
    secret: str,
    token: Any,
    link_lifetime: int,
    codes: dict[str, dict[str, Any]],
    invitations: dict[str, dict[str, Any]] | None = None,
    now: float | None = None,
) -> tuple[int, str | None, str | None] | None:
    """(expiry, kind, ref) of a valid token; None if forged, expired or no longer allowed.

    * A quick link («expires.signature»): kind None, valid for at most the current link duration.
    * A session from a permanent code («expires.ref.signature»): kind «code», valid for at most that code's days,
      and only while the code exists.
    * A named invitation («i.ref.signature»): kind «invite», valid until the invitation's stored expiry, and only
      while it exists.
    """
    if not isinstance(token, str) or not secret:
        return None
    parts = token.split(".")
    if len(parts) == 2:
        return (expires, None, None) if (expires := token_expiry(secret, token, link_lifetime, now)) else None
    if len(parts) != 3:
        return None
    if parts[0] in ("i", "d"):
        item = (invitations or {}).get(parts[1])
        now = now if now is not None else time.time()
        if item is None or not now < item["expires"]:
            return None
        if parts[0] == "i":
            # The shared link. For a single-phone invitation it can only claim a phone, which the panel checks.
            if not hmac.compare_digest(token, invite_token(secret, parts[1])):
                return None
            return int(item["expires"]), "invite", parts[1]
        # «d.ref.key»: the phone a single-phone invitation is bound to.
        bound = item.get("device") or ""
        if not bound or not hmac.compare_digest(hashlib.sha256(parts[2].encode()).hexdigest(), bound):
            return None
        return int(item["expires"]), "device", parts[1]
    raw, ref, signature = parts
    if ref not in codes or not raw.isascii() or not raw.isdigit() or len(raw) > 12:
        return None
    expires = int(raw)
    now = now if now is not None else time.time()
    if not now < expires <= now + code_seconds(codes[ref]):
        return None
    if not hmac.compare_digest(signature, _sign(secret, expires, ref)):
        return None
    return expires, "code", ref


def create_token(secret: str, lifetime: int, now: float | None = None) -> tuple[str, int]:
    expires = int(now if now is not None else time.time()) + lifetime
    return f"{expires}.{_sign(secret, expires)}", expires


def token_expiry(secret: str, token: Any, lifetime: int, now: float | None = None) -> int | None:
    """When a valid token expires; None if it is forged, expired, or outlives the current link duration."""
    if not isinstance(token, str) or not secret or lifetime <= 0:
        return None
    raw, _, signature = token.partition(".")
    if not raw.isascii() or not raw.isdigit() or len(raw) > 12:
        return None
    expires = int(raw)
    now = now if now is not None else time.time()
    # A link made before the duration was shortened must not keep its old, longer life.
    if not now < expires <= now + lifetime:
        return None
    if not hmac.compare_digest(signature, _sign(secret, expires)):
        return None
    return expires


def _base_url(hass: HomeAssistant) -> str:
    """The address guests use: always the local one, never the remote or cloud address."""
    try:
        return get_url(hass, allow_external=False, allow_cloud=False)
    except NoURLAvailableError:
        scheme = "https" if hass.config.api.use_ssl else "http"
        return f"{scheme}://{hass.config.api.local_ip}:{hass.config.api.port}"


def _loaded_entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


@websocket_api.websocket_command({vol.Required("type"): "ha_guest/invite"})
@callback
def ws_invite(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Create a guest link and its QR code. For any Home Assistant user who is not themselves restricted."""
    entry = _loaded_entry(hass)
    panel = entry.options.get(CONF_PANEL, {}) if entry else {}
    if entry is None or not panel.get(CONF_ENABLED):
        connection.send_error(msg["id"], "disabled", "The guest panel is switched off")
        return
    if connection.user.id in entry.options.get(CONF_USERS, {}):
        connection.send_error(msg["id"], "unauthorized", "Restricted users cannot invite guests")
        return

    url = f"{_base_url(hass).rstrip('/')}{PANEL_URL}"
    expires = None
    if lifetime := link_seconds(panel):
        token, expires = create_token(entry.data[CONF_SECRET], lifetime)
        url = f"{url}?t={token}"
    base = f"{_base_url(hass).rstrip('/')}{PANEL_URL}"
    codes = [
        {
            "ref": ref, "name": code.get(CONF_NAME) or ref, "days": float(code.get(CONF_DAYS) or DEFAULT_CODE_DAYS),
            "url": (code_url := f"{base}?k={code[CONF_KEY]}"), "svg": _qr(code_url),
        }
        for ref, code in entry.options.get(CONF_CODES, {}).items()
        if code.get(CONF_KEY)
    ]
    connection.send_result(msg["id"], {"url": url, "expires": expires, "svg": _qr(url), "codes": codes})


def _qr(url: str) -> str:
    # No fixed size, only a viewBox, so the page can draw it at any size (a thumbnail, a printed sheet).
    return segno.make(url, error="m").svg_inline(scale=8, border=3, dark="#000", light="#fff", omitsize=True)


async def async_setup_invites(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_invite)
    here = Path(__file__).parent
    await hass.http.async_register_static_paths([
        StaticPathConfig(STATIC_URL, str(here / "frontend" / "invite-panel.js"), False),
        # Home-screen icons for guests who install the panel.
        StaticPathConfig(ICON_URL.format(size=256), str(here / "brand" / "icon.png"), True),
        StaticPathConfig(ICON_URL.format(size=512), str(here / "brand" / "icon@2x.png"), True),
    ])


@callback
def async_show_invite_panel(hass: HomeAssistant, show: bool) -> None:
    if not show:
        frontend.async_remove_panel(hass, INVITE_PANEL_PATH, warn_if_unknown=False)
        return
    frontend.async_register_built_in_panel(
        hass, component_name="custom", sidebar_title="Guest QR", sidebar_icon="mdi:qrcode-scan",
        frontend_url_path=INVITE_PANEL_PATH, require_admin=False, update=True,
        config={"_panel_custom": {
            "name": "ha-guest-invite-panel", "module_url": STATIC_URL, "embed_iframe": False, "trust_external": False,
        }},
    )
