# Changelog

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
