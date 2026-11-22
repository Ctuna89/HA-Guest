"""HA Guest: let guests use a chosen set of entities, only while they are on your network.

Two kinds of guest:

* Someone visiting, with no account and no app: the panel at /guest, reachable only from the configured networks
  and, optionally, only through a link that expires (handed over as a QR code from the «Guest QR» sidebar page).
* Someone with a Home Assistant user and the Companion app: they keep a set of entities everywhere (for example
  Family Location), and get the «home only» entities while their phone is on your Wi-Fi.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENABLED, CONF_PANEL, CONF_SECRET, CONF_USERS, DOMAIN
from .activity import GuestRuntime
from .invitations import async_get_invitations, async_setup_invitation_commands
from .invite import async_setup_invites, async_show_invite_panel, new_secret
from .panel import async_register_views
from .services import async_setup_services
from .permissions import GuestUserManager, async_release_users

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.IMAGE, Platform.SWITCH]

type GuestConfigEntry = ConfigEntry[GuestRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Views cannot be unregistered, so they are registered once and answer 404 while the panel is off.
    async_register_views(hass)
    await async_setup_invites(hass)
    async_setup_invitation_commands(hass)
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GuestConfigEntry) -> bool:
    if CONF_SECRET not in entry.data:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_SECRET: new_secret()})
    users = GuestUserManager(hass, entry.options.get(CONF_USERS, {}))
    await users.async_start()
    entry.runtime_data = GuestRuntime(hass, entry, users)
    entry.runtime_data.invitations = await async_get_invitations(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_show_invite_panel(hass, bool(entry.options.get(CONF_PANEL, {}).get(CONF_ENABLED)))
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GuestConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.users.async_stop()
    entry.runtime_data.async_stop()
    async_show_invite_panel(hass, False)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: GuestConfigEntry) -> None:
    """Removing the integration gives every restricted user their normal access back, and forgets invitations."""
    await async_release_users(hass)
    await (await async_get_invitations(hass)).async_remove()


async def _reload(hass: HomeAssistant, entry: GuestConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
