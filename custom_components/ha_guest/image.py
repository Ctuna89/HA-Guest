"""«Guest panel QR code» image: a quick link to the guest panel, renewed before it expires.

Meant for a dashboard on a wall tablet by the door, so visitors scan it there. It always holds a link that works
for at least half of the link duration; with no link duration it is the plain panel address.
"""

from __future__ import annotations

from datetime import UTC, datetime
import io
from typing import Any

import segno

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import CONF_ENABLED, CONF_PANEL, CONF_SECRET, PANEL_URL
from .entity import GuestEntity
from .invite import _base_url, create_token, link_seconds

MIN_RENEW = 300  # never renew more often than every 5 minutes


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([GuestQrImage(hass, entry)])


def _png(url: str) -> bytes:
    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="png", scale=10, border=3)
    return buffer.getvalue()


class GuestQrImage(GuestEntity, ImageEntity):
    _attr_translation_key = "qr"
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        GuestEntity.__init__(self, entry, "qr")
        ImageEntity.__init__(self, hass)
        self._entry = entry
        self._png: bytes | None = None
        self._expires: int | None = None
        self._renew: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        return bool(self._entry.options.get(CONF_PANEL, {}).get(CONF_ENABLED))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        expires = datetime.fromtimestamp(self._expires, UTC).isoformat() if self._expires else None
        return {"expires": expires}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._renew:
            self._renew()
            self._renew = None

    async def _async_refresh(self, _now: Any = None) -> None:
        self._renew = None
        panel = self._entry.options.get(CONF_PANEL, {})
        url = f"{_base_url(self.hass).rstrip('/')}{PANEL_URL}"
        self._expires = None
        if lifetime := link_seconds(panel):
            token, self._expires = create_token(self._entry.data[CONF_SECRET], lifetime)
            url = f"{url}?t={token}"
            # Renew at half its life, so whoever scans it always gets at least half the duration.
            self._renew = async_call_later(self.hass, max(MIN_RENEW, lifetime // 2), self._async_refresh)
        self._png = await self.hass.async_add_executor_job(_png, url)
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return self._png if self.available else None
