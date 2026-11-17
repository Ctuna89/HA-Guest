// «Guest QR» sidebar page: shows a fresh guest link as a QR code each time it is opened.
const TEXT = {
  en: {
    title: "Guest QR", scan: "Guests scan this code while connected to the home Wi‑Fi.",
    qr: "QR code with the guest link", link: "Guest link",
    until: "Valid until", forever: "This link does not expire.", share: "Share link", copy: "Copy link",
    copied: "Copied", select: "Link selected: copy it from the menu", again: "Create another code",
    again_note: "Codes you created before keep working until they expire.",
    share_text: "Guest access to the house. Open it while connected to the home Wi‑Fi.",
    disabled: "The guest panel is switched off.",
    unauthorized: "Your user cannot create guest codes. Ask someone from the household to create one.",
    error: "Could not create the code.",
  },
  es: {
    title: "QR de invitados", scan: "El invitado escanea este código conectado al WiFi de la casa.",
    qr: "Código QR con el enlace de invitados", link: "Enlace de invitados",
    until: "Válido hasta", forever: "Este enlace no vence.", share: "Compartir enlace", copy: "Copiar enlace",
    copied: "Copiado", select: "Enlace seleccionado: cópialo desde el menú", again: "Crear otro código",
    again_note: "Los códigos que creaste antes siguen sirviendo hasta que venzan.",
    share_text: "Acceso de invitado a la casa. Ábrelo conectado al WiFi de la casa.",
    disabled: "El panel de invitados está desactivado.",
    unauthorized: "Tu usuario no puede crear códigos de invitado. Pídele uno a alguien de la casa.",
    error: "No se pudo crear el código.",
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
        p { color: var(--secondary-text-color); margin: 16px 0 0; text-wrap: balance; }
        .when { color: var(--primary-text-color); font-weight: 600; }
        input { width: 100%; margin-top: 16px; padding: 12px; border-radius: 12px; font: inherit; font-size: 14px;
          text-align: center; color: var(--primary-text-color); background: var(--card-background-color);
          border: 1px solid var(--outline-color, var(--divider-color)); box-sizing: border-box; }
        .actions { display: flex; gap: 12px; margin-top: 16px; }
        button { flex: 1; min-height: 44px; border-radius: 12px; font: inherit; font-weight: 600; cursor: pointer;
          border: 1px solid var(--primary-color); background: var(--primary-color); color: var(--text-primary-color); }
        button.plain { background: transparent; color: var(--primary-color); }
        button.link { border: 0; background: none; color: var(--primary-color); margin-top: 28px; }
        button:focus-visible, input:focus-visible { outline: 3px solid var(--primary-color); outline-offset: 2px; }
        .note { font-size: 14px; margin-top: 4px; }
        [hidden] { display: none !important; }
      </style>
      <header><ha-menu-button></ha-menu-button><span class="title"></span></header>
      <main>
        <div class="ready" hidden>
          <div class="qr" role="img"></div>
          <p class="scan"></p>
          <p class="when"></p>
          <input class="url" readonly>
          <div class="actions">
            <button class="share" hidden></button>
            <button class="copy"></button>
          </div>
          <p class="status" role="status" aria-live="polite"></p>
          <button class="again link"></button>
          <p class="note"></p>
        </div>
        <p class="failed when" hidden></p>
      </main>`;
    const $ = (selector) => this.shadowRoot.querySelector(selector);
    $(".title").textContent = t.title;
    $(".scan").textContent = t.scan;
    $(".qr").setAttribute("aria-label", t.qr);
    $(".url").setAttribute("aria-label", t.link);
    $(".share").textContent = t.share;
    $(".copy").textContent = t.copy;
    $(".again").textContent = t.again;
    $(".note").textContent = t.again_note;
    $("ha-menu-button").hass = this._hass;
    $("ha-menu-button").narrow = this._narrow;

    // Sharing is the main action where the browser offers it; otherwise copying is.
    if (navigator.share) {
      $(".share").hidden = false;
      $(".copy").classList.add("plain");
    }

    $(".url").addEventListener("focus", (ev) => ev.target.select());
    $(".again").addEventListener("click", () => this._load());
    $(".share").addEventListener("click", async () => {
      try {
        await navigator.share({ title: t.title, text: t.share_text, url: this._url });
      } catch (err) { /* the person closed the share sheet */ }
    });
    $(".copy").addEventListener("click", async () => {
      $(".status").textContent = (await this._copy()) ? t.copied : t.select;
    });
  }

  // Home Assistant is usually opened over plain http at home, where the clipboard API does not exist.
  async _copy() {
    try {
      await navigator.clipboard.writeText(this._url);
      return true;
    } catch (err) { /* fall through to the older way */ }
    const field = this.shadowRoot.querySelector(".url");
    field.focus();
    field.select();
    field.setSelectionRange(0, field.value.length);
    try {
      return document.execCommand("copy");
    } catch (err) {
      return false;
    }
  }

  async _load() {
    const t = this._t;
    const $ = (selector) => this.shadowRoot.querySelector(selector);
    $(".status").textContent = "";
    try {
      const invite = await this._hass.callWS({ type: "ha_guest/invite" });
      this._url = invite.url;
      // The SVG is produced by this integration's own backend from the link above.
      $(".qr").innerHTML = invite.svg;
      $(".url").value = invite.url;
      $(".ready .when").textContent = invite.expires
        ? `${t.until} ${new Date(invite.expires * 1000).toLocaleString(this._hass.language, { dateStyle: "medium", timeStyle: "short" })}`
        : t.forever;
      $(".again").hidden = $(".note").hidden = !invite.expires;
      $(".ready").hidden = false;
      $(".failed").hidden = true;
    } catch (err) {
      $(".ready").hidden = true;
      $(".failed").hidden = false;
      $(".failed").textContent = t[err.code] || t.error;
    }
  }
}

customElements.define("ha-guest-invite-panel", HaGuestInvitePanel);
