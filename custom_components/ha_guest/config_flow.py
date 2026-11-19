"""Config flow: the guest panel on first setup; panel, appearance and restricted users from «Configure»."""

from __future__ import annotations

from ipaddress import ip_network
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, selector

from .const import (
    CONF_ACCENT,
    CONF_ALWAYS_ENTITIES,
    CONF_APPEARANCE,
    CONF_AWAY_READ,
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATES,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_FAMILY_LOCATION,
    CONF_HOME_ENTITIES,
    CONF_LAYOUT,
    CONF_LINK_HOURS,
    CONF_MODE,
    CONF_NETWORKS,
    CONF_NOTIFY,
    CONF_NOTIFY_ON,
    CONF_PANEL_CONDITION_ENTITY,
    CONF_PANEL_CONDITION_STATES,
    CONF_REQUIRE_HOME,
    CONF_SAFETY,
    CONF_PANEL,
    CONF_SECRET,
    CONF_SHOW_ICONS,
    CONF_THEME,
    CONF_TITLE,
    CONF_USER,
    CONF_USERS,
    CONF_WELCOME,
    DEFAULT_NOTIFY_ON,
    DEFAULT_THEME,
    DEFAULT_TITLE,
    DOMAIN,
    NOTIFY_EVENTS,
)
from .invite import new_secret

ENTITIES = selector.EntitySelector(selector.EntitySelectorConfig(multiple=True))
TEXT_LIST = selector.TextSelector(selector.TextSelectorConfig(multiple=True))

PANEL_SCHEMA = vol.Schema({
    vol.Required(CONF_ENABLED, default=True): selector.BooleanSelector(),
    vol.Required(CONF_TITLE, default=DEFAULT_TITLE): selector.TextSelector(),
    vol.Required(CONF_NETWORKS): TEXT_LIST,
    vol.Optional(CONF_ENTITIES, default=[]): ENTITIES,
    vol.Required(CONF_LINK_HOURS, default=0): selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=720, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
    ),
})

USER_SCHEMA = vol.Schema({
    vol.Optional(CONF_ALWAYS_ENTITIES, default=[]): ENTITIES,
    vol.Required(CONF_FAMILY_LOCATION, default=False): selector.BooleanSelector(),
    vol.Optional(CONF_HOME_ENTITIES, default=[]): ENTITIES,
    vol.Required(CONF_AWAY_READ, default=False): selector.BooleanSelector(),
    vol.Required(CONF_CONDITION_ENTITY): selector.EntitySelector(),
})

HOME_STATE_SCHEMA = vol.Schema({vol.Required(CONF_CONDITION_STATES): TEXT_LIST})

BUTTONS = ["open", "close", "stop", "lock", "unlock", "activate", "play_pause", "start", "dock"]

# What the owner writes under «Layout», in the style of a dashboard's entities card.
LAYOUT_SCHEMA = vol.Schema([{
    vol.Optional("title"): cv.string,
    vol.Required("entities"): [vol.Any(
        cv.entity_id,
        {
            vol.Required("entity"): cv.entity_id,
            vol.Optional("name"): cv.string,
            # One icon, or one per state.
            vol.Optional("icon"): vol.Any({cv.string: cv.icon}, cv.icon),
            # The text shown for each state, e.g. {unlocked: Open}.
            vol.Optional("states"): {cv.string: cv.string},
            # The text of each button, e.g. {unlock: Open the door}.
            vol.Optional("buttons"): {vol.In(BUTTONS): cv.string},
        },
    )],
}])
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# States that mean «home» for entities that are not a Wi-Fi name sensor.
HOME_BY_DOMAIN = {"device_tracker": "home", "person": "home", "binary_sensor": "on", "input_boolean": "on"}


def _default_network(hass: HomeAssistant) -> list[str]:
    """Guess the home network from Home Assistant's own address."""
    try:
        return [str(ip_network(f"{hass.config.api.local_ip}/24", strict=False))]
    except (AttributeError, ValueError):
        return []


def _clean_networks(values: list[str]) -> list[str] | None:
    """Normalised networks, or None if any of them is not a valid address or CIDR range."""
    try:
        networks = [str(ip_network(value.strip(), strict=False)) for value in values if value.strip()]
    except ValueError:
        return None
    return networks or None


class GuestConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if networks := _clean_networks(user_input[CONF_NETWORKS]):
                panel = {**user_input, CONF_NETWORKS: networks}
                return self.async_create_entry(
                    title="HA Guest", data={CONF_SECRET: new_secret()}, options={CONF_PANEL: panel, CONF_USERS: {}}
                )
            errors[CONF_NETWORKS] = "invalid_network"
        suggested = user_input or {CONF_NETWORKS: _default_network(self.hass)}
        return self.async_show_form(
            step_id="user", errors=errors,
            data_schema=self.add_suggested_values_to_schema(PANEL_SCHEMA, suggested),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GuestOptionsFlow:
        return GuestOptionsFlow()


class GuestOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._user_id: str | None = None
        self._user_conf: dict[str, Any] = {}

    @property
    def _users(self) -> dict[str, dict[str, Any]]:
        return self.config_entry.options.get(CONF_USERS, {})

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def _user_names(self) -> dict[str, str]:
        return {
            user.id: user.name or user.id
            for user in await self.hass.auth.async_get_users()
            if not user.system_generated
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = ["panel", "appearance", "safety", "add_user"]
        if self._users:
            options += ["edit_user", "remove_user"]
        if self.config_entry.options.get(CONF_PANEL, {}).get(CONF_LINK_HOURS):
            options.append("revoke_links")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_panel(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if networks := _clean_networks(user_input[CONF_NETWORKS]):
                return self._save(**{CONF_PANEL: {**user_input, CONF_NETWORKS: networks}})
            errors[CONF_NETWORKS] = "invalid_network"
        suggested = user_input or self.config_entry.options.get(CONF_PANEL, {})
        return self.async_show_form(
            step_id="panel", errors=errors,
            data_schema=self.add_suggested_values_to_schema(PANEL_SCHEMA, suggested),
        )

    async def async_step_appearance(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """How the guest page looks: a Home Assistant theme, colours, a welcome text and the layout."""
        errors: dict[str, str] = {}
        placeholders = {"entity": ""}
        if user_input is not None:
            accent = (user_input.get(CONF_ACCENT) or "").strip()
            layout = user_input.get(CONF_LAYOUT) or []
            if accent and not HEX_COLOR.match(accent):
                errors[CONF_ACCENT] = "invalid_color"
            try:
                layout = LAYOUT_SCHEMA(layout)
            except vol.Invalid:
                errors[CONF_LAYOUT] = "invalid_layout"
            else:
                allowed = self.config_entry.options.get(CONF_PANEL, {}).get(CONF_ENTITIES, [])
                used = [item if isinstance(item, str) else item["entity"] for s in layout for item in s["entities"]]
                if missing := [entity_id for entity_id in used if entity_id not in allowed]:
                    errors[CONF_LAYOUT] = "layout_not_allowed"
                    placeholders["entity"] = ", ".join(missing)
            if not errors:
                appearance = {**user_input, CONF_ACCENT: accent, CONF_LAYOUT: layout}
                return self._save(**{CONF_APPEARANCE: appearance})

        themes = [DEFAULT_THEME, *sorted(self.hass.data.get("frontend_themes", {}))]
        current = self.config_entry.options.get(CONF_APPEARANCE, {})
        if current.get(CONF_THEME) not in themes:
            current = {**current, CONF_THEME: DEFAULT_THEME}
        schema = vol.Schema({
            vol.Required(CONF_THEME, default=DEFAULT_THEME): selector.SelectSelector(
                selector.SelectSelectorConfig(options=themes, translation_key=CONF_THEME, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_MODE, default="auto"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["auto", "light", "dark"], translation_key=CONF_MODE)
            ),
            vol.Optional(CONF_ACCENT): selector.TextSelector(),
            vol.Optional(CONF_WELCOME): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Required(CONF_SHOW_ICONS, default=True): selector.BooleanSelector(),
            vol.Optional(CONF_LAYOUT): selector.ObjectSelector(),
        })
        return self.async_show_form(
            step_id="appearance", errors=errors, description_placeholders=placeholders,
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
        )

    async def async_step_safety(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """When the panel answers, and who hears about what guests do."""
        errors: dict[str, str] = {}
        if user_input is not None:
            states = [s.strip() for s in user_input.get(CONF_PANEL_CONDITION_STATES, []) if s.strip()]
            if user_input.get(CONF_PANEL_CONDITION_ENTITY) and not states:
                errors[CONF_PANEL_CONDITION_STATES] = "no_states"
            else:
                safety = {**user_input, CONF_PANEL_CONDITION_STATES: states}
                return self._save(**{CONF_SAFETY: safety})

        services = sorted(s for s in self.hass.services.async_services_for_domain("notify") if s != "send_message")
        schema = vol.Schema({
            vol.Required(CONF_REQUIRE_HOME, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_PANEL_CONDITION_ENTITY): selector.EntitySelector(),
            vol.Optional(CONF_PANEL_CONDITION_STATES, default=[]): TEXT_LIST,
            vol.Optional(CONF_NOTIFY, default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=services, multiple=True, custom_value=True)
            ),
            vol.Required(CONF_NOTIFY_ON, default=DEFAULT_NOTIFY_ON): selector.SelectSelector(
                selector.SelectSelectorConfig(options=NOTIFY_EVENTS, multiple=True, translation_key=CONF_NOTIFY_ON)
            ),
        })
        current = user_input or self.config_entry.options.get(CONF_SAFETY, {})
        return self.async_show_form(
            step_id="safety", errors=errors,
            data_schema=self.add_suggested_values_to_schema(schema, current),
        )

    async def async_step_revoke_links(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Replace the signing secret, which makes every link handed out so far stop working."""
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_SECRET: new_secret()}
            )
            return self.async_abort(reason="links_revoked")
        return self.async_show_form(step_id="revoke_links", data_schema=vol.Schema({}))

    async def async_step_add_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick who to restrict. Owners and administrators are not offered: they cannot be restricted."""
        if user_input is not None:
            self._user_id = user_input[CONF_USER]
            return await self.async_step_user_settings()
        candidates = [
            selector.SelectOptionDict(value=user.id, label=user.name or user.id)
            for user in await self.hass.auth.async_get_users()
            if not user.system_generated and not user.is_owner and not user.is_admin
            and user.is_active and user.id not in self._users
        ]
        if not candidates:
            return self.async_abort(reason="no_users")
        return self.async_show_form(
            step_id="add_user",
            data_schema=vol.Schema({
                vol.Required(CONF_USER): selector.SelectSelector(selector.SelectSelectorConfig(options=candidates))
            }),
        )

    async def async_step_edit_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._user_id = user_input[CONF_USER]
            return await self.async_step_user_settings()
        return self.async_show_form(step_id="edit_user", data_schema=await self._restricted_user_schema(False))

    async def async_step_user_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._user_id is not None
        if user_input is not None:
            self._user_conf = user_input
            return await self.async_step_home_state()
        names = await self._user_names()
        return self.async_show_form(
            step_id="user_settings",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, self._users.get(self._user_id, {})),
            description_placeholders={"name": names.get(self._user_id, self._user_id)},
        )

    async def async_step_home_state(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which states of the chosen entity mean «home», shown next to what it reports right now.

        A mistyped Wi-Fi name would never match and never say so, so the current value is offered as the answer.
        """
        assert self._user_id is not None
        entity_id = self._user_conf[CONF_CONDITION_ENTITY]
        errors: dict[str, str] = {}
        if user_input is not None:
            states = [s.strip() for s in user_input[CONF_CONDITION_STATES] if s.strip()]
            if states:
                conf = {**self._user_conf, CONF_CONDITION_STATES: states}
                return self._save(**{CONF_USERS: {**self._users, self._user_id: conf}})
            errors[CONF_CONDITION_STATES] = "no_states"

        state = self.hass.states.get(entity_id)
        current = state.state if state else STATE_UNAVAILABLE
        previous = self._users.get(self._user_id, {})
        if previous.get(CONF_CONDITION_ENTITY) == entity_id and previous.get(CONF_CONDITION_STATES):
            suggested = previous[CONF_CONDITION_STATES]
        elif home := HOME_BY_DOMAIN.get(entity_id.partition(".")[0]):
            suggested = [home]
        elif current not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            suggested = [current]
        else:
            suggested = []
        return self.async_show_form(
            step_id="home_state", errors=errors,
            data_schema=self.add_suggested_values_to_schema(HOME_STATE_SCHEMA, {CONF_CONDITION_STATES: suggested}),
            description_placeholders={"entity": state.name if state else entity_id, "state": current},
        )

    async def async_step_remove_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            removed = set(user_input[CONF_USER])
            return self._save(**{CONF_USERS: {k: v for k, v in self._users.items() if k not in removed}})
        return self.async_show_form(step_id="remove_user", data_schema=await self._restricted_user_schema(True))

    async def _restricted_user_schema(self, multiple: bool) -> vol.Schema:
        names = await self._user_names()
        options = [
            selector.SelectOptionDict(value=user_id, label=names.get(user_id, user_id)) for user_id in self._users
        ]
        return vol.Schema({
            vol.Required(CONF_USER): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=multiple)
            )
        })
