"""Texts for the logbook and for notifications, in Spanish or English following Home Assistant's language."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

ACTIONS = {
    "en": {
        "turn_on": "turned on {name}", "turn_off": "turned off {name}", "open": "opened {name}",
        "close": "closed {name}", "stop": "stopped {name}", "lock": "locked {name}", "unlock": "unlocked {name}",
        "activate": "activated {name}", "brightness": "set {name} to {value}%", "position": "set {name} to {value}%",
        "temperature": "set {name} to {value}°", "hvac_mode": "set {name} to {value}",
        "volume": "set the volume of {name} to {value}%", "volume_up": "turned up {name}",
        "volume_down": "turned down {name}", "play_pause": "played or paused {name}", "start": "started {name}",
        "dock": "sent {name} to its dock",
    },
    "es": {
        "turn_on": "encendió {name}", "turn_off": "apagó {name}", "open": "abrió {name}", "close": "cerró {name}",
        "stop": "detuvo {name}", "lock": "bloqueó {name}", "unlock": "desbloqueó {name}", "activate": "activó {name}",
        "brightness": "puso {name} en {value}%", "position": "puso {name} en {value}%",
        "temperature": "puso {name} en {value}°", "hvac_mode": "puso {name} en {value}",
        "volume": "puso el volumen de {name} en {value}%", "volume_up": "subió el volumen de {name}",
        "volume_down": "bajó el volumen de {name}", "play_pause": "reprodujo o pausó {name}",
        "start": "inició {name}", "dock": "mandó {name} a la base",
    },
}

TEXT = {
    "en": {
        "guest": "Guest", "title": "Guest panel", "used": "used {name}", "open": "opened the guest panel",
        "blocked_link": "tried to get in with an expired or invalid guest link",
        "blocked_paused": "tried to use the guest panel while it was paused",
    },
    "es": {
        "guest": "Invitado", "title": "Panel de invitados", "used": "usó {name}", "open": "abrió el panel de invitados",
        "blocked_link": "intentó entrar con un enlace de invitado vencido o no válido",
        "blocked_paused": "intentó usar el panel de invitados mientras estaba pausado",
    },
}


def language(hass: HomeAssistant) -> str:
    return "es" if (hass.config.language or "en").lower().startswith("es") else "en"


def describe(lang: str, data: dict[str, Any]) -> tuple[str, str]:
    """(who, what) for an «ha_guest_event», e.g. ("Guest (192.168.1.20)", "turned off Living room lights")."""
    text = TEXT[lang]
    who = f"{text['guest']} ({data.get('ip') or '?'})"
    kind = data.get("type")
    if kind == "action":
        template = ACTIONS[lang].get(data.get("action"), text["used"])
        what = template.format(name=data.get("name") or data.get("entity_id"), value=data.get("value"))
    elif kind == "blocked":
        what = text.get(f"blocked_{data.get('reason')}", text["blocked_link"])
    else:
        what = text["open"]
    return who, what


def notification(lang: str, data: dict[str, Any]) -> tuple[str, str]:
    who, what = describe(lang, data)
    return TEXT[lang]["title"], f"{who} {what}."
