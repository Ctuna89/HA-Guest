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
    codes: "Permanent QR codes", days: "Each phone can use it for {n} days after scanning.", print: "Print",
    nfc: "Write NFC tag", nfc_lock: "Lock the tag afterwards (it can never be changed again)",
    nfc_hold: "Hold the NFC tag against the back of this phone…", nfc_done: "Done: the tag opens this guest panel.",
    nfc_locked: "Done, and the tag is locked.", nfc_failed: "Could not write the tag: {error}",
    nfc_unsupported: "This browser cannot write NFC tags. It works in Chrome on Android with Home Assistant opened over https (for example your Home Assistant Cloud address). Otherwise copy the link and write it as a URL record with an app such as NFC Tools.",
    copy_link: "Copy link",
    print_text: "Scan with your phone's camera while connected to the Wi‑Fi.",
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
    codes: "QR fijos", days: "Cada teléfono puede usarlo {n} días después de escanearlo.", print: "Imprimir",
    nfc: "Escribir tag NFC", nfc_lock: "Bloquear el tag después (nunca más se podrá cambiar)",
    nfc_hold: "Acerca el tag NFC a la parte de atrás de este teléfono…", nfc_done: "Listo: el tag abre este panel de invitados.",
    nfc_locked: "Listo, y el tag quedó bloqueado.", nfc_failed: "No se pudo escribir el tag: {error}",
    nfc_unsupported: "Este navegador no puede escribir tags NFC. Funciona en Chrome para Android con Home Assistant abierto por https (por ejemplo tu dirección de Home Assistant Cloud). Si no, copia el enlace y escríbelo como registro de tipo URL con una app como NFC Tools.",
    copy_link: "Copiar enlace",
    print_text: "Escanéalo con la cámara del teléfono, conectado al WiFi.",
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
        .codes { margin-top: 40px; text-align: left; }
        .codes h2 { font-size: 16px; margin: 0 0 12px; }
        .code { display: flex; gap: 16px; align-items: center; padding: 12px; margin-bottom: 12px; border-radius: 16px;
          background: var(--card-background-color); border: 1px solid var(--divider-color); }
        .code .mini { flex: none; width: 88px; background: #fff; border-radius: 8px; padding: 4px; }
        .code .mini svg { display: block; width: 100%; height: auto; }
        .code .info { flex: 1; min-width: 0; }
        .code strong { display: block; color: var(--primary-text-color); }
        .code p { margin: 4px 0 8px; font-size: 13px; }
        .code button { flex: none; min-height: 40px; padding: 0 16px; }
        .code { flex-wrap: wrap; }
        .code .buttons { display: flex; flex-wrap: wrap; gap: 8px; }
        .code label { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; margin-top: 10px;
          color: var(--secondary-text-color); }
        .code label input { flex: none; width: 18px; height: 18px; margin: 0; padding: 0; }
        .code .mini { align-self: flex-start; }
        .code .nfc-status { margin: 8px 0 0; font-size: 13px; color: var(--primary-text-color); }
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
        <section class="codes" hidden><h2></h2><div class="list"></div></section>
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
      this._showCodes(invite.codes || []);
    } catch (err) {
      $(".ready").hidden = true;
      $(".failed").hidden = false;
      $(".failed").textContent = t[err.code] || t.error;
    }
  }
}

HaGuestInvitePanel.prototype._showCodes = function (codes) {
  const t = this._t;
  const $ = (selector) => this.shadowRoot.querySelector(selector);
  $(".codes").hidden = !codes.length;
  $(".codes h2").textContent = t.codes;
  const list = $(".codes .list");
  list.replaceChildren();
  for (const code of codes) {
    const row = document.createElement("div");
    row.className = "code";
    const mini = document.createElement("div");
    mini.className = "mini";
    mini.innerHTML = code.svg; // produced by this integration's backend
    const info = document.createElement("div");
    info.className = "info";
    const name = document.createElement("strong");
    name.textContent = code.name;
    const days = document.createElement("p");
    days.textContent = t.days.replace("{n}", code.days);
    const make = (text, onClick) => {
      const b = document.createElement("button");
      b.className = "plain";
      b.textContent = text;
      b.addEventListener("click", onClick);
      return b;
    };
    const status = document.createElement("p");
    status.className = "nfc-status";
    status.setAttribute("role", "status");
    status.hidden = true;
    const show = (text) => { status.hidden = false; status.textContent = text; };
    const lockLabel = document.createElement("label");
    const lock = document.createElement("input");
    lock.type = "checkbox";
    lockLabel.append(lock, document.createTextNode(t.nfc_lock));
    lockLabel.hidden = !("NDEFReader" in window && window.isSecureContext);
    const buttons = document.createElement("div");
    buttons.className = "buttons";
    buttons.append(
      make(t.print, () => this._print(code)),
      make(t.nfc, () => this._writeNfc(code, lock.checked, show)),
      make(t.copy_link, async () => show((await this._copyText(code.url)) ? t.copied : code.url)),
    );
    info.append(name, days, buttons, lockLabel, status);
    row.append(mini, info);
    list.append(row);
  }
};

// An NFC tag holding the same address as the printed code: phones open it with a tap, no app needed. Writing needs
// Web NFC (Chrome on Android, over https).
HaGuestInvitePanel.prototype._writeNfc = async function (code, lock, show) {
  const t = this._t;
  if (!("NDEFReader" in window) || !window.isSecureContext) {
    show(t.nfc_unsupported);
    return;
  }
  try {
    const ndef = new NDEFReader();
    show(t.nfc_hold);
    await ndef.write({ records: [{ recordType: "url", data: code.url }] });
    if (lock) {
      await ndef.makeReadOnly();
      show(t.nfc_locked);
    } else {
      show(t.nfc_done);
    }
  } catch (err) {
    show(t.nfc_failed.replace("{error}", err && err.message ? err.message : String(err)));
  }
};

// Copies text; over plain http, where the clipboard API does not exist, through a temporary selected field.
HaGuestInvitePanel.prototype._copyText = async function (text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) { /* fall through */ }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  this.shadowRoot.append(field);
  field.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
  field.remove();
  return ok;
};

// A clean sheet with just the code, its name and how to use it, opened in a new tab and sent to the printer.
HaGuestInvitePanel.prototype._print = function (code) {
  const escape = (text) => String(text).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${escape(code.name)}</title>
    <style>body{font-family:system-ui,sans-serif;text-align:center;margin:48px}svg{width:70mm;height:auto}
    h1{font-size:28px;margin:24px 0 8px}p{font-size:16px;color:#444}</style></head>
    <body>${code.svg}<h1>${escape(code.name)}</h1><p>${escape(this._t.print_text)}</p>
    <script>window.onload = () => window.print();<\/script></body></html>`;
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60000);
};

customElements.define("ha-guest-invite-panel", HaGuestInvitePanel);
