"""Config flow: the guest panel on first setup; the panel and the restricted users from «Configure»."""

from __future__ import annotations

from ipaddress import ip_network
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALWAYS_ENTITIES,
    CONF_AWAY_READ,
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATES,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_FAMILY_LOCATION,
    CONF_HOME_ENTITIES,
    CONF_NETWORKS,
    CONF_PANEL,
    CONF_TITLE,
    CONF_USER,
    CONF_USERS,
    DEFAULT_TITLE,
    DOMAIN,
)

ENTITIES = selector.EntitySelector(selector.EntitySelectorConfig(multiple=True))
TEXT_LIST = selector.TextSelector(selector.TextSelectorConfig(multiple=True))

PANEL_SCHEMA = vol.Schema({
    vol.Required(CONF_ENABLED, default=True): selector.BooleanSelector(),
    vol.Required(CONF_TITLE, default=DEFAULT_TITLE): selector.TextSelector(),
    vol.Required(CONF_NETWORKS): TEXT_LIST,
    vol.Optional(CONF_ENTITIES, default=[]): ENTITIES,
})

USER_SCHEMA = vol.Schema({
    vol.Optional(CONF_ALWAYS_ENTITIES, default=[]): ENTITIES,
    vol.Required(CONF_FAMILY_LOCATION, default=False): selector.BooleanSelector(),
    vol.Optional(CONF_HOME_ENTITIES, default=[]): ENTITIES,
    vol.Required(CONF_CONDITION_ENTITY): selector.EntitySelector(),
    vol.Required(CONF_CONDITION_STATES): TEXT_LIST,
    vol.Required(CONF_AWAY_READ, default=False): selector.BooleanSelector(),
})


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
                    title="HA Guest", data={}, options={CONF_PANEL: panel, CONF_USERS: {}}
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
        options = ["panel", "add_user"]
        if self._users:
            options += ["edit_user", "remove_user"]
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
        errors: dict[str, str] = {}
        if user_input is not None:
            states = [s.strip() for s in user_input[CONF_CONDITION_STATES] if s.strip()]
            if states:
                conf = {**user_input, CONF_CONDITION_STATES: states}
                return self._save(**{CONF_USERS: {**self._users, self._user_id: conf}})
            errors[CONF_CONDITION_STATES] = "no_states"
        names = await self._user_names()
        return self.async_show_form(
            step_id="user_settings", errors=errors,
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or self._users.get(self._user_id, {})
            ),
            description_placeholders={"name": names.get(self._user_id, self._user_id)},
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
