"""Use a Home Assistant dashboard as the guest panel's content.

The dashboard is read, not shown: guests get the guest page, built from the entities the dashboard's cards name,
in the dashboard's order, under its views' and headings' titles, with the names and icons set on the cards. Those
entities are then the only ones a guest may use, so editing the dashboard is how the household changes what guests
can control.

Custom cards are not run. Any card that names an `entity` or `entities` is understood; cards that pick entities
some other way (an area card, a picture of a floor plan without elements) contribute nothing.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, valid_entity_id

DEFAULT_DASHBOARD = "lovelace"  # the URL of Home Assistant's default dashboard, stored as None by Lovelace

Plan = list[tuple[str | None, list[dict[str, Any]]]]


def _lovelace(hass: HomeAssistant) -> Any:
    return hass.data.get("lovelace")


def dashboard_options(hass: HomeAssistant) -> list[dict[str, str]]:
    """The dashboards to pick from, as select options."""
    data = _lovelace(hass)
    if data is None:
        return []
    options = []
    for url_path, dashboard in data.dashboards.items():
        title = (dashboard.config or {}).get("title") if url_path else None
        value = url_path or DEFAULT_DASHBOARD
        options.append({"value": value, "label": f"{title or value} (/{value})" if url_path else f"/{value}"})
    return sorted(options, key=lambda option: (option["value"] != DEFAULT_DASHBOARD, option["label"].casefold()))


async def async_dashboard_config(hass: HomeAssistant, url_path: str | None) -> dict[str, Any] | None:
    """The dashboard's configuration, or None if it does not exist or is auto-generated."""
    data = _lovelace(hass)
    key = None if url_path in (None, DEFAULT_DASHBOARD) else url_path
    if data is None or (dashboard := data.dashboards.get(key)) is None:
        return None
    try:
        config = await dashboard.async_load(False)
    except Exception:  # noqa: BLE001 - ConfigNotFound, an unreadable YAML file, recovery mode...
        return None
    return config if isinstance(config, dict) else None


async def async_dashboard_plan(hass: HomeAssistant, url_path: str | None) -> Plan | None:
    """The dashboard as sections of cards, or None if it does not exist or is auto-generated."""
    config = await async_dashboard_config(hass, url_path)
    return None if config is None else plan_from_config(config)


def background_from_config(config: Any) -> Any:
    """The first view's background setting, as the dashboard stores it (a CSS string or {image: ...})."""
    views = config.get("views") if isinstance(config, dict) else None
    if isinstance(views, list):
        for view in views:
            if isinstance(view, dict) and view.get("background"):
                return view["background"]
    return None


def plan_from_config(config: Any) -> Plan:
    views = config.get("views") if isinstance(config, dict) else None
    if not isinstance(views, list):
        return []
    sections: dict[str | None, list[dict[str, Any]]] = {}
    seen: set[str] = set()

    def add(title: str | None, item: dict[str, Any]) -> None:
        if item["entity"] in seen:
            return
        seen.add(item["entity"])
        sections.setdefault(title, []).append(item)

    def walk(card: Any, heading: str | None) -> str | None:
        """Collect a card's entities; return the heading that applies to the cards after it."""
        if not isinstance(card, dict):
            return heading
        if card.get("type") == "heading":
            return card.get("heading") if isinstance(card.get("heading"), str) else heading
        own = card.get("title") if isinstance(card.get("title"), str) and card.get("title") else None
        here = own or heading
        if isinstance(item := _item(card), dict):
            add(here, item)
        for key in ("entities", "elements"):
            for row in card.get(key) or [] if isinstance(card.get(key), list) else []:
                if isinstance(item := _item(row), dict):
                    add(here, item)
                elif isinstance(row, dict):
                    walk(row, here)
        if isinstance(card.get("card"), dict):  # conditional cards and the like
            walk(card["card"], here)
        inner = here
        for child in card.get("cards") or [] if isinstance(card.get("cards"), list) else []:
            inner = walk(child, inner)
        return heading

    for view in views:
        if not isinstance(view, dict):
            continue
        view_title = view.get("title") if len(views) > 1 and isinstance(view.get("title"), str) else None
        heading = view_title
        for card in view.get("cards") or [] if isinstance(view.get("cards"), list) else []:
            heading = walk(card, heading)
        for section in view.get("sections") or [] if isinstance(view.get("sections"), list) else []:
            if isinstance(section, dict):
                walk({"cards": section.get("cards"), "title": section.get("title")}, view_title)
    return list(sections.items())


def _item(card: Any) -> dict[str, Any] | None:
    """{entity, name, icon} for a card or row that names a single entity."""
    if isinstance(card, str):
        return {"entity": card} if valid_entity_id(card) else None
    if not isinstance(card, dict) or not isinstance(entity := card.get("entity"), str) or not valid_entity_id(entity):
        return None
    item: dict[str, Any] = {"entity": entity}
    if isinstance(card.get("name"), str) and card["name"]:
        item["name"] = card["name"]
    if isinstance(card.get("icon"), str) and card["icon"].startswith("mdi:"):
        item["icon"] = card["icon"]
    return item
