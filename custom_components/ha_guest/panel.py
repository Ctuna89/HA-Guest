"""The guest panel: a page at /guest that needs no Home Assistant account.

It is the only door for account-less guests, so everything is checked here on every request: the request must
come from one of the configured networks (never through Home Assistant Cloud), carry a link token that has not
expired when expiring links are on, and arrive while the panel is open (its switch is on and the owner's
conditions hold). The entity must be on the panel's list, and the action must be one of the few this module knows
how to translate into a service call.
"""

from __future__ import annotations

from http import HTTPStatus
import re
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from aiohttp import web
from yarl import URL

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.network import is_cloud_connection
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_ACCENT,
    CONF_APPEARANCE,
    CONF_BACKGROUND,
    CONF_LAYOUT,
    CONF_MODE,
    CONF_SHOW_ICONS,
    CONF_THEME,
    CONF_WELCOME,
    DEFAULT_THEME,
    API_ACTION_URL,
    API_REDEEM_URL,
    CONF_CODES,
    CONF_NAME,
    API_STATES_URL,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_SECRET,
    CONF_DASHBOARD,
    CONF_SOURCE,
    SOURCE_DASHBOARD,
    CONF_NETWORKS,
    CONF_PANEL,
    CONF_TITLE,
    DEFAULT_TITLE,
    DOMAIN,
    PANEL_URL,
    TOKEN_HEADER,
)
from .activity import Guest, GuestRuntime
from .dashboard import Plan, async_dashboard_config, async_dashboard_plan, background_from_config
from .icons import async_icon_path
from .invite import check_token, code_seconds, create_session, find_code, link_seconds

ATTRIBUTES = (
    "brightness", "supported_color_modes", "color_temp_kelvin", "min_color_temp_kelvin", "max_color_temp_kelvin",
    "current_position", "temperature", "temperature_unit", "current_temperature", "humidity",
    "min_temp", "max_temp", "target_temp_step", "hvac_modes", "volume_level", "media_title", "media_artist",
    "unit_of_measurement", "device_class",
)
BACKGROUND_URL = re.compile(r"""url\(\s*(['"]?)([^'")\s]+)\1\s*\)""")
SAFE_URL = re.compile(r"""^(/(?!/)|https://)[^\s"'()\\<>]+$""")

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
# Icons that change with the state, as Home Assistant shows them: (domain or device class, state) -> icon
STATE_ICONS = {
    ("lock", "unlocked"): "mdi:lock-open-variant", ("lock", "open"): "mdi:lock-open-variant",
    ("lock", "jammed"): "mdi:lock-alert", ("light", "off"): "mdi:lightbulb-outline",
    ("weather", "clear-night"): "mdi:weather-night", ("weather", "cloudy"): "mdi:weather-cloudy",
    ("weather", "exceptional"): "mdi:alert-circle-outline", ("weather", "fog"): "mdi:weather-fog",
    ("weather", "hail"): "mdi:weather-hail", ("weather", "lightning"): "mdi:weather-lightning",
    ("weather", "lightning-rainy"): "mdi:weather-lightning-rainy", ("weather", "partlycloudy"): "mdi:weather-partly-cloudy",
    ("weather", "pouring"): "mdi:weather-pouring", ("weather", "rainy"): "mdi:weather-rainy",
    ("weather", "snowy"): "mdi:weather-snowy", ("weather", "snowy-rainy"): "mdi:weather-snowy-rainy",
    ("weather", "sunny"): "mdi:weather-sunny", ("weather", "windy"): "mdi:weather-windy",
    ("weather", "windy-variant"): "mdi:weather-windy-variant", ("media_player", "off"): "mdi:speaker-off",
    ("cover", "open"): "mdi:window-shutter-open", ("door", "on"): "mdi:door-open", ("window", "on"): "mdi:window-open",
    ("garage", "open"): "mdi:garage-open", ("garage", "on"): "mdi:garage-open",
    ("curtain", "open"): "mdi:curtains", ("curtain", "closed"): "mdi:curtains-closed",
    ("blind", "open"): "mdi:blinds-horizontal", ("blind", "closed"): "mdi:blinds-horizontal-closed",
    ("outlet", "off"): "mdi:power-plug-off", ("switch", "off"): "mdi:toggle-switch-variant-off",
    ("input_boolean", "off"): "mdi:toggle-switch-variant-off", ("fan", "off"): "mdi:fan-off",
}

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
    if (domain, action) == ("light", "color_temp"):
        low = state.attributes.get("min_color_temp_kelvin") or 2000
        high = state.attributes.get("max_color_temp_kelvin") or 6500
        return "turn_on", {"color_temp_kelvin": int(_number(value, low, high))}
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


def panel_config(hass: HomeAssistant) -> tuple[GuestRuntime, dict[str, Any]] | None:
    """The runtime and panel settings (plus link secret and appearance), or None when the panel is switched off
    in the settings or the integration is not loaded."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        conf = entry.options.get(CONF_PANEL, {})
        if conf.get(CONF_ENABLED):
            return entry.runtime_data, {
                **conf,
                CONF_SECRET: entry.data.get(CONF_SECRET, ""),
                CONF_APPEARANCE: entry.options.get(CONF_APPEARANCE, {}),
                CONF_CODES: entry.options.get(CONF_CODES, {}),
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


LOCAL_NAMES = {"localhost", "homeassistant", "homeassistant.local"}


def host_allowed(hass: HomeAssistant, request: web.Request) -> bool:
    """The page was reached through Home Assistant's own address, not through some other website's name.

    Without this, a website opened by any device on the home network could point its own domain name at Home
    Assistant (DNS rebinding) and use the panel from that device's browser. Accepted: IP addresses, the usual local
    names, any «.local» name, and the hosts of Home Assistant's internal and external URLs (the QR code uses the
    internal one).
    """
    host = (request.host or "").strip().lower()
    if host.startswith("["):  # [IPv6]:port
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:
        host = host.partition(":")[0]
    if not host:
        return False
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        return True
    if host in LOCAL_NAMES or host.endswith(".local"):
        return True
    configured = {
        (URL(url).host or "").lower() for url in (hass.config.internal_url, hass.config.external_url) if url
    }
    return host in configured


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


def background_url(value: Any) -> str | None:
    """An image address from a dashboard or theme background setting, if it is safe to put in the page.

    Accepts a CSS value with url(...), a {image: ...} setting, or a bare address. Only addresses on Home Assistant
    itself (/local/..., /api/image/serve/...) or https:// are kept, and nothing that could break out of a CSS url().
    """
    if isinstance(value, dict):
        value = value.get("image")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if match := BACKGROUND_URL.search(value):
        value = match.group(2)
    return value if SAFE_URL.match(value) else None


async def async_state_labels(hass: HomeAssistant, states: list[State]) -> dict[str, str]:
    """Each state in Home Assistant's own words and language («Inactivo», «Despejado, de noche»...)."""
    registry = er.async_get(hass)
    domains = {state.domain for state in states}
    platforms = {entry.platform for state in states if (entry := registry.async_get(state.entity_id))}
    language = hass.config.language or "en"
    component = await async_get_translations(hass, language, "entity_component", domains)
    entity = await async_get_translations(hass, language, "entity", platforms) if platforms else {}
    labels = {}
    for state in states:
        entry = registry.async_get(state.entity_id)
        keys = []
        if entry and entry.translation_key:
            keys.append(f"component.{entry.platform}.entity.{state.domain}.{entry.translation_key}.state.{state.state}")
        if device_class := state.attributes.get("device_class"):
            keys.append(f"component.{state.domain}.entity_component.{device_class}.state.{state.state}")
        keys.append(f"component.{state.domain}.entity_component._.state.{state.state}")
        for key in keys:
            if label := entity.get(key) or component.get(key):
                labels[state.entity_id] = label
                break
    return labels


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


def _icon_name(hass: HomeAssistant, state: State, override: str | dict[str, str] | None) -> str:
    """Same precedence as Home Assistant: the icon chosen by the owner wins over the integration's.

    The layout's icon can be one icon, or one per state ({locked: mdi:door-closed-lock, unlocked: mdi:door-open}).
    """
    if isinstance(override, dict):
        override = override.get(state.state)
    entry = er.async_get(hass).async_get(state.entity_id)
    device_class = state.attributes.get("device_class")
    return (
        override
        or (entry and entry.icon)
        or state.attributes.get("icon")
        or (entry and entry.original_icon)
        or STATE_ICONS.get((device_class, state.state))
        or DEVICE_CLASS_ICONS.get(device_class)
        or STATE_ICONS.get((state.domain, state.state))
        or DOMAIN_ICONS.get(state.domain, DEFAULT_ICON)
    )


async def async_dashboard_or_none(hass: HomeAssistant, conf: dict[str, Any]) -> Plan | None:
    """The chosen dashboard's content, when the panel takes it from a dashboard."""
    if conf.get(CONF_SOURCE) != SOURCE_DASHBOARD:
        return None
    return await async_dashboard_plan(hass, conf.get(CONF_DASHBOARD)) or []


async def async_allowed_entities(hass: HomeAssistant, conf: dict[str, Any], guest: Guest | None = None) -> list[str]:
    """What guests may use: the panel's entity list, or every entity the chosen dashboard shows.

    A guest who scanned a permanent code that names its own entities may use only those. A dashboard that is
    missing or cannot be read allows nothing.
    """
    if guest is not None and guest.scope is not None:
        return list(guest.scope)
    plan = await async_dashboard_or_none(hass, conf)
    if plan is None:
        return conf.get(CONF_ENTITIES, [])
    return [item["entity"] for _, items in plan for item in items]


async def async_build_sections(
    hass: HomeAssistant, conf: dict[str, Any], guest: Guest | None = None
) -> list[dict[str, Any]]:
    """The page content: the chosen dashboard, the owner's layout, or the allowed entities grouped by area."""
    appearance = conf.get(CONF_APPEARANCE, {})
    show_icons = appearance.get(CONF_SHOW_ICONS, True)

    plan: Plan = []
    if (dashboard := await async_dashboard_or_none(hass, conf)) is not None:
        plan = dashboard
        allowed = [item["entity"] for _, items in dashboard for item in items]
    elif layout := appearance.get(CONF_LAYOUT):
        allowed = conf.get(CONF_ENTITIES, [])
        for section in layout:
            items = [{"entity": item} if isinstance(item, str) else item for item in section.get("entities", [])]
            plan.append((section.get("title"), items))
    else:
        allowed = conf.get(CONF_ENTITIES, [])
        by_area: dict[str | None, list[dict[str, Any]]] = {}
        for entity_id in allowed:
            by_area.setdefault(_area_name(hass, entity_id), []).append({"entity": entity_id})
        named = sorted((area for area in by_area if area), key=str.casefold)
        plan = [(area, by_area[area]) for area in named]
        if None in by_area:
            plan.append((None, by_area[None]))

    if guest is not None and guest.scope is not None:
        # A permanent code's own entities: shown where the dashboard or layout puts them, the rest by area.
        allowed = list(guest.scope)
        placed = {item.get("entity") for _, items in plan for item in items}
        extra: dict[str | None, list[dict[str, Any]]] = {}
        for entity_id in allowed:
            if entity_id not in placed:
                extra.setdefault(_area_name(hass, entity_id), []).append({"entity": entity_id})
        plan = [*plan, *extra.items()]

    states = [s for _, items in plan for item in items if (s := hass.states.get(item.get("entity") or ""))]
    labels = await async_state_labels(hass, states)
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
                # The owner's own words for this card's state and buttons, when the layout gives them.
                "state_text": (item.get("states") or {}).get(state.state),
                "state_label": labels.get(entity_id),
                "labels": item.get("buttons") or {},
                "icon": icon,
                "attributes": {k: state.attributes[k] for k in ATTRIBUTES if k in state.attributes},
            })
        if entities:
            sections.append({"title": title, "entities": entities})
    return sections


async def async_background(hass: HomeAssistant, conf: dict[str, Any]) -> str | None:
    """The page background: the owner's own choice, else the chosen dashboard's, else the theme's."""
    appearance = conf.get(CONF_APPEARANCE, {})
    if url := background_url(appearance.get(CONF_BACKGROUND)):
        return url
    if conf.get(CONF_SOURCE) == SOURCE_DASHBOARD:
        config = await async_dashboard_config(hass, conf.get(CONF_DASHBOARD))
        if url := background_url(background_from_config(config)):
            return url
    theme = hass.data.get("frontend_themes", {}).get(appearance.get(CONF_THEME))
    if isinstance(theme, dict):
        return background_url(theme.get("lovelace-background"))
    return None


class _GuestView(HomeAssistantView):
    requires_auth = False

    def _place(
        self, request: web.Request
    ) -> tuple[HomeAssistant, dict[str, Any], GuestRuntime] | web.Response:
        """The checks that do not depend on a link: panel on, Home Assistant's own address, allowed network."""
        hass = request.app["hass"]
        found = panel_config(hass)
        if found is None:
            return self.json({"error": "disabled"}, HTTPStatus.NOT_FOUND)
        runtime, conf = found
        if not host_allowed(hass, request):
            return self.json({"error": "host"}, HTTPStatus.FORBIDDEN)
        if not network_allowed(hass, request, conf):
            # The caller's own address helps the owner work out which network to allow.
            return self.json({"error": "network", "ip": request.remote}, HTTPStatus.FORBIDDEN)
        return hass, conf, runtime

    def _guard(
        self, request: web.Request
    ) -> tuple[HomeAssistant, dict[str, Any], GuestRuntime, Guest] | web.Response:
        """Return (hass, panel settings, runtime, guest), or the response that turns the caller away."""
        place = self._place(request)
        if isinstance(place, web.Response):
            return place
        hass, conf, runtime = place
        guest = Guest(ip=str(request.remote))
        lifetime = link_seconds(conf)
        codes = conf.get(CONF_CODES, {})
        token = request.headers.get(TOKEN_HEADER)
        invitations = runtime.invitations.items if runtime.invitations is not None else {}
        checked = check_token(conf[CONF_SECRET], token, lifetime, codes, invitations) if token else None
        if checked is not None:
            expires, kind, ref = checked
            link = token.rpartition(".")[2][:8]
            guest = Guest(ip=guest.ip, link=link, expires=expires)
            if kind == "code":
                code = codes[ref]
                scope = tuple(code.get(CONF_ENTITIES) or ()) or None
                guest = Guest(ip=guest.ip, link=link, expires=expires, code=code.get(CONF_NAME), scope=scope)
            elif kind == "invite":
                item = invitations[ref]
                scope = tuple(item.get("entities") or ()) or None
                guest = Guest(ip=guest.ip, link=link, expires=expires, code=item["name"], scope=scope, invite=ref)
        elif lifetime:
            # Opening the bare address is ordinary; presenting a link that no longer works is worth reporting.
            if token:
                runtime.async_blocked(guest, "link")
            return self.json({"error": "link"}, HTTPStatus.FORBIDDEN)
        if not runtime.panel_open():
            # The same answer whatever the reason: it must not tell someone outside that nobody is home.
            runtime.async_blocked(guest, "paused")
            return self.json({"error": "paused"}, HTTPStatus.FORBIDDEN)
        return hass, conf, runtime, guest


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
        hass, conf, runtime, guest = guard
        runtime.async_seen(guest)
        title = conf.get(CONF_TITLE) or DEFAULT_TITLE
        appearance = conf.get(CONF_APPEARANCE, {})
        theme = theme_variables(hass, appearance.get(CONF_THEME))
        return self.json({
            "title": title,
            "code": guest.code,
            "expires": guest.expires,
            "appearance": {
                "mode": appearance.get(CONF_MODE, "auto"),
                "theme": theme,
                "background": await async_background(hass, conf),
                "accent": appearance.get(CONF_ACCENT) or None,
                "welcome": appearance.get(CONF_WELCOME) or "",
            },
            "sections": await async_build_sections(hass, conf, guest),
        })


class GuestActionView(_GuestView):
    url = API_ACTION_URL
    name = "api:ha_guest:action"

    async def post(self, request: web.Request) -> web.Response:
        guard = self._guard(request)
        if isinstance(guard, web.Response):
            return guard
        hass, conf, runtime, guest = guard
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
        if (
            not isinstance(entity_id, str)
            or entity_id not in await async_allowed_entities(hass, conf, guest)
            or (state := hass.states.get(entity_id)) is None
        ):
            return self.json({"error": "not_allowed"}, HTTPStatus.FORBIDDEN)
        action, value = body.get("action"), body.get("value")
        if action == "toggle":
            # Decided here, from the state the guest saw, so the log says what actually happened.
            action = "turn_off" if state.state == "on" else "turn_on"
        try:
            service, data = resolve_action(state, action, value)
        except InvalidAction:
            return self.json({"error": "invalid"}, HTTPStatus.BAD_REQUEST)
        context = Context()
        runtime.async_action(guest, state, action, value, context)
        try:
            await hass.services.async_call(
                state.domain, service, {**data, "entity_id": entity_id}, blocking=True, context=context
            )
        except (HomeAssistantError, ValueError):
            return self.json({"error": "failed"}, HTTPStatus.BAD_REQUEST)
        runtime.async_action_done(guest, state, action, value)
        return self.json({"ok": True})


class GuestRedeemView(_GuestView):
    """Trade the key in a permanent QR code for a session on this phone that lasts the code's days."""

    url = API_REDEEM_URL
    name = "api:ha_guest:redeem"

    async def post(self, request: web.Request) -> web.Response:
        place = self._place(request)
        if isinstance(place, web.Response):
            return place
        _, conf, runtime = place
        if request.content_type != "application/json":
            return self.json({"error": "invalid"}, HTTPStatus.BAD_REQUEST)
        try:
            body = await request.json()
        except ValueError:
            return self.json({"error": "invalid"}, HTTPStatus.BAD_REQUEST)
        codes = conf.get(CONF_CODES, {})
        guest = Guest(ip=str(request.remote))
        if not isinstance(body, dict) or (ref := find_code(codes, body.get("key"))) is None:
            runtime.async_blocked(guest, "link")
            return self.json({"error": "link"}, HTTPStatus.FORBIDDEN)
        if not runtime.panel_open():
            runtime.async_blocked(guest, "paused")
            return self.json({"error": "paused"}, HTTPStatus.FORBIDDEN)
        token, expires = create_session(conf[CONF_SECRET], ref, code_seconds(codes[ref]))
        return self.json({"token": token, "expires": expires})


def async_register_views(hass: HomeAssistant) -> None:
    hass.http.register_view(GuestPageView())
    hass.http.register_view(GuestStatesView())
    hass.http.register_view(GuestActionView())
    hass.http.register_view(GuestRedeemView())
