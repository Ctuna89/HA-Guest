"""Tests for the guest panel and the restricted users."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import Unauthorized
from homeassistant.setup import async_setup_component

from custom_components.ha_guest.const import DOMAIN
from custom_components.ha_guest.permissions import group_id

LIVING = "input_boolean.living"
BEDROOM = "input_boolean.bedroom"
ALWAYS = "input_boolean.always"
WIFI = "sensor.phone_wifi"


async def _setup(hass, panel=None, users=None):
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"living": None, "bedroom": None, "always": None}}
    )
    panel = {"enabled": True, "title": "Casa", "networks": ["127.0.0.0/8"], "entities": [LIVING], **(panel or {})}
    entry = MockConfigEntry(domain=DOMAIN, options={"panel": panel, "users": users or {}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_panel_lists_and_controls_only_allowed_entities(hass, hass_client_no_auth):
    await _setup(hass)
    client = await hass_client_no_auth()

    assert (await client.get("/guest")).status == 200
    data = await (await client.get("/api/ha_guest/states")).json()
    assert data["title"] == "Casa"
    assert [e["entity_id"] for e in data["entities"]] == [LIVING]

    res = await client.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"})
    assert res.status == 200
    assert hass.states.get(LIVING).state == "on"

    res = await client.post("/api/ha_guest/action", json={"entity_id": BEDROOM, "action": "turn_on"})
    assert res.status == 403
    assert hass.states.get(BEDROOM).state == "off"

    for body in ({"entity_id": LIVING, "action": "brightness", "value": 50}, {"entity_id": LIVING}, [1]):
        assert (await client.post("/api/ha_guest/action", json=body)).status == 400
    res = await client.post("/api/ha_guest/action", data={"entity_id": LIVING, "action": "turn_off"})
    assert res.status == 400
    assert hass.states.get(LIVING).state == "on"


async def test_panel_rejects_other_networks(hass, hass_client_no_auth):
    await _setup(hass, panel={"networks": ["192.168.50.0/24"]})
    client = await hass_client_no_auth()

    res = await client.get("/api/ha_guest/states")
    assert res.status == 403
    assert (await res.json()) == {"error": "network", "ip": "127.0.0.1"}
    res = await client.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"})
    assert res.status == 403
    assert hass.states.get(LIVING).state == "off"


async def test_panel_rejects_cloud_connections(hass, hass_client_no_auth, monkeypatch):
    await _setup(hass)
    monkeypatch.setattr("custom_components.ha_guest.panel.is_cloud_connection", lambda hass: True)
    client = await hass_client_no_auth()
    assert (await client.get("/api/ha_guest/states")).status == 403


async def test_panel_disabled(hass, hass_client_no_auth):
    await _setup(hass, panel={"enabled": False})
    client = await hass_client_no_auth()
    assert (await client.get("/guest")).status == 404
    assert (await client.get("/api/ha_guest/states")).status == 404


async def _turn_on(hass, user, entity_id):
    await hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": entity_id}, blocking=True, context=Context(user_id=user.id)
    )


async def _create_user(hass):
    await hass.auth.async_create_user("Owner")  # the first user becomes the owner, who cannot be restricted
    return await hass.auth.async_create_user("Suegra", group_ids=[GROUP_ID_USER])


def _user_conf(**extra):
    return {
        "always_entities": [ALWAYS], "family_location": False, "home_entities": [LIVING],
        "condition_entity": WIFI, "condition_states": ["MiWifi"], "away_read": False, **extra,
    }


async def test_restricted_user_controls_home_entities_only_on_wifi(hass):
    user = await _create_user(hass)
    hass.states.async_set(WIFI, "<not connected>")
    entry = await _setup(hass, users={user.id: _user_conf()})

    assert [g.id for g in user.groups] == [group_id(user.id)]
    await _turn_on(hass, user, ALWAYS)
    with pytest.raises(Unauthorized):
        await _turn_on(hass, user, LIVING)
    assert not user.permissions.check_entity(LIVING, "read")

    hass.states.async_set(WIFI, "miwifi ")
    await hass.async_block_till_done()
    await _turn_on(hass, user, LIVING)
    assert hass.states.get(LIVING).state == "on"
    with pytest.raises(Unauthorized):
        await _turn_on(hass, user, BEDROOM)

    hass.states.async_set(WIFI, "unavailable")
    await hass.async_block_till_done()
    assert not user.permissions.check_entity(LIVING, "control")

    # Unloading keeps the user locked down; removing the integration frees them.
    hass.states.async_set(WIFI, "MiWifi")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not user.permissions.check_entity(LIVING, "control")
    assert user.permissions.check_entity(ALWAYS, "control")

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert [g.id for g in user.groups] == [GROUP_ID_USER]
    assert await hass.auth.async_get_group(group_id(user.id)) is None
    await _turn_on(hass, user, BEDROOM)


async def test_away_read_only_and_family_location(hass, entity_registry):
    user = await _create_user(hass)
    button = entity_registry.async_get_or_create("button", "family_location", "sos").entity_id
    entry = await _setup(hass, users={user.id: _user_conf(away_read=True, family_location=True)})

    perms = user.permissions
    assert perms.check_entity(LIVING, "read") and not perms.check_entity(LIVING, "control")
    assert perms.check_entity(button, "control")
    assert perms.check_entity("person.suegra", "read") and not perms.check_entity("person.suegra", "control")

    late = entity_registry.async_get_or_create("sensor", "family_location", "place").entity_id
    await hass.async_block_till_done()
    assert user.permissions.check_entity(late, "read")

    # Dropping the user from the options gives them normal access back.
    hass.config_entries.async_update_entry(entry, options={**entry.options, "users": {}})
    await hass.async_block_till_done()
    assert [g.id for g in user.groups] == [GROUP_ID_USER]


async def test_config_and_options_flow(hass):
    user = await _create_user(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    form = {"enabled": True, "title": "Casa", "networks": ["nope"], "entities": [LIVING]}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"] == {"networks": "invalid_network"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**form, "networks": ["192.168.1.7/24"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options["panel"]["networks"] == ["192.168.1.0/24"]
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["panel", "add_user"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_user"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"user": user.id})
    assert result["step_id"] == "user_settings"
    result = await hass.config_entries.options.async_configure(result["flow_id"], _user_conf())
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["users"][user.id]["condition_states"] == ["MiWifi"]
    assert [g.id for g in user.groups] == [group_id(user.id)]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["panel", "add_user", "edit_user", "remove_user"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remove_user"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"user": [user.id]})
    await hass.async_block_till_done()
    assert entry.options["users"] == {}
    assert [g.id for g in user.groups] == [GROUP_ID_USER]


async def test_app_only_receives_permitted_entities(hass, hass_ws_client):
    """What the Companion app or browser of a restricted user is sent over the websocket."""
    user = await _create_user(hass)
    hass.states.async_set(WIFI, "other")
    await _setup(hass, users={user.id: _user_conf()})
    refresh = await hass.auth.async_create_refresh_token(user, "https://example.com")
    client = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))

    await client.send_json({"id": 1, "type": "get_states"})
    assert [s["entity_id"] for s in (await client.receive_json())["result"]] == [ALWAYS]

    await client.send_json({
        "id": 2, "type": "call_service", "domain": "input_boolean", "service": "turn_on",
        "service_data": {"entity_id": LIVING},
    })
    assert not (await client.receive_json())["success"]
    assert hass.states.get(LIVING).state == "off"

    hass.states.async_set(WIFI, "MiWifi")
    await hass.async_block_till_done()
    await client.send_json({"id": 3, "type": "get_states"})
    assert {s["entity_id"] for s in (await client.receive_json())["result"]} == {ALWAYS, LIVING}
