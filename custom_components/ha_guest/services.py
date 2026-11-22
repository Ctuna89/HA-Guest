"""Actions for automations: create, extend and revoke named invitations.

For example, when a booking arrives: create an invitation that starts at check-in and ends at check-out, and send
its link in a notification. «create_invitation» returns the link.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .invitations import CONDITION_DOMAINS, MAX_HOURS, _describe, async_create
from .panel import panel_config

DEFAULT_DURATION = timedelta(days=1)

CREATE_SCHEMA = vol.Schema({
    vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=60)),
    vol.Optional("entities", default=[]): cv.entity_ids,
    vol.Optional("starts"): cv.datetime,
    vol.Exclusive("duration", "end"): cv.positive_time_period,
    vol.Exclusive("ends", "end"): cv.datetime,
    vol.Optional("condition_entity"): cv.entity_domain(list(CONDITION_DOMAINS)),
    vol.Optional("single_device", default=False): cv.boolean,
})
EXTEND_SCHEMA = vol.Schema({
    vol.Required("invitation"): cv.string,
    vol.Required("duration"): cv.positive_time_period,
})
REVOKE_SCHEMA = vol.Schema({vol.Required("invitation"): cv.string})


def _timestamp(value: datetime) -> int:
    """A date and time from an automation; one without a time zone is in Home Assistant's own."""
    return int(dt_util.as_timestamp(value if value.tzinfo else value.replace(tzinfo=dt_util.get_default_time_zone())))


def _context(hass: HomeAssistant) -> tuple[Any, dict[str, Any]]:
    if (found := panel_config(hass)) is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="panel_off")
    return found


def _find(runtime: Any, key: str) -> str:
    if (ref := runtime.invitations.find(key)) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_invitation", translation_placeholders={"invitation": key}
        )
    return ref


def _response(hass: HomeAssistant, runtime: Any, ref: str) -> dict[str, Any]:
    item = _describe(hass, runtime.entry.data["secret"], ref, runtime.invitations.items[ref])

    def iso(value: int | None) -> str | None:
        return dt_util.utc_from_timestamp(value).isoformat() if value else None

    return {
        "invitation": ref, "name": item["name"], "url": item["url"],
        "starts": iso(item.get("starts")), "expires": iso(item["expires"]),
    }


async def _async_create(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    runtime, conf = _context(hass)
    data = call.data
    starts = _timestamp(data["starts"]) if "starts" in data else None
    begin = starts or int(dt_util.utcnow().timestamp())
    if "ends" in data:
        seconds = _timestamp(data["ends"]) - begin
    else:
        seconds = int(data.get("duration", DEFAULT_DURATION).total_seconds())
    if not 0 < seconds <= MAX_HOURS * 3600:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="bad_period")
    try:
        ref = await async_create(
            hass, runtime, conf, name=data["name"], entities=data["entities"], seconds=seconds,
            created_by="automation", starts=starts, condition=data.get("condition_entity"),
            single_device=data["single_device"],
        )
    except ValueError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key=str(err)) from err
    return _response(hass, runtime, ref)


async def _async_extend(call: ServiceCall) -> ServiceResponse:
    runtime, _ = _context(call.hass)
    ref = _find(runtime, call.data["invitation"])
    runtime.invitations.extend(ref, int(call.data["duration"].total_seconds()))
    return _response(call.hass, runtime, ref)


async def _async_revoke(call: ServiceCall) -> None:
    runtime, _ = _context(call.hass)
    runtime.invitations.revoke(_find(runtime, call.data["invitation"]))


def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, "create_invitation", _async_create, CREATE_SCHEMA, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "extend_invitation", _async_extend, EXTEND_SCHEMA, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "revoke_invitation", _async_revoke, REVOKE_SCHEMA)
