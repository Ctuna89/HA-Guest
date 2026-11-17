"""Restricted Home Assistant users whose control depends on being at home.

Each restricted user is moved out of the built-in «Users» group (which may control everything) into a group of
their own. That group's policy lists the entities the user may always use, plus the «home only» entities while
their condition entity (typically the Companion app's Wi-Fi sensor) reports one of the configured states.

Home Assistant enforces the policy itself: entities outside it are not sent to the user's app or browser, and
entity actions on them are rejected.

Custom groups have no public API, so this module reaches into the auth store (`hass.auth._store`). Everything
that does so is kept in the three helpers at the bottom of the file.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.models import Group, User
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_ALWAYS_ENTITIES,
    CONF_AWAY_READ,
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATES,
    CONF_FAMILY_LOCATION,
    CONF_HOME_ENTITIES,
    FAMILY_LOCATION_DOMAIN,
    GROUP_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

READ_ONLY = {"read": True}


def group_id(user_id: str) -> str:
    return f"{GROUP_PREFIX}{user_id}"


class GuestUserManager:
    """Keeps every restricted user's group policy in line with whether they are home."""

    def __init__(self, hass: HomeAssistant, users: dict[str, dict[str, Any]]) -> None:
        self.hass = hass
        self._users = users
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> None:
        await async_release_users(self.hass, keep=set(self._users))

        for user_id, conf in self._users.items():
            user = await self.hass.auth.async_get_user(user_id)
            if user is None:
                _LOGGER.warning("Restricted user %s no longer exists; skipping", user_id)
                continue
            if user.is_owner:
                _LOGGER.warning("%s is the owner and cannot be restricted; skipping", user.name)
                continue
            await _async_assign(self.hass, user, self._policy(conf, home=False))
            self._apply(user_id)

            if entity := conf.get(CONF_CONDITION_ENTITY):
                self._unsubs.append(
                    async_track_state_change_event(self.hass, [entity], self._condition_changed(user_id))
                )

        if any(conf.get(CONF_FAMILY_LOCATION) for conf in self._users.values()):
            self._unsubs.append(
                self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed)
            )
        self._unsubs.append(self.hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, self._lock_all))

    async def async_stop(self) -> None:
        """Stop tracking. Users stay restricted, with the «away» policy, until the entry is removed."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._lock_all()

    def is_home(self, user_id: str) -> bool:
        conf = self._users[user_id]
        entity = conf.get(CONF_CONDITION_ENTITY)
        if not entity or (state := self.hass.states.get(entity)) is None:
            return False
        wanted = {str(s).strip().casefold() for s in conf.get(CONF_CONDITION_STATES, [])}
        return state.state.strip().casefold() in wanted

    def _policy(self, conf: dict[str, Any], home: bool) -> dict[str, Any]:
        entities: dict[str, Any] = {}
        domains: dict[str, Any] = {}

        # Listed first so that an entity in both lists ends up always allowed.
        for entity_id in conf.get(CONF_HOME_ENTITIES, []):
            if home:
                entities[entity_id] = True
            elif conf.get(CONF_AWAY_READ):
                entities[entity_id] = READ_ONLY

        if conf.get(CONF_FAMILY_LOCATION):
            # The map needs people and zones; the integration's own entities include the check-in / SOS buttons.
            domains["person"] = READ_ONLY
            domains["zone"] = READ_ONLY
            for entry in er.async_get(self.hass).entities.values():
                if entry.platform == FAMILY_LOCATION_DOMAIN:
                    entities[entry.entity_id] = True

        for entity_id in conf.get(CONF_ALWAYS_ENTITIES, []):
            entities[entity_id] = True

        return {"entities": {"entity_ids": entities, "domains": domains}}

    @callback
    def _apply(self, user_id: str, force_away: bool = False) -> None:
        home = False if force_away else self.is_home(user_id)
        _set_policy(self.hass, user_id, self._policy(self._users[user_id], home))

    def _condition_changed(self, user_id: str):
        @callback
        def _changed(event: Event) -> None:
            self._apply(user_id)

        return _changed

    @callback
    def _registry_changed(self, event: Event) -> None:
        for user_id, conf in self._users.items():
            if conf.get(CONF_FAMILY_LOCATION):
                self._apply(user_id)

    @callback
    def _lock_all(self, event: Event | None = None) -> None:
        """Fail closed: what is stored on disk never grants «home» access."""
        for user_id in self._users:
            self._apply(user_id, force_away=True)


# --- auth store access -------------------------------------------------------------------------------------


async def _async_assign(hass: HomeAssistant, user: User, policy: dict[str, Any]) -> None:
    """Make the user a member of their own restricted group, and of nothing else."""
    store = hass.auth._store  # noqa: SLF001
    gid = group_id(user.id)
    if gid not in store._groups:  # noqa: SLF001
        store._groups[gid] = Group(name=f"HA Guest: {user.name}", policy=policy, id=gid)  # noqa: SLF001
    if [group.id for group in user.groups] != [gid]:
        await hass.auth.async_update_user(user, group_ids=[gid])


@callback
def _set_policy(hass: HomeAssistant, user_id: str, policy: dict[str, Any]) -> None:
    store = hass.auth._store  # noqa: SLF001
    group = store._groups.get(group_id(user_id))  # noqa: SLF001
    if group is None or group.policy == policy:
        return
    group.policy = policy
    for user in store._users.values():  # noqa: SLF001
        if group in user.groups:
            user.invalidate_cache()
    store._async_schedule_save()  # noqa: SLF001


async def async_release_users(hass: HomeAssistant, keep: set[str] | None = None) -> None:
    """Give normal access back to every restricted user not in `keep`, and delete their groups."""
    store = hass.auth._store  # noqa: SLF001
    keep_groups = {group_id(user_id) for user_id in keep or ()}
    stale = [gid for gid in store._groups if gid.startswith(GROUP_PREFIX) and gid not in keep_groups]  # noqa: SLF001
    for gid in stale:
        for user in await hass.auth.async_get_users():
            if any(group.id == gid for group in user.groups):
                await hass.auth.async_update_user(user, group_ids=[GROUP_ID_USER])
        del store._groups[gid]  # noqa: SLF001
    if stale:
        store._async_schedule_save()  # noqa: SLF001
