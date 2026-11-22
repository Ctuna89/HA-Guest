"""Named invitations: a link for one guest («Pedro», «Cleaner»), with its own entities and its own expiry.

Unlike quick links, an invitation is stored, so it can be listed, extended or revoked on its own. Its link carries a
token «i.ref.signature» that does not hold the expiry: the expiry lives here, so extending an invitation keeps the
link the guest already has. Any household user (anyone not restricted) can manage invitations from the «Guest QR»
page, but only with entities the guest panel already allows.
"""

from __future__ import annotations

import hashlib
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
    def create(
        self,
        name: str,
        entities: list[str],
        seconds: int,
        created_by: str | None,
        starts: int | None = None,
        condition: str | None = None,
        single_device: bool = False,
    ) -> str:
        """A new invitation. «seconds» counts from «starts» when it starts later, else from now."""
        self._prune()
        ref = secrets.token_hex(4)
        while ref in self.items:
            ref = secrets.token_hex(4)
        now = int(time.time())
        starts = int(starts) if starts and starts > now else None
        self.items[ref] = {
            "name": name, "entities": entities, "created": now, "starts": starts,
            "expires": (starts or now) + seconds, "created_by": created_by, "last_used": None,
            "condition": condition or None, "single_device": bool(single_device), "device": None,
        }
        self._save()
        return ref

    def find(self, key: str) -> str | None:
        """An invitation's ref from its ref or its name (the most recent one with that name)."""
        if key in self.items:
            return key
        named = [ref for ref, item in self.items.items() if item["name"].casefold() == key.strip().casefold()]
        return max(named, key=lambda ref: self.items[ref]["created"]) if named else None

    @callback
    def claim(self, ref: str) -> str | None:
        """Bind a single-phone invitation to the phone opening it now; its device key, or None if already bound."""
        item = self.items[ref]
        if item.get("device"):
            return None
        key = secrets.token_urlsafe(16)
        item["device"] = hashlib.sha256(key.encode()).hexdigest()
        self._save()
        return key

    @callback
    def release(self, ref: str) -> None:
        """Let the invitation be opened on another phone (the guest changed phones)."""
        self.items[ref]["device"] = None
        self._save()

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
    for item in invitations:
        item["device"] = bool(item.get("device"))  # whether a phone is bound, never the key's hash
        if item.get("condition"):
            state = hass.states.get(item["condition"])
            item["condition_name"] = state.name if state else item["condition"]
    conditions = [
        {"entity_id": state.entity_id, "name": state.name}
        for state in sorted(hass.states.async_all(CONDITION_DOMAINS), key=lambda s: s.name.casefold())
    ]
    return {"invitations": invitations, "entities": entities, "conditions": conditions, "now": int(time.time())}


CONDITION_DOMAINS = ("schedule", "input_boolean")


async def async_create(
    hass: HomeAssistant,
    runtime: Any,
    conf: dict[str, Any],
    *,
    name: str,
    entities: list[str],
    seconds: int,
    created_by: str | None,
    starts: int | None = None,
    condition: str | None = None,
    single_device: bool = False,
) -> str:
    """Create an invitation after checking what it asks for; raises ValueError with a reason code otherwise."""
    if not set(entities) <= set(await async_allowed_entities(hass, conf)):
        # Household members can narrow what a guest gets, never widen it beyond what the owner allowed.
        raise ValueError("not_allowed")
    if condition and condition.partition(".")[0] not in CONDITION_DOMAINS:
        raise ValueError("bad_condition")
    return runtime.invitations.create(
        name, entities, seconds, created_by, starts=starts, condition=condition, single_device=single_device
    )


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
    vol.Optional("starts"): vol.Any(None, vol.Coerce(int)),
    vol.Optional("condition"): vol.Any(None, str),
    vol.Optional("single_device", default=False): bool,
})
@websocket_api.async_response
async def ws_create(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (found := _context(hass, connection, msg)) is None:
        return
    runtime, conf = found
    try:
        await async_create(
            hass, runtime, conf, name=msg["name"], entities=msg["entities"], seconds=int(msg["hours"] * 3600),
            created_by=connection.user.name, starts=msg.get("starts"), condition=msg.get("condition"),
            single_device=msg["single_device"],
        )
    except ValueError as err:
        connection.send_error(msg["id"], str(err), "The invitation cannot be created")
        return
    connection.send_result(msg["id"], await _async_listing(hass, runtime, conf))


@websocket_api.websocket_command({vol.Required("type"): "ha_guest/invitations/release", vol.Required("ref"): str})
@websocket_api.async_response
async def ws_release(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (found := _context(hass, connection, msg)) is None:
        return
    runtime, conf = found
    if msg["ref"] in runtime.invitations.items:
        runtime.invitations.release(msg["ref"])
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
    for command in (ws_list, ws_create, ws_extend, ws_revoke, ws_release):
        websocket_api.async_register_command(hass, command)
