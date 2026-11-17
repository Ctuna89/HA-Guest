"""The guest panel: a page at /guest that needs no Home Assistant account.

It is the only door for account-less guests, so everything is checked here on every request: the request must
come from one of the configured networks (never through Home Assistant Cloud), the entity must be on the panel's
list, and the action must be one of the few this module knows how to translate into a service call.
"""

from __future__ import annotations

from http import HTTPStatus
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.network import is_cloud_connection

from .const import (
    API_ACTION_URL,
    API_STATES_URL,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_NETWORKS,
    CONF_PANEL,
    CONF_TITLE,
    DEFAULT_TITLE,
    DOMAIN,
    PANEL_URL,
)

ATTRIBUTES = (
    "brightness", "supported_color_modes", "current_position", "temperature", "current_temperature",
    "min_temp", "max_temp", "target_temp_step", "hvac_modes", "volume_level", "media_title",
    "unit_of_measurement", "device_class",
)

ON_OFF = {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"}
# domain -> guest action -> service
SIMPLE_ACTIONS: dict[str, dict[str, str]] = {
    "light": ON_OFF,
    "switch": ON_OFF,
    "input_boolean": ON_OFF,
    "fan": ON_OFF,
    "humidifier": ON_OFF,
    "cover": {"open": "open_cover", "close": "close_cover", "stop": "stop_cover"},
    "valve": {"open": "open_valve", "close": "close_valve"},
    "lock": {"lock": "lock", "unlock": "unlock"},
    "scene": {"activate": "turn_on"},
    "script": {"activate": "turn_on"},
    "button": {"activate": "press"},
    "input_button": {"activate": "press"},
    "vacuum": {"start": "start", "dock": "return_to_base"},
    "media_player": {
        "turn_on": "turn_on", "turn_off": "turn_off", "play_pause": "media_play_pause",
        "volume_up": "volume_up", "volume_down": "volume_down",
    },
}


class InvalidAction(Exception):
    """The guest asked for something the panel does not offer."""


def _number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise InvalidAction
    return value


def resolve_action(state: State, action: Any, value: Any) -> tuple[str, dict[str, Any]]:
    """Translate a guest action into (service, service data) for the entity's own domain."""
    domain = state.domain
    if not isinstance(action, str):
        raise InvalidAction
    if service := SIMPLE_ACTIONS.get(domain, {}).get(action):
        return service, {}
    if (domain, action) == ("light", "brightness"):
        return "turn_on", {"brightness_pct": int(_number(value, 1, 100))}
    if (domain, action) == ("cover", "position"):
        return "set_cover_position", {"position": int(_number(value, 0, 100))}
    if (domain, action) == ("media_player", "volume"):
        return "volume_set", {"volume_level": _number(value, 0, 100) / 100}
    if (domain, action) == ("climate", "temperature"):
        low = state.attributes.get("min_temp", 5)
        high = state.attributes.get("max_temp", 40)
        return "set_temperature", {"temperature": _number(value, low, high)}
    if (domain, action) == ("climate", "hvac_mode"):
        if value not in state.attributes.get("hvac_modes", []):
            raise InvalidAction
        return "set_hvac_mode", {"hvac_mode": value}
    raise InvalidAction


def panel_config(hass: HomeAssistant) -> dict[str, Any] | None:
    """The panel settings, or None when the panel is switched off or the integration is not loaded."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        conf = entry.options.get(CONF_PANEL, {})
        if conf.get(CONF_ENABLED):
            return conf
    return None


def network_allowed(hass: HomeAssistant, request: web.Request, conf: dict[str, Any]) -> bool:
    if is_cloud_connection(hass) or request.remote is None:
        return False
    try:
        address = ip_address(request.remote)
    except ValueError:
        return False
    if mapped := getattr(address, "ipv4_mapped", None):
        address = mapped
    for network in conf.get(CONF_NETWORKS, []):
        try:
            if address in ip_network(network, strict=False):
                return True
        except ValueError:
            continue
    return False


def _area_name(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    area_id = entry.area_id
    if area_id is None and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = device.area_id if device else None
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None


class _GuestView(HomeAssistantView):
    requires_auth = False

    def _guard(self, request: web.Request) -> tuple[HomeAssistant, dict[str, Any]] | web.Response:
        hass = request.app["hass"]
        conf = panel_config(hass)
        if conf is None:
            return self.json({"error": "disabled"}, HTTPStatus.NOT_FOUND)
        if not network_allowed(hass, request, conf):
            # The caller's own address helps the owner work out which network to allow.
            return self.json({"error": "network", "ip": request.remote}, HTTPStatus.FORBIDDEN)
        return hass, conf


class GuestPageView(HomeAssistantView):
    """The page itself. It holds no data; everything comes from the guarded API below."""

    requires_auth = False
    url = PANEL_URL
    name = "ha_guest:page"

    def __init__(self) -> None:
        self._html: str | None = None

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if panel_config(hass) is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        if self._html is None:
            path = Path(__file__).parent / "frontend" / "guest.html"
            self._html = await hass.async_add_executor_job(path.read_text, "utf-8")
        return web.Response(
            text=self._html, content_type="text/html",
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
        )


class GuestStatesView(_GuestView):
    url = API_STATES_URL
    name = "api:ha_guest:states"

    async def get(self, request: web.Request) -> web.Response:
        guard = self._guard(request)
        if isinstance(guard, web.Response):
            return guard
        hass, conf = guard
        entities = []
        for entity_id in conf.get(CONF_ENTITIES, []):
            if (state := hass.states.get(entity_id)) is None:
                continue
            entities.append({
                "entity_id": entity_id,
                "name": state.name,
                "state": state.state,
                "area": _area_name(hass, entity_id),
                "attributes": {k: state.attributes[k] for k in ATTRIBUTES if k in state.attributes},
            })
        return self.json({"title": conf.get(CONF_TITLE) or DEFAULT_TITLE, "entities": entities})


class GuestActionView(_GuestView):
    url = API_ACTION_URL
    name = "api:ha_guest:action"

    async def post(self, request: web.Request) -> web.Response:
        guard = self._guard(request)
        if isinstance(guard, web.Response):
            return guard
        hass, conf = guard
        # Requiring JSON keeps other websites the guest visits from posting here with a plain form.
        if request.content_type != "application/json":
            return self.json({"error": "invalid"}, HTTPStatus.BAD_REQUEST)
        try:
            body = await request.json()
        except ValueError:
            return self.json({"error": "invalid"}, HTTPStatus.BAD_REQUEST)
        if not isinstance(body, dict):
            return self.json({"error": "invalid"}, HTTPStatus.BAD_REQUEST)

        entity_id = body.get("entity_id")
        if entity_id not in conf.get(CONF_ENTITIES, []) or (state := hass.states.get(entity_id)) is None:
            return self.json({"error": "not_allowed"}, HTTPStatus.FORBIDDEN)
        try:
            service, data = resolve_action(state, body.get("action"), body.get("value"))
        except InvalidAction:
            return self.json({"error": "invalid"}, HTTPStatus.BAD_REQUEST)
        try:
            await hass.services.async_call(
                state.domain, service, {**data, "entity_id": entity_id}, blocking=True
            )
        except (HomeAssistantError, ValueError):
            return self.json({"error": "failed"}, HTTPStatus.BAD_REQUEST)
        return self.json({"ok": True})


def async_register_views(hass: HomeAssistant) -> None:
    hass.http.register_view(GuestPageView())
    hass.http.register_view(GuestStatesView())
    hass.http.register_view(GuestActionView())
