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
CONF_SECRET = "secret"

CONF_APPEARANCE = "appearance"
CONF_THEME = "theme"
CONF_MODE = "mode"
CONF_ACCENT = "accent"
CONF_WELCOME = "welcome"
CONF_SHOW_ICONS = "show_icons"
CONF_LAYOUT = "layout"
DEFAULT_THEME = "default"

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
TOKEN_HEADER = "X-Guest-Token"
INVITE_PANEL_PATH = "guest-qr"
STATIC_URL = "/ha_guest_static/invite-panel.js"

GROUP_PREFIX = "ha_guest_"
FAMILY_LOCATION_DOMAIN = "family_location"
