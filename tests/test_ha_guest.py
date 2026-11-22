"""Tests for the guest panel and the restricted users."""

import pytest
from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import Unauthorized
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.ha_guest.const import DOMAIN
from custom_components.ha_guest.permissions import group_id

LIVING = "input_boolean.living"
BEDROOM = "input_boolean.bedroom"
ALWAYS = "input_boolean.always"
WIFI = "sensor.phone_wifi"


async def _setup(hass, panel=None, users=None, safety=None):
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"living": None, "bedroom": None, "always": None}}
    )
    panel = {"enabled": True, "title": "Casa", "networks": ["127.0.0.0/8"], "entities": [LIVING], **(panel or {})}
    options = {"panel": panel, "users": users or {}}
    if safety is not None:
        options["safety"] = safety
    entry = MockConfigEntry(domain=DOMAIN, options=options)
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
    assert [e["entity_id"] for s in data["sections"] for e in s["entities"]] == [LIVING]

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


def _user_form():
    conf = _user_conf()
    conf.pop("condition_states")
    return conf


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
    assert entry.options["panel"]["link_hours"] == 12  # new installs require expiring links
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["panel", "appearance", "safety", "codes", "add_user", "revoke_links"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_user"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"user": user.id})
    assert result["step_id"] == "user_settings"
    hass.states.async_set(WIFI, "MiWifi", {"friendly_name": "Phone Wi-Fi"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], _user_form())
    # The second step shows what the sensor reports now and offers it as the answer.
    assert result["step_id"] == "home_state"
    assert result["description_placeholders"] == {"entity": "Phone Wi-Fi", "state": "MiWifi"}
    schema_key = next(iter(result["data_schema"].schema))
    assert schema_key.description == {"suggested_value": ["MiWifi"]}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"condition_states": ["MiWifi"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["users"][user.id]["condition_states"] == ["MiWifi"]
    assert [g.id for g in user.groups] == [group_id(user.id)]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == [
        "panel", "appearance", "safety", "codes", "add_user", "edit_user", "remove_user", "revoke_links",
    ]
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


async def _invite(client, msg_id=1):
    await client.send_json({"id": msg_id, "type": "ha_guest/invite"})
    return await client.receive_json()


async def test_expiring_links(hass, hass_client_no_auth, hass_ws_client, freezer):
    entry = await _setup(hass, panel={"link_hours": 2})
    http = await hass_client_no_auth()
    ws = await hass_ws_client(hass)

    # On the right network, but without a link from the household.
    res = await http.get("/api/ha_guest/states")
    assert res.status == 403 and (await res.json()) == {"error": "link", "message": ""}
    assert (await http.get("/api/ha_guest/states", headers={"X-Guest-Token": "9999999999.abc"})).status == 403

    invite = (await _invite(ws))["result"]
    assert "/guest?t=" in invite["url"] and invite["svg"].startswith("<svg")
    headers = {"X-Guest-Token": invite["url"].split("?t=")[1]}
    res = await http.get("/api/ha_guest/states", headers=headers)
    assert res.status == 200 and (await res.json())["expires"] == invite["expires"]
    body = {"entity_id": LIVING, "action": "turn_on"}
    assert (await http.post("/api/ha_guest/action", json=body)).status == 403
    assert (await http.post("/api/ha_guest/action", json=body, headers=headers)).status == 200

    freezer.tick(2 * 3600 + 1)
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403

    # Revoking kills links that have not expired yet.
    headers = {"X-Guest-Token": (await _invite(ws, 2))["result"]["url"].split("?t=")[1]}
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 200
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "revoke_links" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "revoke_links"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["reason"] == "links_revoked"
    await hass.async_block_till_done()
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403


async def test_shortening_the_duration_cuts_existing_links(hass, hass_client_no_auth, hass_ws_client):
    entry = await _setup(hass, panel={"link_hours": 24})
    http = await hass_client_no_auth()
    headers = {"X-Guest-Token": (await _invite(await hass_ws_client(hass)))["result"]["url"].split("?t=")[1]}
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 200

    panel = {**entry.options["panel"], "link_hours": 1}
    hass.config_entries.async_update_entry(entry, options={**entry.options, "panel": panel})
    await hass.async_block_till_done()
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403


async def test_who_can_invite(hass, hass_ws_client):
    """Any household member can create a code, administrator or not; a restricted guest cannot."""
    guest = await _create_user(hass)
    member = await hass.auth.async_create_user("Hija", group_ids=[GROUP_ID_USER])
    await _setup(hass, users={guest.id: _user_conf()})

    async def invite_as(user):
        refresh = await hass.auth.async_create_refresh_token(user, "https://example.com")
        client = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))
        return await _invite(client)

    result = await invite_as(member)
    assert result["success"] and result["result"]["expires"] is None  # links do not expire by default
    assert result["result"]["url"].endswith("/guest")
    assert (await invite_as(guest))["error"]["code"] == "unauthorized"
    assert "guest-qr" in hass.data["frontend_panels"]


async def test_appearance(hass, hass_client_no_auth, entity_registry):
    """Theme, accent, welcome text and a layout with the owner's own names and icons."""
    entry = await _setup(hass, panel={"entities": [LIVING, BEDROOM]})
    hass.data["frontend_themes"] = {
        "Ocean": {
            "primary-color": "#0277bd", "card-background-color": "var(--other)",
            "modes": {"dark": {"primary-background-color": "#001018"}},
        }
    }
    client = await hass_client_no_auth()

    data = await (await client.get("/api/ha_guest/states")).json()
    assert data["appearance"] == {
        "mode": "auto", "theme": {"light": {}, "dark": {}}, "accent": None, "welcome": "", "background": None,
    }
    assert [s["title"] for s in data["sections"]] == [None]
    assert data["sections"][0]["entities"][0]["icon"].startswith("M")  # default icon for the domain

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "appearance"})
    form = {"theme": "Ocean", "mode": "dark", "accent": "orange", "welcome": "Wi-Fi: casa123", "show_icons": True}
    layout = [{"title": "Downstairs", "entities": [BEDROOM, {"entity": ALWAYS}]}]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form, "layout": layout})
    assert result["errors"] == {"accent": "invalid_color", "layout": "layout_not_allowed"}
    assert result["description_placeholders"] == {"entity": ALWAYS}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**form, "accent": "#ff9800", "layout": {"entities": "nope"}}
    )
    assert result["errors"] == {"layout": "invalid_layout"}
    layout = [{"title": "Downstairs", "entities": [BEDROOM, {"entity": LIVING, "name": "Sofa", "icon": "mdi:sofa"}]}]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**form, "accent": "#ff9800", "layout": layout}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    data = await (await client.get("/api/ha_guest/states")).json()
    assert data["appearance"] == {
        "mode": "dark", "accent": "#ff9800", "welcome": "Wi-Fi: casa123", "background": None,
        "theme": {"light": {"--accent": "#0277bd"}, "dark": {"--accent": "#0277bd", "--bg": "#001018"}},
    }
    section, = data["sections"]
    assert section["title"] == "Downstairs"
    assert [(e["entity_id"], e["name"]) for e in section["entities"]] == [(BEDROOM, "bedroom"), (LIVING, "Sofa")]
    default_icon, sofa_icon = (e["icon"] for e in section["entities"])
    assert sofa_icon.startswith("M") and sofa_icon != default_icon

    # An entity taken off the allowed list disappears even if the layout still names it.
    panel = {**entry.options["panel"], "entities": [BEDROOM]}
    hass.config_entries.async_update_entry(entry, options={**entry.options, "panel": panel})
    await hass.async_block_till_done()
    data = await (await client.get("/api/ha_guest/states")).json()
    assert [e["entity_id"] for e in data["sections"][0]["entities"]] == [BEDROOM]
    res = await client.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"})
    assert res.status == 403



def _entity(entity_registry, entry, platform, key):
    return entity_registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")


async def test_activity_is_logged_and_notified(hass, hass_client_no_auth, entity_registry, freezer):
    notify = async_mock_service(hass, "notify", "phone")
    events = async_capture_events(hass, "ha_guest_event")
    entry = await _setup(hass, safety={"notify": ["phone"], "notify_on": ["open", "action", "blocked"]})
    client = await hass_client_no_auth()
    in_use = _entity(entity_registry, entry, "binary_sensor", "in_use")
    assert hass.states.get(in_use).state == "off"

    # Opening the page is one visit, however often it checks in.
    for _ in range(3):
        assert (await client.get("/api/ha_guest/states")).status == 200
    await hass.async_block_till_done()
    assert [e.data["type"] for e in events] == ["open"]
    assert events[0].data["ip"] == "127.0.0.1"
    assert hass.states.get(in_use).state == "on"
    assert hass.states.get(in_use).attributes["guests"] == 1

    # «toggle» is recorded as what it did, and the light's change carries the guest's context.
    assert (await client.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "toggle"})).status == 200
    await hass.async_block_till_done()
    action = events[-1]
    assert action.data | {"ip": None} == {
        "type": "action", "entity_id": LIVING, "name": "living", "action": "turn_on", "value": None,
        "ip": None, "link": None, "link_expires": None, "code": None,
    }
    assert hass.states.get(LIVING).context.id == action.context.id
    assert [c.data["message"] for c in notify] == [
        "Guest (127.0.0.1) opened the guest panel.", "Guest (127.0.0.1) turned on living.",
    ]
    assert notify[0].data["title"] == "Guest panel"

    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(in_use).state == "off"


async def test_logbook_description(hass):
    from custom_components.ha_guest import logbook

    described = {}
    logbook.async_describe_events(hass, lambda domain, event, fn: described.setdefault(event, fn))

    class FakeEvent:
        data = {"type": "action", "entity_id": LIVING, "name": "Living", "action": "turn_off", "ip": "10.0.0.5"}

    entry = described["ha_guest_event"](FakeEvent())
    assert entry["name"] == "Guest (10.0.0.5)" and entry["message"] == "turned off Living"
    assert entry["entity_id"] == LIVING

    hass.config.language = "es"
    FakeEvent.data = {"type": "blocked", "reason": "link", "ip": "10.0.0.5"}
    entry = described["ha_guest_event"](FakeEvent())
    assert entry["name"] == "Invitado (10.0.0.5)"
    assert entry["message"] == "intentó entrar con un enlace de invitado vencido o no válido"


async def test_old_link_is_reported_once(hass, hass_client_no_auth, hass_ws_client, freezer):
    notify = async_mock_service(hass, "notify", "phone")
    await _setup(hass, panel={"link_hours": 1}, safety={"notify": ["phone"], "notify_on": ["blocked"]})
    client = await hass_client_no_auth()
    ws = await hass_ws_client(hass)
    headers = {"X-Guest-Token": (await _invite(ws))["result"]["url"].split("?t=")[1]}

    assert (await client.get("/api/ha_guest/states")).status == 403  # no link at all: not reported
    freezer.tick(timedelta(hours=2))
    for _ in range(5):  # the page keeps checking in with its old link
        assert (await client.get("/api/ha_guest/states", headers=headers)).status == 403
    await hass.async_block_till_done()
    assert [c.data["message"] for c in notify] == [
        "Guest (127.0.0.1) tried to get in with an expired or invalid guest link."
    ]


async def test_panel_switch_and_conditions(hass, hass_client_no_auth, entity_registry):
    guest = await _create_user(hass)
    events = async_capture_events(hass, "ha_guest_event")
    entry = await _setup(
        hass, users={guest.id: _user_conf()},
        safety={"require_home": True, "condition_entity": "alarm_control_panel.home", "condition_states": ["disarmed"]},
    )
    client = await hass_client_no_auth()
    switch = _entity(entity_registry, entry, "switch", "panel")
    assert hass.states.get(switch).state == "on"

    async def status():
        res = await client.get("/api/ha_guest/states")
        return res.status, (await res.json()).get("error")

    hass.states.async_set("alarm_control_panel.home", "disarmed")
    # Only the restricted relative is home: that is not someone from the household.
    hass.states.async_set("person.suegra", "home", {"user_id": guest.id})
    assert await status() == (403, "paused")
    hass.states.async_set("person.jaime", "home", {"user_id": "owner"})
    assert await status() == (200, None)
    hass.states.async_set("alarm_control_panel.home", "armed_away")
    assert await status() == (403, "paused")
    res = await client.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"})
    assert res.status == 403 and hass.states.get(LIVING).state == "off"
    hass.states.async_set("alarm_control_panel.home", "disarmed")

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert await status() == (403, "paused")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert await status() == (200, None)

    # Being paused is reported once, and the switch survives a reload.
    assert [e.data.get("reason") for e in events if e.data["type"] == "blocked"] == ["paused"]
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off"
    assert await status() == (403, "paused")


async def test_safety_options_flow(hass):
    async_mock_service(hass, "notify", "mobile_app_phone")
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "safety"})
    form = {"require_home": True, "condition_entity": "alarm_control_panel.home", "condition_states": [" "],
            "notify": ["mobile_app_phone"], "notify_on": ["blocked"]}
    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["errors"] == {"condition_states": "no_states"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**form, "condition_states": ["disarmed "]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["safety"]["condition_states"] == ["disarmed"]
    assert entry.options["panel"]["entities"] == [LIVING]



async def test_layout_texts_and_state_icons(hass, hass_client_no_auth):
    """The owner's words for states and buttons, per-state icons, and icons that follow the state by default."""
    lock = "lock.front_door"
    hass.states.async_set(lock, "locked", {"friendly_name": "PuertaPeten"})
    hass.states.async_set("light.porch", "off", {"friendly_name": "PuertaExterior Light"})
    entry = await _setup(hass, panel={"entities": [lock, "light.porch"]})
    client = await hass_client_no_auth()

    async def cards():
        data = await (await client.get("/api/ha_guest/states")).json()
        return {e["entity_id"]: e for s in data["sections"] for e in s["entities"]}

    # Without a layout the padlock opens with the lock, and a light that is off gets an outline bulb.
    locked = (await cards())[lock]["icon"]
    hass.states.async_set(lock, "unlocked", {"friendly_name": "PuertaPeten"})
    by_id = await cards()
    assert by_id[lock]["icon"] != locked
    assert by_id[lock]["state_text"] is None and by_id[lock]["labels"] == {}

    layout = [{"title": "Entrance", "entities": [
        {"entity": lock, "name": "Front door", "icon": {"locked": "mdi:door-closed-lock", "unlocked": "mdi:door-open"},
         "states": {"locked": "Closed", "unlocked": "Open"}, "buttons": {"unlock": "Open the door", "lock": "Close"}},
        {"entity": "light.porch", "name": "Porch light", "icon": "mdi:outdoor-lamp"},
    ]}]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "appearance"})
    form = {"theme": "default", "mode": "auto", "show_icons": True}
    bad = [{"entities": [{"entity": lock, "buttons": {"explode": "Boom"}}]}]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form, "layout": bad})
    assert result["errors"] == {"layout": "invalid_layout"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form, "layout": layout})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    door = (await cards())[lock]
    assert (door["name"], door["state_text"]) == ("Front door", "Open")
    assert door["labels"] == {"unlock": "Open the door", "lock": "Close"}
    open_icon = door["icon"]
    hass.states.async_set(lock, "locked", {"friendly_name": "PuertaPeten"})
    door = (await cards())[lock]
    assert door["state_text"] == "Closed" and door["icon"] not in (open_icon, locked)



GUEST_DASHBOARD = {"views": [{"title": "Home", "type": "sections", "sections": [
    {"cards": [
        {"type": "heading", "heading": "Entrance"},
        {"type": "tile", "entity": LIVING, "name": "Sofa lights", "icon": "mdi:sofa"},
        {"type": "custom:mushroom-light-card", "entity": ALWAYS},
        {"type": "markdown", "content": "Welcome"},
    ]},
    {"cards": [
        {"type": "entities", "title": "Bedroom", "entities": [BEDROOM, {"type": "divider"}, {"entity": LIVING}]},
    ]},
]}]}


async def _save_dashboard(client, msg_id, config):
    await client.send_json({"id": msg_id, "type": "lovelace/config/save", "url_path": "guest-view", "config": config})
    assert (await client.receive_json())["success"]


async def test_dashboard_as_source(hass, hass_client_no_auth, hass_ws_client):
    # Set up everything before the first client starts the web server: views cannot be added after that.
    assert await async_setup_component(hass, "lovelace", {})
    await _setup(hass, panel={"source": "dashboard", "dashboard": "guest-view", "entities": []})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "lovelace/dashboards/create", "url_path": "guest-view", "title": "Guests"})
    assert (await ws.receive_json())["success"]
    await _save_dashboard(ws, 2, GUEST_DASHBOARD)
    client = await hass_client_no_auth()
    data = await (await client.get("/api/ha_guest/states")).json()
    assert [(s["title"], [(e["entity_id"], e["name"]) for e in s["entities"]]) for s in data["sections"]] == [
        ("Entrance", [(LIVING, "Sofa lights"), (ALWAYS, "always")]),
        ("Bedroom", [(BEDROOM, "bedroom")]),
    ]

    body = {"entity_id": BEDROOM, "action": "turn_on"}
    assert (await client.post("/api/ha_guest/action", json=body)).status == 200

    # Taking a card off the dashboard takes the entity away from guests.
    trimmed = {"views": [{"cards": [{"type": "tile", "entity": LIVING}]}]}
    await _save_dashboard(ws, 3, trimmed)
    assert (await client.post("/api/ha_guest/action", json={**body, "action": "turn_off"})).status == 403
    data = await (await client.get("/api/ha_guest/states")).json()
    assert [e["entity_id"] for s in data["sections"] for e in s["entities"]] == [LIVING]

    # A dashboard that disappears allows nothing.
    await ws.send_json({"id": 4, "type": "lovelace/dashboards/list"})
    dashboard_id = (await ws.receive_json())["result"][0]["id"]
    await ws.send_json({"id": 5, "type": "lovelace/dashboards/delete", "dashboard_id": dashboard_id})
    assert (await ws.receive_json())["success"]
    assert (await client.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"})).status == 403


async def test_dashboard_choice_in_options(hass, hass_ws_client):
    assert await async_setup_component(hass, "lovelace", {})
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "lovelace/dashboards/create", "url_path": "guest-view", "title": "Guests"})
    assert (await ws.receive_json())["success"]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "panel"})
    dashboards = next(k for k in result["data_schema"].schema if k == "dashboard")
    options = result["data_schema"].schema[dashboards].config["options"]
    assert [o["value"] for o in options] == ["lovelace", "guest-view"]

    form = {**entry.options["panel"], "source": "dashboard"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["errors"] == {"dashboard": "no_dashboard"}
    # The auto-generated default dashboard and a dashboard with no entity cards are refused.
    for dashboard in ("lovelace", "guest-view"):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {**form, "dashboard": dashboard})
        assert result["errors"] == {"dashboard": "dashboard_empty"}
    await _save_dashboard(ws, 2, GUEST_DASHBOARD)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form, "dashboard": "guest-view"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["panel"]["dashboard"] == "guest-view"


def test_plan_from_config():
    from custom_components.ha_guest.dashboard import plan_from_config

    config = {"views": [
        {"title": "Downstairs", "cards": [
            {"type": "vertical-stack", "title": "Kitchen", "cards": [
                {"type": "button", "entity": "light.kitchen", "icon": "/local/x.png"},
                {"type": "conditional", "card": {"type": "light", "entity": "light.island", "name": "Island"}},
            ]},
            {"type": "glance", "entities": ["sensor.temp", "not an entity"]},
            {"type": "picture-elements", "elements": [{"type": "state-icon", "entity": "lock.door"}]},
            {"type": "area", "area": "garage"},
        ]},
        {"title": "Upstairs", "cards": [{"type": "entity", "entity": "light.kitchen"}, {"type": "tile", "entity": "fan.attic"}]},
    ]}
    assert plan_from_config(config) == [
        ("Kitchen", [{"entity": "light.kitchen"}, {"entity": "light.island", "name": "Island"}]),
        ("Downstairs", [{"entity": "sensor.temp"}, {"entity": "lock.door"}]),
        ("Upstairs", [{"entity": "fan.attic"}]),
    ]
    assert plan_from_config({"strategy": {"type": "original-states"}}) == []



@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1:8123", 200), ("[::1]:8123", 200), ("homeassistant.local:8123", 200), ("localhost", 200),
        ("ha.home.example:8123", 200),  # the configured internal URL
        ("evil.example", 403), ("evil.example:8123", 403), ("127.0.0.1.evil.example", 403), ("", 403),
    ],
)
async def test_panel_only_answers_its_own_address(hass, hass_client_no_auth, host, status):
    """DNS rebinding: another website pointing its own name at Home Assistant gets nothing."""
    await _setup(hass)
    await hass.config.async_update(internal_url="http://ha.home.example:8123")
    client = await hass_client_no_auth()
    res = await client.get("/api/ha_guest/states", headers={"Host": host})
    assert res.status == status
    res = await client.post(
        "/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"}, headers={"Host": host}
    )
    assert res.status == (200 if status == 200 else 403)
    assert hass.states.get(LIVING).state == ("on" if status == 200 else "off")



async def test_states_in_home_assistant_words_and_tone(hass, hass_client_no_auth):
    """Translated states, weather with its temperature, and the light's colour temperature."""
    hass.config.language = "es"
    hass.states.async_set("media_player.sonos", "idle", {"friendly_name": "Sonos"})
    hass.states.async_set(
        "weather.home", "clear-night", {"friendly_name": "Viña", "temperature": 18, "temperature_unit": "°C"}
    )
    calls = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.living", "on", {
        "friendly_name": "Living", "supported_color_modes": ["color_temp"], "color_temp_kelvin": 3000,
        "min_color_temp_kelvin": 2200, "max_color_temp_kelvin": 6500,
    })
    await _setup(hass, panel={"entities": ["media_player.sonos", "weather.home", "light.living"]})
    client = await hass_client_no_auth()

    data = await (await client.get("/api/ha_guest/states")).json()
    by_id = {e["entity_id"]: e for s in data["sections"] for e in s["entities"]}
    assert by_id["media_player.sonos"]["state_label"] == "Inactivo"
    assert by_id["weather.home"]["state_label"] == "Despejado, de noche"
    assert by_id["weather.home"]["attributes"]["temperature"] == 18
    assert by_id["weather.home"]["icon"] != by_id["media_player.sonos"]["icon"]
    assert by_id["light.living"]["attributes"]["min_color_temp_kelvin"] == 2200

    body = {"entity_id": "light.living", "action": "color_temp", "value": 4000}
    assert (await client.post("/api/ha_guest/action", json=body)).status == 200
    assert calls[-1].data == {"entity_id": "light.living", "color_temp_kelvin": 4000}
    for value in (1000, 9000, "warm"):
        assert (await client.post("/api/ha_guest/action", json={**body, "value": value})).status == 400


async def test_background_from_dashboard_theme_or_setting(hass, hass_client_no_auth, hass_ws_client):
    assert await async_setup_component(hass, "lovelace", {})
    entry = await _setup(hass, panel={"source": "dashboard", "dashboard": "guest-view", "entities": []})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "lovelace/dashboards/create", "url_path": "guest-view", "title": "Guests"})
    assert (await ws.receive_json())["success"]
    config = {"views": [{"background": 'center / cover no-repeat url("/local/sofa.jpg") fixed',
                         "cards": [{"type": "tile", "entity": LIVING}]}]}
    await _save_dashboard(ws, 2, config)
    client = await hass_client_no_auth()

    async def background():
        return (await (await client.get("/api/ha_guest/states")).json())["appearance"]["background"]

    assert await background() == "/local/sofa.jpg"
    # Something that could break out of the page's CSS is dropped.
    config["views"][0]["background"] = 'url("/local/x.jpg\\") ; color: red")'
    await _save_dashboard(ws, 3, config)
    assert await background() is None
    # The owner's own choice wins.
    options = {**entry.options, "appearance": {"background": "/local/guests.jpg"}}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert await background() == "/local/guests.jpg"



async def _add_code(hass, entry, name, entities, days, ref=None, new_qr=False):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "codes"})
    if ref is None:
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_code"})
        form = {"name": name, "entities": entities, "days": days}
    else:
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_code"})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"code": ref})
        form = {"name": name, "entities": entities, "days": days, "new_qr": new_qr}
    assert result["step_id"] == "code_settings"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_permanent_qr_code(hass, hass_client_no_auth, hass_ws_client, freezer):
    """A printed code that never changes: each phone gets a session for some days, limited to the code's entities."""
    events = async_capture_events(hass, "ha_guest_event")
    entry = await _setup(hass, panel={"entities": [LIVING, BEDROOM], "link_hours": 2})
    await _add_code(hass, entry, "Guest bedroom", [BEDROOM], 3)
    (ref, code), = entry.options["codes"].items()
    http = await hass_client_no_auth()

    invite = (await _invite(await hass_ws_client(hass)))["result"]
    printed, = invite["codes"]
    assert printed["name"] == "Guest bedroom" and printed["days"] == 3
    assert printed["url"].endswith(f"/guest?k={code['key']}") and printed["svg"].startswith("<svg")

    async def redeem(key):
        res = await http.post("/api/ha_guest/redeem", json={"key": key})
        return res.status, await res.json()

    assert (await redeem("nope"))[0] == 403
    status, body = await redeem(code["key"])
    assert status == 200
    headers = {"X-Guest-Token": body["token"]}

    data = await (await http.get("/api/ha_guest/states", headers=headers)).json()
    assert data["code"] == "Guest bedroom"
    assert [e["entity_id"] for s in data["sections"] for e in s["entities"]] == [BEDROOM]
    assert (await http.post("/api/ha_guest/action", json={"entity_id": BEDROOM, "action": "turn_on"},
                            headers=headers)).status == 200
    assert (await http.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"},
                            headers=headers)).status == 403
    assert events[-1].data["code"] == "Guest bedroom"

    # The session outlives the 2-hour link duration, but not the code's 3 days; then the same printout works again.
    freezer.tick(timedelta(days=1))
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 200
    freezer.tick(timedelta(days=2, seconds=1))
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403
    status, body = await redeem(code["key"])
    assert status == 200
    headers = {"X-Guest-Token": body["token"]}

    # Replacing the QR code ends the old printout and every session started with it.
    await _add_code(hass, entry, "Guest bedroom", [BEDROOM], 3, ref=ref, new_qr=True)
    (new_ref, new), = entry.options["codes"].items()
    assert new_ref != ref and new["key"] != code["key"]
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403
    assert (await redeem(code["key"]))[0] == 403
    status, body = await redeem(new["key"])
    headers = {"X-Guest-Token": body["token"]}

    # Editing without replacing keeps the printout and its sessions.
    await _add_code(hass, entry, "Guest room", [BEDROOM, LIVING], 3, ref=new_ref)
    data = await (await http.get("/api/ha_guest/states", headers=headers)).json()
    assert data["code"] == "Guest room" and len([e for s in data["sections"] for e in s["entities"]]) == 2

    # Removing the code ends its sessions too.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "codes"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remove_code"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"code": [new_ref]})
    await hass.async_block_till_done()
    assert entry.options["codes"] == {}
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403


async def test_code_sessions_cannot_be_forged(hass, hass_client_no_auth):
    from custom_components.ha_guest.invite import _sign

    entry = await _setup(hass, panel={"entities": [LIVING, BEDROOM], "link_hours": 2})
    await _add_code(hass, entry, "Guest bedroom", [BEDROOM], 3)
    ref = next(iter(entry.options["codes"]))
    secret = entry.data["secret"]
    http = await hass_client_no_auth()
    now = int(dt_util.utcnow().timestamp())

    async def status(token):
        return (await http.get("/api/ha_guest/states", headers={"X-Guest-Token": token})).status

    assert await status(f"{now + 3600}.{ref}.{_sign(secret, now + 3600, ref)}") == 200
    far = now + 30 * 86400
    assert await status(f"{far}.{ref}.{_sign(secret, far, ref)}") == 403  # longer than the code's days
    assert await status(f"{now + 3600}.other.{_sign(secret, now + 3600, ref)}") == 403  # another code's name
    assert await status(f"{now + 3600}.{ref}.{_sign(secret, now + 3600)}") == 403  # a plain link's signature
    assert await status(f"{now + 3600}.{_sign(secret, now + 3600, ref)}") == 403  # a session cut down to a link



async def test_named_invitations(hass, hass_client_no_auth, hass_ws_client, freezer):
    """One link per guest, with its own entities and expiry, that can be extended or revoked on its own."""
    events = async_capture_events(hass, "ha_guest_event")
    entry = await _setup(hass, panel={"entities": [LIVING, BEDROOM], "link_hours": 2})
    member = await hass.auth.async_create_user("Hija", group_ids=[GROUP_ID_USER])
    refresh = await hass.auth.async_create_refresh_token(member, "https://example.com")
    http = await hass_client_no_auth()

    async def call(**msg):
        # A fresh connection each time: access tokens expire when the test moves the clock forward.
        ws = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))
        await ws.send_json({"id": 1, **msg})
        return await ws.receive_json()

    # Not more than the panel allows.
    result = await call(type="ha_guest/invitations/create", name="Pedro", entities=[ALWAYS], hours=24)
    assert result["error"]["code"] == "not_allowed"

    result = await call(type="ha_guest/invitations/create", name="Pedro", entities=[BEDROOM], hours=24)
    assert result["success"]
    pedro, = result["result"]["invitations"]
    assert pedro["name"] == "Pedro" and pedro["created_by"] == "Hija" and pedro["last_used"] is None
    assert [e["entity_id"] for e in result["result"]["entities"]] == [LIVING, BEDROOM]
    headers = {"X-Guest-Token": pedro["url"].split("?t=")[1]}

    data = await (await http.get("/api/ha_guest/states", headers=headers)).json()
    assert data["code"] == "Pedro" and data["expires"] == pedro["expires"]
    assert [e["entity_id"] for s in data["sections"] for e in s["entities"]] == [BEDROOM]
    assert (await http.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"},
                            headers=headers)).status == 403
    assert events[-1].data["code"] == "Pedro"
    listed, = (await call(type="ha_guest/invitations"))["result"]["invitations"]
    assert listed["last_used"] is not None

    # It outlives the 2-hour quick-link duration; extending keeps the same link working.
    freezer.tick(timedelta(hours=23))
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 200
    freezer.tick(timedelta(hours=2))
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403
    result = await call(type="ha_guest/invitations/extend", ref=pedro["ref"], hours=48)
    assert result["success"]
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 200

    # A forged token for the same invitation, or one for another, does not work.
    ref = pedro["ref"]
    for token in (f"i.{ref}." + "0" * 32, "i.deadbeef." + headers["X-Guest-Token"].rsplit(".", 1)[1]):
        assert (await http.get("/api/ha_guest/states", headers={"X-Guest-Token": token})).status == 403

    # It survives a restart of the integration, and revoking it ends it at once.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 200
    result = await call(type="ha_guest/invitations/revoke", ref=ref)
    assert result["result"]["invitations"] == []
    assert (await http.get("/api/ha_guest/states", headers=headers)).status == 403


async def test_restricted_users_cannot_manage_invitations(hass, hass_ws_client):
    guest = await _create_user(hass)
    await _setup(hass, users={guest.id: _user_conf()})
    refresh = await hass.auth.async_create_refresh_token(guest, "https://example.com")
    ws = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))
    for msg_id, msg in enumerate(({"type": "ha_guest/invitations"},
                                  {"type": "ha_guest/invitations/create", "name": "X", "hours": 1}), 1):
        await ws.send_json({"id": msg_id, **msg})
        assert (await ws.receive_json())["error"]["code"] == "unauthorized"



async def test_live_updates(hass, hass_client_no_auth, hass_ws_client):
    """The stream says «change» when one of the guest's entities changes, and nothing for the others."""
    import asyncio

    entry = await _setup(hass, panel={"entities": [LIVING, BEDROOM], "link_hours": 2})
    http = await hass_client_no_auth()
    assert (await http.get("/api/ha_guest/stream")).status == 403  # no link

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "ha_guest/invitations/create", "name": "Pedro", "entities": [BEDROOM], "hours": 1})
    url = (await ws.receive_json())["result"]["invitations"][0]["url"]
    res = await http.get("/api/ha_guest/stream", headers={"X-Guest-Token": url.split("?t=")[1]})
    assert res.status == 200 and res.headers["Content-Type"] == "text/event-stream"
    assert res.headers["Referrer-Policy"] == "no-referrer"

    async def line():
        return (await asyncio.wait_for(res.content.readline(), 5)).decode().strip()

    assert await line() == ": connected"
    assert await line() == ""
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": LIVING}, blocking=True)
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": BEDROOM}, blocking=True)
    assert await line() == "data: change"  # only the invitation's own entity woke the stream
    res.close()


async def test_home_screen_manifest(hass, hass_client_no_auth):
    await _setup(hass)
    http = await hass_client_no_auth()
    manifest = await (await http.get("/api/ha_guest/manifest.json?t=123.abc")).json()
    assert manifest["name"] == "Casa" and manifest["start_url"] == "/guest?t=123.abc"
    assert manifest["scope"] == "/guest" and manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (await http.get(icon["src"])).status == 200
    # Anything that is not token-shaped is not echoed into the start address.
    manifest = await (await http.get("/api/ha_guest/manifest.json?t=a%20b%26x%3D1")).json()
    assert manifest["start_url"] == "/guest"



async def test_page_security_headers(hass, hass_client_no_auth):
    await _setup(hass)
    http = await hass_client_no_auth()
    res = await http.get("/guest")
    html = await res.text()
    policy = res.headers["Content-Security-Policy"]
    nonce = policy.split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in html and f'<style nonce="{nonce}">' in html
    assert "unsafe-inline" not in policy and "frame-ancestors 'none'" in policy and "connect-src 'self'" in policy
    assert res.headers["X-Content-Type-Options"] == "nosniff" and res.headers["Referrer-Policy"] == "no-referrer"
    # A new nonce on every load.
    assert (await http.get("/guest")).headers["Content-Security-Policy"] != policy
    res = await http.get("/api/ha_guest/states")
    assert res.headers["X-Content-Type-Options"] == "nosniff" and res.headers["Cache-Control"] == "no-store"


async def test_action_rate_limit(hass, hass_client_no_auth):
    await _setup(hass)
    http = await hass_client_no_auth()
    body = {"entity_id": LIVING, "action": "toggle"}
    statuses = [(await http.post("/api/ha_guest/action", json=body)).status for _ in range(32)]
    assert statuses[:30] == [200] * 30 and statuses[30:] == [429, 429]


async def test_expired_link_message(hass, hass_client_no_auth):
    await _setup(hass, panel={"link_hours": 1})
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    options = {**entry.options, "appearance": {"expired_message": "Text me on 555-0100"}}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    http = await hass_client_no_auth()
    res = await http.get("/api/ha_guest/states", headers={"X-Guest-Token": "1.bad"})
    assert await res.json() == {"error": "link", "message": "Text me on 555-0100"}


async def test_fan_speed_and_lock_unlatch(hass, hass_client_no_auth):
    fan_calls = async_mock_service(hass, "fan", "set_percentage")
    open_calls = async_mock_service(hass, "lock", "open")
    hass.states.async_set("fan.ceiling", "on", {"percentage": 50, "percentage_step": 25})
    hass.states.async_set("lock.gate", "locked", {"supported_features": 1})
    hass.states.async_set("lock.door", "locked", {"supported_features": 0})
    await _setup(hass, panel={"entities": ["fan.ceiling", "lock.gate", "lock.door"]})
    http = await hass_client_no_auth()

    async def act(entity_id, action, value=None):
        return (await http.post("/api/ha_guest/action", json={"entity_id": entity_id, "action": action, "value": value})).status

    assert await act("fan.ceiling", "percentage", 75) == 200
    assert fan_calls[-1].data == {"entity_id": "fan.ceiling", "percentage": 75}
    assert await act("fan.ceiling", "percentage", 150) == 400
    assert await act("lock.gate", "open") == 200 and open_calls[-1].data == {"entity_id": "lock.gate"}
    assert await act("lock.door", "open") == 400  # this lock cannot unlatch
    assert len(open_calls) == 1



async def test_invitation_actions_for_automations(hass, hass_client_no_auth, freezer):
    """A booking: starts at check-in, ends at check-out, only while a helper is on; extended and revoked by name."""
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass, panel={"entities": [LIVING, BEDROOM], "link_hours": 2})
    hass.states.async_set("input_boolean.guests_allowed", "on")
    http = await hass_client_no_auth()
    now = dt_util.utcnow()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "create_invitation", {"name": "X", "entities": [ALWAYS]},
                                       blocking=True, return_response=True)

    booking = await hass.services.async_call(DOMAIN, "create_invitation", {
        "name": "Booking 1234", "entities": [BEDROOM],
        "starts": (now + timedelta(hours=3)).isoformat(), "ends": (now + timedelta(days=2)).isoformat(),
        "condition_entity": "input_boolean.guests_allowed",
    }, blocking=True, return_response=True)
    assert booking["name"] == "Booking 1234" and "/guest?t=i." in booking["url"]
    assert dt_util.parse_datetime(booking["starts"]) == (now + timedelta(hours=3)).replace(microsecond=0)
    headers = {"X-Guest-Token": booking["url"].split("?t=")[1]}

    async def get():
        res = await http.get("/api/ha_guest/states", headers=headers)
        return res.status, (await res.json()).get("error")

    assert await get() == (403, "not_yet")  # before check-in
    freezer.tick(timedelta(hours=3, seconds=1))
    assert await get() == (200, None)
    hass.states.async_set("input_boolean.guests_allowed", "off")
    assert await get() == (403, "schedule")  # the helper is off
    hass.states.async_set("input_boolean.guests_allowed", "on")
    assert await get() == (200, None)

    freezer.tick(timedelta(days=2))
    assert (await get())[0] == 403  # after check-out
    extended = await hass.services.async_call(DOMAIN, "extend_invitation", {"invitation": "booking 1234", "duration": "06:00:00"},
                                              blocking=True, return_response=True)
    assert extended["invitation"] == booking["invitation"]
    assert await get() == (200, None)
    await hass.services.async_call(DOMAIN, "revoke_invitation", {"invitation": booking["invitation"]}, blocking=True)
    assert (await get())[0] == 403
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "revoke_invitation", {"invitation": "nobody"}, blocking=True)


async def test_single_phone_invitation(hass, hass_client_no_auth, hass_ws_client):
    """The first phone to open the link keeps it; the same link forwarded to another phone does not work."""
    await _setup(hass, panel={"entities": [LIVING], "link_hours": 2})
    result = await hass.services.async_call(DOMAIN, "create_invitation", {"name": "Ana", "single_device": True},
                                            blocking=True, return_response=True)
    link = {"X-Guest-Token": result["url"].split("?t=")[1]}
    http = await hass_client_no_auth()

    res = await http.get("/api/ha_guest/states", headers=link)
    assert res.status == 403 and (await res.json())["error"] == "claim"
    res = await http.post("/api/ha_guest/claim", json={}, headers=link)
    phone = {"X-Guest-Token": (await res.json())["token"]}
    assert phone["X-Guest-Token"].startswith("d.")
    assert (await http.get("/api/ha_guest/states", headers=phone)).status == 200
    assert (await http.post("/api/ha_guest/action", json={"entity_id": LIVING, "action": "turn_on"}, headers=phone)).status == 200

    # A second phone with the forwarded link.
    res = await http.post("/api/ha_guest/claim", json={}, headers=link)
    assert res.status == 403 and (await res.json())["error"] == "claimed"
    res = await http.get("/api/ha_guest/states", headers=link)
    assert (await res.json())["error"] == "claimed"
    forged = {"X-Guest-Token": phone["X-Guest-Token"][:-4] + "AAAA"}
    assert (await http.get("/api/ha_guest/states", headers=forged)).status == 403

    # The household can free it for a new phone; the old phone stops working.
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "ha_guest/invitations/release", "ref": result["invitation"]})
    listed, = (await ws.receive_json())["result"]["invitations"]
    assert listed["device"] is False and listed["single_device"] is True
    assert (await http.get("/api/ha_guest/states", headers=phone)).status == 403
    assert (await http.post("/api/ha_guest/claim", json={}, headers=link)).status == 200
