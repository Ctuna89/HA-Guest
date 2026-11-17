# HA Guest

🇪🇸 [Leer en español](README.es.md)

A Home Assistant integration that lets your guests control **only the entities you choose**, and **only while
they are connected to your network**.

It covers two kinds of guest:

| | Who | How they get in | What they can do |
|---|---|---|---|
| **Guest panel** | A friend visiting your home | Opens `http://<your-home-assistant>:8123/guest` in the browser. No app, no user | Controls the panel's entities, only from your network |
| **Restricted user** | Someone with the Home Assistant app (e.g. a relative who shares their location) | With their own user, in the app or the browser | Always: whatever you allow (e.g. Family Location). Lights and the rest: only while their phone is on your Wi-Fi |

<img src="docs/guest-panel.png" alt="Guest panel" width="320"> <img src="docs/guest-qr.png" alt="Guest QR page" width="320">

## Installation

1. HACS → ⋮ menu → *Custom repositories* → add this repository as an **Integration**.
2. Install **HA Guest** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → HA Guest.*

Requires Home Assistant 2025.3 or later.

## Guest panel (no account)

The panel is configured when you add the integration:

- **Allowed networks:** your Wi-Fi network in CIDR format, for example `192.168.1.0/24`. Your Home Assistant's
  own network is suggested. If you have a separate guest network, use that one (and make sure the router lets it
  reach Home Assistant).
- **Entities:** the ones a guest can control. Lights (with brightness), switches, fans, covers, locks, scenes,
  scripts, buttons, climate, media players and vacuums get controls; any other entity is shown read-only.

Guests open `http://<home-assistant-ip>:8123/guest`. The easiest way is to leave a QR code with that address.
From any other network the page only shows «Connect to the home Wi-Fi» along with the IP address Home Assistant
sees, which helps when adjusting the allowed networks.

### Expiring links (QR codes)

Being on the Wi-Fi is not always enough: someone you invited once could come back later, stand within reach of
the network and switch your lights off. To prevent that, set **Guest link duration** to a number of hours.

- The plain `/guest` address then stops working. Guests need a link that carries a code, and each link works
  only for that many hours from the moment it was created.
- Links are created on the **Guest QR** page in the Home Assistant sidebar, which shows a fresh QR code every
  time it is opened. The guest scans it with their phone's camera while on your Wi-Fi.
- **Any Home Assistant user of your household can create codes**, administrator or not, from the app or the
  browser, even when away from home. Restricted users (see below) cannot.
- When a link expires the guest sees «This link is no longer valid» and has to scan a new code.
- **Configure → Revoke all guest links** cuts off every link handed out so far, immediately. Shortening the
  duration also cuts links that were created with a longer one.

With the duration at 0 (the default) links never expire and the Guest QR page simply shows the plain address.
The QR code uses Home Assistant's local address (*Settings → System → Network → Local network*).

Home Assistant cannot see the name of a visitor's Wi-Fi, only their IP address: that is why the panel is
restricted by network. Remote access through Home Assistant Cloud (Nabu Casa) is always rejected. If you use a
reverse proxy, configure `trusted_proxies` under `http:` so Home Assistant sees the visitor's real IP.

## Restricted users (with the app)

1. Create the person in *Settings → People* with a user that is **not an administrator**, and install the app
   for them.
2. On the integration page: **Configure → Restrict a user**.
3. Set their permissions:
   - **Entities always allowed:** they can see and control these from anywhere.
   - **Include Family Location:** always allows the map (people and zones) and the
     [Family Location](https://github.com/Ctuna89/HA-Family-Location) entities, including the check-in and
     ask-for-help buttons.
   - **Entities allowed only at home:** e.g. the living room lights.
   - **Entity that shows they are home** and its **states**: usually their phone's Wi-Fi connection sensor
     (`sensor.<phone>_wifi_connection` on Android, `sensor.<phone>_ssid` on iPhone) and the name of your Wi-Fi.
     You can also use a `device_tracker` from your router with the state `home`, which is harder to fool than a
     network name.
   - **When away, show them read-only:** if off, these entities do not appear to them while away.

Anything not on those lists they can no longer see or control. Home Assistant enforces the restriction on the
server, so it applies equally to the app, the browser and the API.

### Good to know

- On arriving home, the «home only» entities can take a while to show up in an app that was already open: they
  appear when their state changes or when the app is reopened.
- Their default dashboard is the same as yours, with gaps where they have no permission. It is worth creating a
  dashboard of their own with what they can use and making it their default.
- The restriction covers seeing state and controlling entities. Home Assistant does not filter everything else
  per user: a restricted user can still run actions that do not target an entity (such as Family Location's
  check-in and help actions, which is the intent), and parts like history do not always honour permissions.
- Custom permission groups have no public API in Home Assistant; the integration uses its internal user storage,
  so a future Home Assistant release may require an update.
- If you disable the integration, restricted users keep their «away» access. When you stop restricting a user,
  or delete the integration, they get normal access back.

## Development

```bash
pip install pytest-homeassistant-custom-component
```

```bash
pytest tests
```
