"""Named invitations: a link for one guest («Pedro», «Cleaner»), with its own entities and its own expiry.

Unlike quick links, an invitation is stored, so it can be listed, extended or revoked on its own. Its link carries a
token «i.ref.signature» that does not hold the expiry: the expiry lives here, so extending an invitation keeps the
link the guest already has. Any household user (anyone not restricted) can manage invitations from the «Guest QR»
page, but only with entities the guest panel already allows.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import CONF_USERS, DOMAIN, PANEL_URL
from .invite import _base_url, _qr, invite_token
from .panel import async_allowed_entities, panel_config

STORE_KEY = f"{DOMAIN}.invitations"
DATA_INVITATIONS: HassKey[Invitations] = HassKey(f"{DOMAIN}_invitations")
KEEP_EXPIRED = 7 * 86400  # expired invitations stay listed (and can be extended) for a week
MAX_HOURS = 24 * 366
TOUCH_SAVE_DELAY = 60


async def async_get_invitations(hass: HomeAssistant) -> Invitations:
    """The one shared set of invitations, loaded once and kept across reloads of the integration.

    Reloading (which every options change does) must not read the file back while a save is still pending.
    """
    if (invitations := hass.data.get(DATA_INVITATIONS)) is None:
        invitations = hass.data[DATA_INVITATIONS] = Invitations(hass)
        await invitations.async_load()
    return invitations


class Invitations:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, 1, STORE_KEY)
        self.items: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.items = data.get("invitations", {}) if isinstance(data, dict) else {}
        self._prune()

    async def async_remove(self) -> None:
        self.items = {}
        await self._store.async_remove()

    @callback
    def _save(self, delay: float = 1) -> None:
        self._store.async_delay_save(lambda: {"invitations": self.items}, delay)

    @callback
    def _prune(self) -> None:
        cutoff = time.time() - KEEP_EXPIRED
        for ref in [ref for ref, item in self.items.items() if item["expires"] < cutoff]:
            del self.items[ref]

    @callback
    def create(self, name: str, entities: list[str], seconds: int, created_by: str | None) -> str:
        self._prune()
        ref = secrets.token_hex(4)
        while ref in self.items:
            ref = secrets.token_hex(4)
        now = int(time.time())
        self.items[ref] = {
            "name": name, "entities": entities, "created": now, "expires": now + seconds,
            "created_by": created_by, "last_used": None,
        }
        self._save()
        return ref

    @callback
    def extend(self, ref: str, seconds: int) -> None:
        """Add time from now if it already expired, else from its current expiry."""
        item = self.items[ref]
        item["expires"] = int(max(time.time(), item["expires"]) + seconds)
        self._save()

    @callback
    def revoke(self, ref: str) -> None:
        self.items.pop(ref, None)
        self._save()

    @callback
    def touch(self, ref: str) -> None:
        if (item := self.items.get(ref)) is not None:
            item["last_used"] = int(time.time())
            self._save(TOUCH_SAVE_DELAY)


# --- the «Guest QR» page's commands -----------------------------------------------------------------------------


def _context(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]):
    """(runtime, panel settings), or None after sending the error that explains why not."""
    found = panel_config(hass)
    if found is None:
        connection.send_error(msg["id"], "disabled", "The guest panel is switched off")
        return None
    runtime, conf = found
    if connection.user.id in runtime.entry.options.get(CONF_USERS, {}):
        connection.send_error(msg["id"], "unauthorized", "Restricted users cannot invite guests")
        return None
    return runtime, conf


def _describe(hass: HomeAssistant, secret: str, ref: str, item: dict[str, Any]) -> dict[str, Any]:
    url = f"{_base_url(hass).rstrip('/')}{PANEL_URL}?t={invite_token(secret, ref)}"
    return {"ref": ref, **item, "url": url, "svg": _qr(url)}


async def _async_listing(hass: HomeAssistant, runtime: Any, conf: dict[str, Any]) -> dict[str, Any]:
    secret = runtime.entry.data["secret"]
    allowed = await async_allowed_entities(hass, conf)
    entities = [
        {"entity_id": entity_id, "name": state.name if (state := hass.states.get(entity_id)) else entity_id}
        for entity_id in allowed
    ]
    invitations = sorted(
        (_describe(hass, secret, ref, item) for ref, item in runtime.invitations.items.items()),
        key=lambda item: -item["created"],
    )
    return {"invitations": invitations, "entities": entities, "now": int(time.time())}


@websocket_api.websocket_command({vol.Required("type"): "ha_guest/invitations"})
@websocket_api.async_response
async def ws_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (found := _context(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], await _async_listing(hass, *found))


@websocket_api.websocket_command({
    vol.Required("type"): "ha_guest/invitations/create",
    vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=60)),
    vol.Optional("entities", default=[]): [str],
    vol.Required("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=MAX_HOURS)),
})
@websocket_api.async_response
async def ws_create(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (found := _context(hass, connection, msg)) is None:
        return
    runtime, conf = found
    allowed = set(await async_allowed_entities(hass, conf))
    # Household members can narrow what a guest gets, never widen it beyond what the owner allowed.
    if not set(msg["entities"]) <= allowed:
        connection.send_error(msg["id"], "not_allowed", "Some entities are not on the guest panel")
        return
    runtime.invitations.create(msg["name"], msg["entities"], int(msg["hours"] * 3600), connection.user.name)
    connection.send_result(msg["id"], await _async_listing(hass, runtime, conf))


@websocket_api.websocket_command({
    vol.Required("type"): "ha_guest/invitations/extend",
    vol.Required("ref"): str,
    vol.Required("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=MAX_HOURS)),
})
@websocket_api.async_response
async def ws_extend(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (found := _context(hass, connection, msg)) is None:
        return
    runtime, conf = found
    if msg["ref"] not in runtime.invitations.items:
        connection.send_error(msg["id"], "not_found", "No such invitation")
        return
    runtime.invitations.extend(msg["ref"], int(msg["hours"] * 3600))
    connection.send_result(msg["id"], await _async_listing(hass, runtime, conf))


@websocket_api.websocket_command({vol.Required("type"): "ha_guest/invitations/revoke", vol.Required("ref"): str})
@websocket_api.async_response
async def ws_revoke(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (found := _context(hass, connection, msg)) is None:
        return
    runtime, conf = found
    runtime.invitations.revoke(msg["ref"])
    connection.send_result(msg["id"], await _async_listing(hass, runtime, conf))


@callback
def async_setup_invitation_commands(hass: HomeAssistant) -> None:
    for command in (ws_list, ws_create, ws_extend, ws_revoke):
        websocket_api.async_register_command(hass, command)
