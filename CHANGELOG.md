# Changelog

## 0.8.0 — 2026-11-22

- **Permanent QR codes** (*Configure → Permanent QR codes*): print one code for a room, such as the guest bedroom
  nightstand. Each phone that scans it gets a session for the days you set, limited to the code's own entities and
  logged under its name, and then scans the same code again. Print it from the **Guest QR** page. **Replace this
  QR code** or removing it ends the old printout and every session started with it. The printed code itself never
  expires: anyone who can see it and is on your network can use it, so keep it to that room's entities.
- QR codes now scale to any size (thumbnails and printed sheets).
- The README shows the repository ready to copy (`Ctuna89/HA-Guest`) and separate steps for the Marketplace and
  for HACS.

## 0.7.0 — 2026-11-20

The guest page now looks much closer to your dashboard.

- **Background photo:** taken from the chosen dashboard's view background, the theme's `lovelace-background`, or a
  new *Background image* setting under *Guest panel appearance* (e.g. `/local/background.jpg`). Cards turn into
  frosted glass over a darkened photo, and «auto» mode uses dark so text stays readable.
- **States in Home Assistant's own words and language:** «Inactivo» instead of `idle`, «Despejado, de noche» instead
  of `clear-night`.
- **Weather** shows its condition and temperature with a weather icon; **media players** show what is playing.
- **Scenes, scripts and buttons** next to each other become a row of round buttons.
- **Lights with colour temperature** get a tone slider (warm · cool).
- The README explains what guests see compared with your dashboard, and what cannot be reproduced (custom cards'
  own design, greetings, templates, pop-ups).

## 0.6.1 — 2026-11-19

Security fixes for the guest panel.

- The panel now only answers when opened through an IP address, a `.local` name, or Home Assistant's internal or
  external URL. Before, a website opened on any device of the home network could point its own domain name at Home
  Assistant (DNS rebinding) and use the panel from that device's browser; with a link duration of 0 it needed no
  link at all. The QR code already uses the internal URL, so guests notice nothing.
- New setups require expiring links (12 hours) instead of none. Existing setups keep their value: with 0, anyone on
  the allowed networks gets in, even with an old QR code. The form now says so; change it under
  **Configure → Guest panel → Guest link duration**.
- New «Security» section in the README: what the panel checks, and what a still-valid forwarded link or a copied
  Wi-Fi name can still do.

## 0.6.0 — 2026-11-19

First published release.

### Guest panel (no account, no app)

- A page at `/guest` where visitors control only the entities you choose, and only from the networks you allow.
  Remote access through Home Assistant Cloud is always refused.
- What guests can control is either a list of entities or a Home Assistant dashboard: the entities its cards show
  become the only ones guests may use, with the dashboard's titles, order, names and icons.
- Optional expiring links: guests scan a QR code from the new **Guest QR** sidebar page, which any household user
  can open. Links stop working after the hours you set, and all of them can be revoked at once.
- Looks: any installed Home Assistant theme, light or dark mode, accent colour, welcome message, icons, and a YAML
  layout with sections, names, per-state icons, and your own texts for states and buttons.
- Safety: the panel can work only while someone from the household is home or an entity is in given states, and
  the **Guest panel** switch pauses it. Guests see the same message whatever the reason.
- Activity: visits, actions and rejected attempts go to the logbook (attributed to the guest), fire an
  `ha_guest_event`, and can notify your phone. An **In use** sensor shows when guests are on the panel.

### Restricted users (with the Home Assistant app)

- Pick a non-administrator user and the entities they may always use, Family Location included.
- «Home only» entities that they can use only while their phone is on your Wi-Fi (or another entity reports them
  home). The setup shows what that entity reports right now, so a mistyped Wi-Fi name does not go unnoticed.
