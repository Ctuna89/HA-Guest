"""The guest panel: a page at /guest that needs no Home Assistant account.

It is the only door for account-less guests, so everything is checked here on every request: the request must
come from one of the configured networks (never through Home Assistant Cloud), carry a link token that has not
expired when expiring links are on, the entity must be on the panel's
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
    CONF_ACCENT,
    CONF_APPEARANCE,
    CONF_LAYOUT,
    CONF_MODE,
    CONF_SHOW_ICONS,
    CONF_THEME,
    CONF_WELCOME,
    DEFAULT_THEME,
    API_ACTION_URL,
    API_STATES_URL,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_SECRET,
    CONF_NETWORKS,
    CONF_PANEL,
    CONF_TITLE,
    DEFAULT_TITLE,
    DOMAIN,
    PANEL_URL,
    TOKEN_HEADER,
)
from .icons import async_icon_path
from .invite import link_seconds, token_expiry

ATTRIBUTES = (
    "brightness", "supported_color_modes", "current_position", "temperature", "current_temperature",
    "min_temp", "max_temp", "target_temp_step", "hvac_modes", "volume_level", "media_title",
    "unit_of_measurement", "device_class",
)

DOMAIN_ICONS = {
    "light": "mdi:lightbulb", "switch": "mdi:toggle-switch-variant", "input_boolean": "mdi:toggle-switch-variant",
    "fan": "mdi:fan", "humidifier": "mdi:air-humidifier", "cover": "mdi:window-shutter", "valve": "mdi:pipe-valve",
    "lock": "mdi:lock", "scene": "mdi:palette", "script": "mdi:script-text", "button": "mdi:gesture-tap-button",
    "input_button": "mdi:gesture-tap-button", "climate": "mdi:thermostat", "media_player": "mdi:speaker",
    "vacuum": "mdi:robot-vacuum", "binary_sensor": "mdi:checkbox-marked-circle-outline",
}
DEVICE_CLASS_ICONS = {
    "temperature": "mdi:thermometer", "humidity": "mdi:water-percent", "door": "mdi:door", "window": "mdi:window-closed",
    "motion": "mdi:motion-sensor", "battery": "mdi:battery", "power": "mdi:flash", "energy": "mdi:lightning-bolt",
    "illuminance": "mdi:brightness-5", "garage": "mdi:garage", "curtain": "mdi:curtains", "blind": "mdi:blinds",
    "outlet": "mdi:power-socket", "tv": "mdi:television", "speaker": "mdi:speaker",
}
DEFAULT_ICON = "mdi:eye"

# Home Assistant theme variable -> the guest page's own variable
THEME_VARIABLES = {
    "primary-color": "--accent",
    "text-primary-color": "--accent-text",
    "primary-background-color": "--bg",
    "card-background-color": "--card",
    "ha-card-background": "--card",
    "primary-text-color": "--text",
    "secondary-text-color": "--muted",
    "divider-color": "--line",
    "secondary-background-color": "--btn",
    "ha-card-border-radius": "--radius",
}

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
    """The panel settings (plus the link secret), or None when the panel is off or the integration not loaded."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        conf = entry.options.get(CONF_PANEL, {})
        if conf.get(CONF_ENABLED):
            return {
                **conf,
                CONF_SECRET: entry.data.get(CONF_SECRET, ""),
                CONF_APPEARANCE: entry.options.get(CONF_APPEARANCE, {}),
            }
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


def theme_variables(hass: HomeAssistant, name: str | None) -> dict[str, dict[str, str]]:
    """A Home Assistant theme as the guest page's variables, for light and for dark mode."""
    theme = hass.data.get("frontend_themes", {}).get(name) if name and name != DEFAULT_THEME else None
    if not isinstance(theme, dict):
        return {"light": {}, "dark": {}}
    modes = theme.get("modes") or {}
    result = {}
    for mode in ("light", "dark"):
        merged = {**theme, **(modes.get(mode) or {})}
        result[mode] = {
            ours: str(merged[theirs]) for theirs, ours in THEME_VARIABLES.items()
            # A value that points at another theme variable cannot be resolved outside Home Assistant.
            if isinstance(merged.get(theirs), (str, int, float)) and "var(" not in str(merged[theirs])
        }
    return result


def _icon_name(hass: HomeAssistant, state: State, override: str | None) -> str:
    """Same precedence as Home Assistant: the icon chosen by the owner wins over the integration's."""
    entry = er.async_get(hass).async_get(state.entity_id)
    return (
        override
        or (entry and entry.icon)
        or state.attributes.get("icon")
        or (entry and entry.original_icon)
        or DEVICE_CLASS_ICONS.get(state.attributes.get("device_class"))
        or DOMAIN_ICONS.get(state.domain, DEFAULT_ICON)
    )


async def async_build_sections(hass: HomeAssistant, conf: dict[str, Any]) -> list[dict[str, Any]]:
    """The page content: the owner's layout if there is one, otherwise the allowed entities grouped by area."""
    allowed = conf.get(CONF_ENTITIES, [])
    appearance = conf.get(CONF_APPEARANCE, {})
    show_icons = appearance.get(CONF_SHOW_ICONS, True)

    plan: list[tuple[str | None, list[dict[str, Any]]]] = []
    if layout := appearance.get(CONF_LAYOUT):
        for section in layout:
            items = [{"entity": item} if isinstance(item, str) else item for item in section.get("entities", [])]
            plan.append((section.get("title"), items))
    else:
        by_area: dict[str | None, list[dict[str, Any]]] = {}
        for entity_id in allowed:
            by_area.setdefault(_area_name(hass, entity_id), []).append({"entity": entity_id})
        named = sorted((area for area in by_area if area), key=str.casefold)
        plan = [(area, by_area[area]) for area in named]
        if None in by_area:
            plan.append((None, by_area[None]))

    sections = []
    for title, items in plan:
        entities = []
        for item in items:
            entity_id = item.get("entity")
            # The layout only arranges; what a guest may touch is decided by the allowed list alone.
            if entity_id not in allowed or (state := hass.states.get(entity_id)) is None:
                continue
            icon = await async_icon_path(hass, _icon_name(hass, state, item.get("icon"))) if show_icons else None
            entities.append({
                "entity_id": entity_id,
                "name": item.get("name") or state.name,
                "state": state.state,
                "icon": icon,
                "attributes": {k: state.attributes[k] for k in ATTRIBUTES if k in state.attributes},
            })
        if entities:
            sections.append({"title": title, "entities": entities})
    return sections


class _GuestView(HomeAssistantView):
    requires_auth = False

    def _guard(self, request: web.Request) -> tuple[HomeAssistant, dict[str, Any], int | None] | web.Response:
        """Return (hass, panel settings, when the guest's link expires), or the response that turns them away."""
        hass = request.app["hass"]
        conf = panel_config(hass)
        if conf is None:
            return self.json({"error": "disabled"}, HTTPStatus.NOT_FOUND)
        if not network_allowed(hass, request, conf):
            # The caller's own address helps the owner work out which network to allow.
            return self.json({"error": "network", "ip": request.remote}, HTTPStatus.FORBIDDEN)
        expires = None
        if lifetime := link_seconds(conf):
            expires = token_expiry(conf[CONF_SECRET], request.headers.get(TOKEN_HEADER), lifetime)
            if expires is None:
                return self.json({"error": "link"}, HTTPStatus.FORBIDDEN)
        return hass, conf, expires


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
        hass, conf, expires = guard
        appearance = conf.get(CONF_APPEARANCE, {})
        return self.json({
            "title": conf.get(CONF_TITLE) or DEFAULT_TITLE,
            "expires": expires,
            "appearance": {
                "mode": appearance.get(CONF_MODE, "auto"),
                "theme": theme_variables(hass, appearance.get(CONF_THEME)),
                "accent": appearance.get(CONF_ACCENT) or None,
                "welcome": appearance.get(CONF_WELCOME) or "",
            },
            "sections": await async_build_sections(hass, conf),
        })


class GuestActionView(_GuestView):
    url = API_ACTION_URL
    name = "api:ha_guest:action"

    async def post(self, request: web.Request) -> web.Response:
        guard = self._guard(request)
        if isinstance(guard, web.Response):
            return guard
        hass, conf, _ = guard
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
