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


def _sign(secret: str, expires: int) -> str:
    return hmac.new(secret.encode(), str(expires).encode(), hashlib.sha256).hexdigest()[:32]


def create_token(secret: str, lifetime: int, now: float | None = None) -> tuple[str, int]:
    expires = int(now if now is not None else time.time()) + lifetime
    return f"{expires}.{_sign(secret, expires)}", expires


def token_expiry(secret: str, token: Any, lifetime: int, now: float | None = None) -> int | None:
    """When a valid token expires; None if it is forged, expired, or outlives the current link duration."""
    if not isinstance(token, str) or not secret:
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
    svg = segno.make(url, error="m").svg_inline(scale=8, border=3, dark="#000", light="#fff")
    connection.send_result(msg["id"], {"url": url, "expires": expires, "svg": svg})


async def async_setup_invites(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_invite)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend" / "invite-panel.js"), False)]
    )


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
