"""What guests do on the panel, and whether the panel is open for them right now.

Every visit, action and rejected attempt is fired as an «ha_guest_event» (which the logbook describes, and which
automations can use) and, if the owner chose so, sent to their notify services. Repeated events from the same
guest are folded together, because the guest page asks the server for news every few seconds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_NOTIFY,
    CONF_NOTIFY_ON,
    CONF_PANEL_CONDITION_ENTITY,
    CONF_PANEL_CONDITION_STATES,
    CONF_REQUIRE_HOME,
    CONF_SAFETY,
    CONF_USERS,
    DEFAULT_NOTIFY_ON,
    EVENT_GUEST,
)
from .messages import language, notification
from .permissions import GuestUserManager

_LOGGER = logging.getLogger(__name__)

ACTIVE_SECONDS = 5 * 60  # a guest counts as «using the panel» this long after the page last checked in
SESSION_GAP = 30 * 60  # coming back after this long counts as opening the panel again
BLOCKED_REPEAT = 60 * 60  # the same rejected guest is reported at most this often


@dataclass(frozen=True)
class Guest:
    ip: str
    link: str | None = None  # a short id of the guest's link, when links are required
    expires: int | None = None
    code: str | None = None  # the name of the permanent QR code the guest scanned, if any
    scope: tuple[str, ...] | None = None  # the only entities that code allows; None for the whole panel

    @property
    def key(self) -> str:
        return f"link:{self.link}" if self.link else f"ip:{self.ip}"


class GuestRuntime:
    """Per-entry state shared by the panel views and the entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, users: GuestUserManager) -> None:
        self.hass = hass
        self.entry = entry
        self.users = users
        self.panel_on = True  # the «Guest panel» switch
        self._seen: dict[str, float] = {}
        self._reported: dict[str, float] = {}
        self._listeners: list[Callable[[], None]] = []
        self._expire: CALLBACK_TYPE | None = None

    @property
    def _safety(self) -> dict[str, Any]:
        return self.entry.options.get(CONF_SAFETY, {})

    # --- whether the panel answers -------------------------------------------------------------------------

    @callback
    def panel_open(self) -> bool:
        if not self.panel_on:
            return False
        safety = self._safety
        if safety.get(CONF_REQUIRE_HOME) and not self._household_home():
            return False
        if entity_id := safety.get(CONF_PANEL_CONDITION_ENTITY):
            state = self.hass.states.get(entity_id)
            wanted = {str(s).strip().casefold() for s in safety.get(CONF_PANEL_CONDITION_STATES, [])}
            if state is None or state.state.strip().casefold() not in wanted:
                return False
        return True

    def _household_home(self) -> bool:
        """Someone who lives here is home. Restricted users (a relative sharing location) do not count."""
        restricted = set(self.entry.options.get(CONF_USERS, {}))
        return any(
            state.state == STATE_HOME and state.attributes.get("user_id") not in restricted
            for state in self.hass.states.async_all("person")
        )

    # --- «in use» ------------------------------------------------------------------------------------------

    @property
    def active_guests(self) -> int:
        now = time.time()
        return sum(1 for seen in self._seen.values() if now - seen < ACTIVE_SECONDS)

    @property
    def last_seen(self) -> float | None:
        return max(self._seen.values(), default=None)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def async_stop(self) -> None:
        if self._expire:
            self._expire()
            self._expire = None

    # --- what guests do ------------------------------------------------------------------------------------

    @callback
    def async_seen(self, guest: Guest) -> None:
        """The guest's page loaded or checked in."""
        now = time.time()
        previous = self._seen.get(guest.key)
        was_active = self.active_guests
        self._seen[guest.key] = now
        if previous is None or now - previous > SESSION_GAP:
            self._report(guest, {"type": "open"})
        if self.active_guests != was_active or previous is None or now - previous >= ACTIVE_SECONDS:
            self._changed()
        if self._expire:
            self._expire()
        self._expire = async_call_later(self.hass, ACTIVE_SECONDS + 1, self._expired)

    @callback
    def _expired(self, _now: Any) -> None:
        self._expire = None
        self._changed()

    @callback
    def async_blocked(self, guest: Guest, reason: str) -> None:
        """A guest on the allowed network was turned away: an old or forged link, or the panel is paused."""
        key = f"{reason}:{guest.key}"
        now = time.time()
        if now - self._reported.get(key, 0) < BLOCKED_REPEAT:
            return
        self._reported[key] = now
        self._report(guest, {"type": "blocked", "reason": reason})

    @callback
    def async_action(self, guest: Guest, state: State, action: str, value: Any, context: Context) -> None:
        """Called before the service call, with its context, so the logbook says the guest caused the change."""
        self._fire(guest, self._action_data(state, action, value), context)

    @callback
    def async_action_done(self, guest: Guest, state: State, action: str, value: Any) -> None:
        self._notify(guest, self._action_data(state, action, value))

    @staticmethod
    def _action_data(state: State, action: str, value: Any) -> dict[str, Any]:
        return {"type": "action", "entity_id": state.entity_id, "name": state.name, "action": action, "value": value}

    @callback
    def _report(self, guest: Guest, data: dict[str, Any]) -> None:
        self._fire(guest, data, Context())
        self._notify(guest, data)

    @callback
    def _fire(self, guest: Guest, data: dict[str, Any], context: Context) -> None:
        self.hass.bus.async_fire(EVENT_GUEST, self._event_data(guest, data), context=context)

    @staticmethod
    def _event_data(guest: Guest, data: dict[str, Any]) -> dict[str, Any]:
        return {**data, "ip": guest.ip, "link": guest.link, "link_expires": guest.expires, "code": guest.code}

    @callback
    def _notify(self, guest: Guest, data: dict[str, Any]) -> None:
        safety = self._safety
        services = safety.get(CONF_NOTIFY) or []
        if not services or data["type"] not in safety.get(CONF_NOTIFY_ON, DEFAULT_NOTIFY_ON):
            return
        title, message = notification(language(self.hass), self._event_data(guest, data))
        for service in services:
            self.entry.async_create_background_task(
                self.hass, self._async_send(service, title, message), f"ha_guest notify {service}"
            )

    async def _async_send(self, service: str, title: str, message: str) -> None:
        try:
            await self.hass.services.async_call("notify", service, {"title": title, "message": message})
        except HomeAssistantError as err:
            _LOGGER.warning("Could not notify notify.%s: %s", service, err)
