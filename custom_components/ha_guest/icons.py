"""Material Design icon shapes for the guest page.

The guest page cannot load Home Assistant's frontend, so the server looks icons up in the icon data that ships
with the frontend package and sends the page the SVG path of each one it needs.
"""

from __future__ import annotations

from bisect import bisect_right
import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

DATA_ICONS: HassKey[dict[str, str | None]] = HassKey("ha_guest_icons")


def _read_path(name: str) -> str | None:
    """Find one icon's path on disk. Blocking: call it in the executor."""
    try:
        import hass_frontend  # noqa: PLC0415

        folder = Path(hass_frontend.where()) / "static" / "mdi"
        parts = json.loads((folder / "iconMetadata.json").read_text("utf-8"))["parts"]
        # Each part holds the icons from its «start» name up to the next part's; the first has no start.
        index = max(bisect_right([part["start"] for part in parts[1:]], name), 0)
        return json.loads((folder / f"{parts[index]['file']}.json").read_text("utf-8")).get(name)
    except (ImportError, OSError, ValueError, KeyError, IndexError):
        return None


async def async_icon_path(hass: HomeAssistant, icon: str | None) -> str | None:
    """The SVG path (24×24 viewBox) of an «mdi:name» icon, or None for anything else or unknown."""
    if not icon or not icon.startswith("mdi:"):
        return None
    cache = hass.data.setdefault(DATA_ICONS, {})
    if icon not in cache:
        cache[icon] = await hass.async_add_executor_job(_read_path, icon[4:])
    return cache[icon]
