// «Guest QR» sidebar page: shows a fresh guest link as a QR code each time it is opened.
const TEXT = {
  en: {
    title: "Guest QR", scan: "Guests scan this code while connected to the home Wi-Fi.",
    until: "Valid until", forever: "This link does not expire.", again: "New code", copy: "Copy link",
    copied: "Copied", disabled: "The guest panel is switched off.",
    unauthorized: "Your user cannot invite guests.", error: "Could not create the code.",
  },
  es: {
    title: "QR de invitados", scan: "El invitado escanea este código conectado al WiFi de la casa.",
    until: "Válido hasta", forever: "Este enlace no vence.", again: "Nuevo código", copy: "Copiar enlace",
    copied: "Copiado", disabled: "El panel de invitados está desactivado.",
    unauthorized: "Tu usuario no puede invitar.", error: "No se pudo crear el código.",
  },
};

class HaGuestInvitePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._started = false;
  }

  set hass(hass) {
    this._hass = hass;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.hass = hass;
    if (!this._started) {
      this._started = true;
      this._build();
      this._load();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.narrow = narrow;
  }

  get _t() {
    return (this._hass.language || "en").startsWith("es") ? TEXT.es : TEXT.en;
  }

  _build() {
    const t = this._t;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100vh; background: var(--primary-background-color);
          color: var(--primary-text-color); font-family: var(--ha-font-family-body, Roboto, sans-serif); }
        header { display: flex; align-items: center; gap: 8px; height: 56px; padding: 0 8px;
          background: var(--app-header-background-color); color: var(--app-header-text-color); font-size: 20px; }
        main { max-width: 420px; margin: 0 auto; padding: 24px 16px; text-align: center; }
        .qr { background: #fff; border-radius: 16px; padding: 8px; }
        .qr svg { display: block; width: 100%; height: auto; }
        p { color: var(--secondary-text-color); margin: 16px 0 0; }
        .when { color: var(--primary-text-color); font-weight: 600; }
        .actions { display: flex; gap: 12px; margin-top: 24px; }
        button { flex: 1; min-height: 44px; border: 0; border-radius: 12px; font: inherit; font-weight: 600;
          cursor: pointer; background: var(--primary-color); color: var(--text-primary-color); }
        button.plain { background: var(--secondary-background-color); color: var(--primary-text-color); }
        [hidden] { display: none; }
      </style>
      <header><ha-menu-button></ha-menu-button><span></span></header>
      <main>
        <div class="qr" hidden></div>
        <p class="scan" hidden></p>
        <p class="when"></p>
        <div class="actions" hidden>
          <button class="again"></button>
          <button class="copy plain"></button>
        </div>
      </main>`;
    const root = this.shadowRoot;
    root.querySelector("header span").textContent = t.title;
    root.querySelector(".scan").textContent = t.scan;
    root.querySelector(".again").textContent = t.again;
    root.querySelector(".copy").textContent = t.copy;
    const menu = root.querySelector("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    root.querySelector(".again").addEventListener("click", () => this._load());
    root.querySelector(".copy").addEventListener("click", async (ev) => {
      try {
        await navigator.clipboard.writeText(this._url);
        ev.target.textContent = t.copied;
        setTimeout(() => { ev.target.textContent = t.copy; }, 1500);
      } catch (err) { /* clipboard is unavailable on plain http */ }
    });
  }

  async _load() {
    const t = this._t;
    const root = this.shadowRoot;
    const show = (ok) => ["qr", "scan", "actions"].forEach((c) => { root.querySelector(`.${c}`).hidden = !ok; });
    try {
      const invite = await this._hass.callWS({ type: "ha_guest/invite" });
      this._url = invite.url;
      // The SVG is produced by this integration's own backend from the link above.
      root.querySelector(".qr").innerHTML = invite.svg;
      root.querySelector(".when").textContent = invite.expires
        ? `${t.until} ${new Date(invite.expires * 1000).toLocaleString(this._hass.language, { dateStyle: "medium", timeStyle: "short" })}`
        : t.forever;
      show(true);
    } catch (err) {
      show(false);
      root.querySelector(".when").textContent = t[err.code] || t.error;
    }
  }
}

customElements.define("ha-guest-invite-panel", HaGuestInvitePanel);
