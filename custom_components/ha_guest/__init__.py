"""HA Guest: let guests use a chosen set of entities, only while they are on your network.

Two kinds of guest:

* Someone visiting, with no account and no app: the panel at /guest, reachable only from the configured networks.
* Someone with a Home Assistant user and the Companion app: they keep a set of entities everywhere (for example
  Family Location), and get the «home only» entities while their phone is on your Wi-Fi.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_USERS, DOMAIN
from .panel import async_register_views
from .permissions import GuestUserManager, async_release_users

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type GuestConfigEntry = ConfigEntry[GuestUserManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Views cannot be unregistered, so they are registered once and answer 404 while the panel is off.
    async_register_views(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GuestConfigEntry) -> bool:
    manager = GuestUserManager(hass, entry.options.get(CONF_USERS, {}))
    await manager.async_start()
    entry.runtime_data = manager
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GuestConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: GuestConfigEntry) -> None:
    """Removing the integration gives every restricted user their normal access back."""
    await async_release_users(hass)


async def _reload(hass: HomeAssistant, entry: GuestConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
