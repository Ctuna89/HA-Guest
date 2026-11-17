"""«In use» sensor: on while a guest has the panel open (or used it in the last few minutes)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import GuestEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([GuestsInUseSensor(entry)])


class GuestsInUseSensor(GuestEntity, BinarySensorEntity):
    _attr_translation_key = "in_use"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "in_use")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self.runtime.active_guests > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self.runtime.last_seen
        return {
            "guests": self.runtime.active_guests,
            "last_seen": datetime.fromtimestamp(last, UTC).isoformat() if last else None,
        }
