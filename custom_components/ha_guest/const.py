"""Constants for HA Guest."""

from __future__ import annotations

DOMAIN = "ha_guest"

# Options layout: {CONF_PANEL: {...}, CONF_USERS: {user_id: {...}}}
CONF_PANEL = "panel"
CONF_ENABLED = "enabled"
CONF_TITLE = "title"
CONF_NETWORKS = "networks"
CONF_ENTITIES = "entities"
CONF_LINK_HOURS = "link_hours"
DEFAULT_LINK_HOURS = 12
CONF_SOURCE = "source"
CONF_DASHBOARD = "dashboard"
SOURCE_ENTITIES = "entities"
SOURCE_DASHBOARD = "dashboard"
CONF_SECRET = "secret"

CONF_APPEARANCE = "appearance"
CONF_THEME = "theme"
CONF_MODE = "mode"
CONF_ACCENT = "accent"
CONF_WELCOME = "welcome"
CONF_SHOW_ICONS = "show_icons"
CONF_LAYOUT = "layout"
CONF_BACKGROUND = "background"
CONF_EXPIRED_MESSAGE = "expired_message"
DEFAULT_THEME = "default"

CONF_SAFETY = "safety"
CONF_REQUIRE_HOME = "require_home"
CONF_PANEL_CONDITION_ENTITY = "condition_entity"
CONF_PANEL_CONDITION_STATES = "condition_states"
CONF_NOTIFY = "notify"
CONF_NOTIFY_ON = "notify_on"
NOTIFY_EVENTS = ["open", "action", "blocked"]
DEFAULT_NOTIFY_ON = ["open", "blocked"]
EVENT_GUEST = "ha_guest_event"

# Permanent QR codes: {ref: {name, key, entities, days}}. «key» is the secret printed in the QR code; «ref» is a
# public id carried by the sessions it starts, so deleting or replacing a code ends them.
CONF_CODES = "codes"
CONF_CODE = "code"
CONF_NAME = "name"
CONF_KEY = "key"
CONF_DAYS = "days"
CONF_NEW_QR = "new_qr"
DEFAULT_CODE_DAYS = 3

CONF_USERS = "users"
CONF_USER = "user"
CONF_ALWAYS_ENTITIES = "always_entities"
CONF_FAMILY_LOCATION = "family_location"
CONF_HOME_ENTITIES = "home_entities"
CONF_CONDITION_ENTITY = "condition_entity"
CONF_CONDITION_STATES = "condition_states"
CONF_AWAY_READ = "away_read"

DEFAULT_TITLE = "Guests"

PANEL_URL = "/guest"
API_STATES_URL = "/api/ha_guest/states"
API_ACTION_URL = "/api/ha_guest/action"
API_REDEEM_URL = "/api/ha_guest/redeem"
API_STREAM_URL = "/api/ha_guest/stream"
API_MANIFEST_URL = "/api/ha_guest/manifest.json"
ICON_URL = "/ha_guest_static/icon-{size}.png"
TOKEN_HEADER = "X-Guest-Token"
INVITE_PANEL_PATH = "guest-qr"
STATIC_URL = "/ha_guest_static/invite-panel.js"

GROUP_PREFIX = "ha_guest_"
FAMILY_LOCATION_DOMAIN = "family_location"
