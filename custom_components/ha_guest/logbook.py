"""Describe guest activity in the logbook, e.g. «Guest (192.168.1.20) turned off Living room lights»."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_ICON,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_GUEST
from .messages import describe, language


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    @callback
    def async_describe(event: Event) -> dict[str, Any]:
        who, what = describe(language(hass), event.data)
        entry = {LOGBOOK_ENTRY_NAME: who, LOGBOOK_ENTRY_MESSAGE: what, LOGBOOK_ENTRY_ICON: "mdi:account-key"}
        if entity_id := event.data.get("entity_id"):
            entry[LOGBOOK_ENTRY_ENTITY_ID] = entity_id
        return entry

    async_describe_event(DOMAIN, EVENT_GUEST, async_describe)
