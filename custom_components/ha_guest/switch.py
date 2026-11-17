"""«Guest panel» switch: pauses the guest panel without changing its settings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import GuestEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([GuestPanelSwitch(entry)])


class GuestPanelSwitch(GuestEntity, SwitchEntity, RestoreEntity):
    _attr_name = None  # takes the device name
    _attr_icon = "mdi:account-key"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "panel")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.runtime.panel_on = last.state != STATE_OFF

    @property
    def is_on(self) -> bool:
        return self.runtime.panel_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.runtime.panel_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.runtime.panel_on = False
        self.async_write_ha_state()
