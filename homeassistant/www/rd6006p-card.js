/* RD6006P card 0.1.2. No imports, CDN, analytics or external requests.
 * Measurements come from HA state. History uses the current HA connection.
 * Draft setpoints are sent only by Apply/Enter; +/- changes the draft only.
 */
(() => {
  "use strict";
  const TAG = "rd6006p-card";
  const VERSION = "0.1.2";
  // Must always return boolean: classList.toggle(name, undefined) toggles
  // instead of removing the class and caused blinking when states were absent.
  const VALID = s => Boolean(s && typeof s.state === "string" && s.state !== "" &&
    !["unknown", "unavailable", "None"].includes(s.state) && !s.attributes?.restored);
  const NUMBER = s => VALID(s) && Number.isFinite(Number(s.state)) ? Number(s.state) : null;
  const FACTOR = {"μV":1e-6,"µV":1e-6,mV:.001,V:1,kV:1000,MV:1e6,"μA":1e-6,"µA":1e-6,mA:.001,A:1,mW:.001,W:1,kW:1000,MW:1e6,GW:1e9,TW:1e12,"BTU/h":.2930710701722222,mAh:.001,Ah:1,Wh:1,kWh:1000,MWh:1e6,GWh:1e9};
  const ESC = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  const DICT = {
    ru: {voltage_set:"Уставка напряжения", current_set:"Предел тока", voltage:"Выходное напряжение",
      current:"Выходной ток", power:"Выходная мощность", output_mode:"Режим выхода", protection:"Защита",
      output:"Выход", on:"Включён", off:"Выключен", turn_on:"Включить", turn_off:"Выключить", apply:"Применить",
      draft:"Изменено, ещё не отправлено", connection:"На связи", disconnected:"Нет свежих данных", waiting:"Проверяю состояние…",
      normal:"Норма", unknown:"Неизвестно", history:"Напряжение и ток", nohistory:"История пока пуста",
      history_error:"История недоступна. Проверь Recorder и History в HA.", history_loading:"Загружаю историю…",
      web:"Веб-управление", diagnostics:"Диагностика", notfound:"Устройство RD6006P не найдено. Сначала добавь интеграцию.",
      many:"Найдено несколько БП: укажи serial в настройках карточки.", limits:"Лимиты записи HA", invalid:"Введи число в допустимых пределах",
      voltage:"Напряжение", amperage:"Ток", minute:"мин", hour:"ч", pending:"Команда отправлена, ожидаю чтение…",
      limit_notice:"Текущая уставка выше лимита HA", no_write:"Редактирование само не меняет БП",
      battery_group:"Аккумулятор", battery_mode:"Режим зарядки", battery_voltage:"Напряжение батареи",
      charge:"Заряд за сеанс", energy:"Энергия за сеанс", session_note:"Счётчики БП за сеанс; это не полная ёмкость аккумулятора."},
    en: {voltage_set:"Set voltage", current_set:"Current limit", voltage:"Output voltage", current:"Output current",
      power:"Output power", output_mode:"Output mode", protection:"Protection", output:"Output", on:"On", off:"Off",
      turn_on:"Turn on", turn_off:"Turn off", apply:"Apply", draft:"Edited, not sent yet", connection:"Connected",
      disconnected:"No fresh data", waiting:"Checking state…", normal:"Normal", unknown:"Unknown", history:"Voltage and current",
      nohistory:"No history yet", history_error:"History unavailable. Check Recorder and History in HA.",
      history_loading:"Loading history…", web:"Web control", diagnostics:"Diagnostics", notfound:"RD6006P not found. Add the integration first.",
      many:"More than one PSU found. Set serial in the card configuration.", limits:"HA write limits", invalid:"Enter a number within the limits",
      voltage:"Voltage", amperage:"Current", minute:"min", hour:"h", pending:"Command sent, awaiting readback…",
      limit_notice:"Current setpoint exceeds the HA limit", no_write:"Editing alone does not change the PSU",
      battery_group:"Battery", battery_mode:"Charging mode", battery_voltage:"Battery voltage",
      charge:"Session charge", energy:"Session energy", session_note:"PSU session counters; these do not measure the battery's full capacity."}
  };

  class RidenCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({mode:"open"});
      this._config = {};
      this._ids = {};
      this._draft = {};
      this._dirty = new Set();
      this._pending = new Set();
      this._history = [];
      this._historyAt = 0;
      this._historyBusy = false;
      this._historyError = "";
      this._serial = "";
      this._hours = 1;
    }
    setConfig(config) {
      if (!config || (config.hours_to_show !== undefined && !(Number(config.hours_to_show) > 0 && Number(config.hours_to_show) <= 24))) {
        throw new Error("hours_to_show must be between 0 and 24");
      }
      this._config = {...config};
      this._hours = Number(config.hours_to_show || 1);
      this._historyAt = 0;
      this._build();
      this._sync();
    }
    set hass(hass) {
      this._hass = hass;
      if (!this.shadowRoot.querySelector(".shell") || this._builtLanguage !== this._ru) this._build();
      this._discover();
      this._sync();
      if (this.isConnected && Date.now() - this._historyAt > 60000) this._loadHistory();
    }
    connectedCallback() {
      this._timer = window.setInterval(() => this._loadHistory(), 60000);
      if (window.ResizeObserver) {
        this._observer = new ResizeObserver(() => {this._drawToken = ""; this._draw();});
        this._observer.observe(this);
      }
      if (this._hass) this._loadHistory();
    }
    disconnectedCallback() {
      window.clearInterval(this._timer);
      this._observer?.disconnect();
    }
    getCardSize() { return 12; }
    getGridOptions() { return {columns:24, rows:12, min_columns:6, min_rows:10}; }
    static getStubConfig() { return {type:`custom:${TAG}`, title:"RD6006P", hours_to_show:1}; }
    static async getConfigElement() { return document.createElement("rd6006p-card-editor"); }
    get _ru() { return (this._config.language || this._hass?.language || navigator.language).toLowerCase().startsWith("ru"); }
    _t(key) { return DICT[this._ru ? "ru" : "en"][key] || key; }
    _format(value, digits, unit = "") {
      if (value === null || value === undefined || !Number.isFinite(value)) return "—";
      return `${new Intl.NumberFormat(this._ru ? "ru-RU" : "en-US", {minimumFractionDigits:digits, maximumFractionDigits:digits}).format(value)}${unit ? ` ${unit}` : ""}`;
    }
    _state(key) { return this._hass?.states[this._ids[key]]; }
    _ready(key) {
      const connection=this._state("connection");
      return VALID(this._state(key)) && (!connection || (VALID(connection) && connection.state === "on"));
    }
    _outputReady() {
      return this._ready("output") && ["on","off"].includes(this._state("output").state);
    }
    _factor(key, unit) {
      const native=key === "charge" ? "Ah" : key === "energy" ? "Wh" : key === "power" ? "W" : key.startsWith("current") ? "A" : "V";
      const selected=unit || this._state(key)?.attributes.unit_of_measurement || native;
      return FACTOR[selected] ?? NaN;
    }
    _number(key) {
      const value=NUMBER(this._state(key));
      const converted=value === null ? NaN : value*this._factor(key);
      return Number.isFinite(converted) ? converted : null;
    }
    _discover() {
      if (!this._hass) return;
      const states = Object.values(this._hass.states);
      const serials = [...new Set(states.filter(s => ["connection","output"].includes(s.attributes.rd6006p_key) && s.attributes.rd6006p_serial != null).map(s => String(s.attributes.rd6006p_serial)))];
      const serial = this._config.serial !== undefined ? String(this._config.serial) : serials.length === 1 ? serials[0] : serials.length === 0 ? this._serial : "";
      this._discoveryError = !serial ? this._t(serials.length > 1 ? "many" : "notfound") : "";
      const sameDevice = this._serial === serial;
      if (!sameDevice) {
        this._serial = serial;
        this._history = [];
        this._historyAt = 0;
        this._dirty.clear();
      }
      const metadata = states.find(s => s.attributes.rd6006p_key === "connection" && String(s.attributes.rd6006p_serial) === serial);
      // Entity reload/startup may briefly remove all custom attributes. Keep
      // the known IDs for this serial; current state availability still gates
      // every control, so this never revives old measurements or sends a write.
      this._ids = {...(sameDevice ? this._ids : {}), ...metadata?.attributes.entity_map};
      for (const s of states) {
        if (String(s.attributes.rd6006p_serial) === serial && s.attributes.rd6006p_key) {
          this._ids[s.attributes.rd6006p_key] = s.entity_id;
        }
      }
    }
    _build() {
      this._drawToken = "";
      this._builtLanguage = this._ru;
      const t = k => this._t(k);
      const setTile = (key, color, icon, digits, unit) => `
        <section class="tile set ${color}" data-key="${key}">
          <div class="tile-head"><ha-icon icon="${icon}"></ha-icon><div><span class="label">${t(key)}</span><b id="${key}-actual">—</b></div></div>
          <div class="stepper"><button class="step minus" data-key="${key}" aria-label="${t(key)} −">−</button>
            <input id="${key}-input" inputmode="decimal" aria-label="${t(key)}" type="text" autocomplete="off" spellcheck="false"><span class="unit">${unit}</span>
            <button class="step plus" data-key="${key}" aria-label="${t(key)} +">+</button></div>
          <div class="apply-row"><span class="draft-hint" id="${key}-hint"></span><button class="apply" id="${key}-apply" data-key="${key}">${t("apply")}</button></div>
        </section>`;
      const meter = (key, color, icon) => `
        <section class="tile meter ${color}" data-key="${key}"><div class="tile-head"><ha-icon icon="${icon}"></ha-icon><div><span class="label">${t(key)}</span><b id="${key}-value">—</b></div></div>
          <div class="bar"><div id="${key}-bar" class="fill"></div><span id="${key}-bar-label"></span></div></section>`;
      const batteryTile = (key, color, icon) => `<section class="tile small ${color}" data-key="${key}"><div class="tile-head"><ha-icon icon="${icon}"></ha-icon><div><span class="label">${t(key)}</span><b id="${key}-value">—</b></div></div></section>`;
      this.shadowRoot.innerHTML = `
      <style>
      :host{display:block;--r-bg:var(--ha-card-background,var(--card-background-color,#181a1d));--r-tile:color-mix(in srgb,var(--r-bg) 93%,white 7%);--r-text:var(--primary-text-color,#e7e9ec);--r-muted:var(--secondary-text-color,#92989f);--r-border:color-mix(in srgb,var(--r-text) 13%,transparent);font-family:var(--paper-font-body1_-_font-family,Roboto,Arial,sans-serif);color:var(--r-text)}
      *{box-sizing:border-box}button,input,select{font:inherit}button,a,input,select{touch-action:manipulation}button{cursor:pointer}button:disabled{cursor:default;opacity:.42}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #8db4f7;outline-offset:3px}
      .shell{container-type:inline-size;display:block;background:var(--r-bg);border-radius:var(--ha-card-border-radius,16px);padding:18px;border:1px solid var(--r-border)}
      header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 2px 16px}h2{margin:0;font-size:19px;font-weight:500;letter-spacing:.1px}.status{display:flex;gap:7px;align-items:center;color:var(--r-muted);font-size:12px}.dot{width:7px;height:7px;border-radius:50%;background:#717780;box-shadow:0 0 0 4px #71778015}.dot.online{background:#63c56c;box-shadow:0 0 0 4px #63c56c15}
      .layout{display:grid;grid-template-columns:minmax(325px,.92fr) minmax(0,1.85fr);gap:12px}.controls{display:grid;grid-template-columns:1fr 1fr;gap:10px}.tile,.chart{border:1px solid var(--r-border);border-radius:16px;background:var(--r-tile)}.tile{padding:14px;min-width:0}.output{grid-column:1/-1;display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 14px;min-height:75px}.tile-head{display:flex;align-items:center;gap:11px;min-width:0}.tile-head>div{min-width:0}.label{display:block;font-size:13px;font-weight:500;margin-bottom:5px;line-height:1.3}.tile-head b{font-weight:500;font-size:14px;font-variant-numeric:tabular-nums}.tile-head ha-icon{flex-shrink:0;--mdc-icon-size:25px;color:var(--accent)}
      .green{--accent:#5fc66b;--wash:#5fc66b25}.red{--accent:#ef5d62;--wash:#ef5d6225}.purple{--accent:#ae8be3;--wash:#ae8be325}.blue{--accent:#77a9e2;--wash:#77a9e225}.gold{--accent:#ffc627;--wash:#ffc62722}.output ha-icon{padding:9px;width:43px;height:43px;border-radius:50%;background:var(--wash);--mdc-icon-size:25px}.power-actions{display:flex;gap:6px}.power-actions button{border:1px solid var(--r-border);color:var(--r-muted);background:transparent;border-radius:11px;min-width:49px;height:42px;padding:0 13px;font-size:12px}.power-actions button.active{color:#1d1803;background:#ffc627;border-color:#ffc627}.power-actions button.pending{animation:pulse 1s infinite}
      .set{padding-bottom:10px}.stepper{display:flex;align-items:center;gap:2px;background:color-mix(in srgb,var(--r-text) 5%,transparent);border-radius:11px;margin-top:14px;height:45px;overflow:hidden}.step{width:32px;min-width:29px;align-self:stretch;background:transparent;border:0;color:var(--r-text);font-size:22px;font-weight:300}.step:active{background:var(--wash)}input{border:0;outline:none;color:var(--r-text);background:transparent;min-width:0;width:100%;font-weight:500;text-align:right;font-size:16px;font-variant-numeric:tabular-nums;padding:10px 0}.unit{font-size:13px;color:var(--r-muted);padding:0 7px 0 5px}.set.dirty .stepper{box-shadow:inset 0 0 0 1px var(--accent)}.apply-row{display:flex;align-items:center;justify-content:flex-end;gap:6px;min-height:28px;margin-top:6px}.draft-hint{font-size:10px;line-height:1.2;color:var(--r-muted);flex:1}.apply{border:0;border-radius:7px;background:var(--wash);color:var(--accent);padding:6px 8px;font-size:11px;font-weight:500}
      .meter{min-height:132px}.bar{height:37px;border-radius:11px;overflow:hidden;position:relative;background:var(--wash);margin-top:16px}.fill{position:absolute;inset:0 auto 0 0;width:0;background:var(--accent);opacity:.75;transition:width .25s}.bar span{position:relative;display:block;padding:11px;font-size:10px;color:var(--r-muted)}.power{grid-row:span 2}.power .bar{height:51px}.small{min-height:73px;display:flex;align-items:center}.small .tile-head{gap:10px}.small .label{font-size:12px}.small b{font-size:13px}
      .chart{display:flex;flex-direction:column;padding:18px 10px 10px;min-width:0;min-height:490px;position:relative}.chart-head{display:flex;justify-content:space-between;align-items:center;padding:0 12px 10px;gap:8px}.legend{display:flex;align-items:center;gap:16px;font-size:11px;color:var(--r-muted);flex-wrap:wrap}.legend span{display:flex;align-items:center;gap:6px}.legend i{width:21px;height:2px;background:#5fc66b;display:inline-block}.legend i.amp{background:#ef5d62}select{border:1px solid var(--r-border);background:var(--r-bg);color:var(--r-muted);padding:5px 7px;border-radius:8px;font-size:11px}.chart-wrap{position:relative;flex:1;min-height:350px}.chart svg{display:block;width:100%;height:100%;position:absolute;inset:0}.chart-message{position:absolute;left:50px;right:50px;top:46%;text-align:center;color:var(--r-muted);font-size:13px;pointer-events:none}.chart-hint{color:var(--r-muted);font-size:10px;padding:4px 15px 0}.plot-hover{position:absolute;left:50%;bottom:30px;transform:translateX(-50%);pointer-events:none;background:var(--r-bg);border:1px solid var(--r-border);border-radius:8px;font-size:11px;padding:7px 10px;white-space:nowrap;color:var(--r-text);display:none}
      .message{margin-top:10px;padding:9px 12px;border-radius:9px;background:#e9a34018;color:#dda758;font-size:12px;line-height:1.5}.message:empty{display:none}.footer{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:13px;color:var(--r-muted);font-size:10px}.links{display:flex;gap:12px}.links a{color:var(--r-muted);text-decoration:none}.links a:hover{text-decoration:underline}.limits{text-align:right;font-variant-numeric:tabular-nums}.unavailable .fill{width:0!important}.unavailable .tile-head b{color:var(--r-muted)}[hidden]{display:none!important}
      @keyframes pulse{50%{opacity:.5}}@container(max-width:820px){.layout{grid-template-columns:1fr}.chart{min-height:345px}.chart-wrap{min-height:260px}.controls{grid-template-columns:1fr 1fr}.meter{min-height:118px}.power{grid-row:auto}.small{min-height:78px}.power .bar{height:37px}}@container(max-width:400px){.shell{padding:10px}.tile{padding:12px}.layout{gap:10px}.tile-head{gap:8px}.label{font-size:12px}.tile-head b{font-size:13px}.power-actions button{min-width:42px;padding:0 8px}.chart-head{padding:0 4px 8px;align-items:flex-start}.legend{gap:8px}.footer{align-items:flex-start}.limits{max-width:155px}.links{flex-direction:column;gap:6px}}
      </style>
      <style>.power>.tile{height:100%}.battery{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:10px}.battery h3{grid-column:1/-1;margin:6px 2px 0;font-size:14px;font-weight:500;color:var(--r-muted)}.battery-note{grid-column:1/-1;margin:0 2px;font-size:10px;color:var(--r-muted);line-height:1.4}</style>
      <div class="shell"><header><h2>${ESC(this._config.title || "RD6006P")}</h2><div class="status"><span class="dot"></span><span id="connection-label">${t("waiting")}</span></div></header>
      <div class="layout"><div class="controls">
        <section class="tile output gold"><div class="tile-head"><ha-icon icon="mdi:power"></ha-icon><div><span class="label">${t("output")}</span><b id="output-value">—</b></div></div><div class="power-actions"><button id="output-off" title="${t("turn_off")}">OFF</button><button id="output-on" title="${t("turn_on")}">ON</button></div></section>
        ${setTile("voltage_set", "green", "mdi:lightning-bolt", 3, "V")}${setTile("current_set", "red", "mdi:current-dc", 4, "A")}
        ${meter("voltage", "green", "mdi:lightning-bolt")}${meter("current", "red", "mdi:current-dc")}
        <div class="power">${meter("power", "purple", "mdi:flash-outline")}</div>
        <section class="tile small green"><div class="tile-head"><ha-icon icon="mdi:sine-wave"></ha-icon><div><span class="label">${t("output_mode")}</span><b id="output_mode-value">—</b></div></div></section>
        <section class="tile small blue"><div class="tile-head"><ha-icon icon="mdi:shield-check"></ha-icon><div><span class="label">${t("protection")}</span><b id="protection-value">—</b></div></div></section>
        <section class="battery" aria-label="${t("battery_group")}"><h3>${t("battery_group")}</h3>
          ${batteryTile("battery_mode", "gold", "mdi:battery-charging")}${batteryTile("battery_voltage", "green", "mdi:battery")}
          ${batteryTile("charge", "blue", "mdi:counter")}${batteryTile("energy", "purple", "mdi:lightning-bolt")}
          <p class="battery-note">${t("session_note")}</p>
        </section>
      </div><section class="chart"><div class="chart-head"><div class="legend"><span><i></i>${t("voltage")}, V</span><span><i class="amp"></i>${t("amperage")}, A</span></div><select id="period" aria-label="${t("history")}"><option value="0.25">15 ${t("minute")}</option><option value="1">1 ${t("hour")}</option><option value="6">6 ${t("hour")}</option><option value="24">24 ${t("hour")}</option></select></div><div class="chart-wrap"><svg viewBox="0 0 780 420" preserveAspectRatio="none" role="img" aria-label="${t("history")}"></svg><div class="chart-message"></div><div class="plot-hover"></div></div><div class="chart-hint" id="chart-hint"></div></section></div>
      <div class="message" role="status" aria-live="polite"></div><div class="footer"><div class="links"><a id="web-link" target="_blank" rel="noopener noreferrer">${t("web")}</a><a id="diag-link" target="_blank" rel="noopener noreferrer">${t("diagnostics")}</a></div><span class="limits"></span></div></div>`;
      const period = this.shadowRoot.querySelector("#period");
      if (![...period.options].some(o => Number(o.value) === this._hours)) period.add(new Option(`${this._hours} ${t("hour")}`, String(this._hours)));
      period.value = String(this._hours);
      period.addEventListener("change", () => {this._hours = Number(period.value); this._history = []; this._historyStart = undefined; this._historyEnd = undefined; this._historyAt = 0; this._loadHistory();});
      for (const key of ["voltage_set", "current_set"]) {
        const input = this.shadowRoot.querySelector(`#${key}-input`);
        if (this._dirty.has(key)) input.value = this._draft[key] || "";
        input.addEventListener("input", () => {this._dirty.add(key); this._draft[key] = input.value; this._syncDraft(key);});
        input.addEventListener("keydown", e => {
          if (e.key === "Enter") this._apply(key);
          if (e.key === "Escape") {this._dirty.delete(key); this._sync(); input.blur();}
        });
        this.shadowRoot.querySelector(`#${key}-apply`).addEventListener("click", () => this._apply(key));
      }
      this.shadowRoot.querySelectorAll(".step").forEach(b => b.addEventListener("click", () => this._step(b.dataset.key, b.classList.contains("plus") ? 1 : -1)));
      this.shadowRoot.querySelector("#output-on").addEventListener("click", () => this._output(true));
      this.shadowRoot.querySelector("#output-off").addEventListener("click", () => this._output(false));
      const plot = this.shadowRoot.querySelector(".chart-wrap");
      plot.addEventListener("pointermove", e => this._hover(e));
      plot.addEventListener("pointerleave", () => this.shadowRoot.querySelector(".plot-hover").style.display = "none");
    }
    _syncDraft(key) {
      const root = this.shadowRoot;
      const busy = this._pending.has(key);
      const ready = this._ready(key);
      root.querySelector(`[data-key="${key}"]`).classList.toggle("dirty", this._dirty.has(key));
      root.querySelector(`#${key}-apply`).disabled = !ready || !this._dirty.has(key) || busy;
      root.querySelector(`#${key}-input`).disabled = !ready || busy;
      root.querySelectorAll(`.step[data-key="${key}"]`).forEach(b => b.disabled = !ready || busy);
      root.querySelector(`#${key}-hint`).textContent = busy ? this._t("pending") : this._dirty.has(key) ? this._t("draft") : "";
    }
    _sync() {
      if (!this._hass || !this.shadowRoot.querySelector(".shell")) return;
      this._discover();
      const root = this.shadowRoot;
      const output = this._state("output");
      const online = this._outputReady();
      root.querySelector(".dot").classList.toggle("online", online);
      root.querySelector("#connection-label").textContent = this._t(online ? "connection" : "disconnected");
      root.querySelector("#output-value").textContent = online ? this._t(output.state === "on" ? "on" : "off") : "—";
      for (const [key, digits, unit] of [["voltage",3,"V"],["current",4,"A"],["power",3,"W"]]) {
        const value = this._number(key);
        root.querySelector(`#${key}-value`).textContent = this._format(value, digits, unit);
        const max = key === "voltage" ? this._max("voltage_set") : key === "current" ? this._max("current_set") : this._max("voltage_set") * this._max("current_set");
        root.querySelector(`#${key}-bar`).style.width = `${value !== null && max > 0 ? Math.max(0, Math.min(100, value / max * 100)) : 0}%`;
        root.querySelector(`#${key}-bar-label`).textContent = max ? `0 — ${this._format(max, key === "current" ? 4 : 3, unit)}` : "";
        root.querySelector(`[data-key="${key}"]`).classList.toggle("unavailable", value === null);
      }
      for (const [key, digits, unit] of [["voltage_set",3,"V"],["current_set",4,"A"]]) {
        const value = this._number(key);
        const input = root.querySelector(`#${key}-input`);
        root.querySelector(`#${key}-actual`).textContent = this._format(value, digits, unit);
        if (!this._dirty.has(key) && root.activeElement !== input) input.value = value === null ? "" : value.toFixed(digits);
        this._syncDraft(key);
      }
      const mode = this._state("output_mode");
      root.querySelector("#output_mode-value").textContent = VALID(mode) ? (["cv","cc"].includes(mode.state) ? mode.state.toUpperCase() : this._t("unknown")) : "—";
      const protection = this._state("protection");
      root.querySelector("#protection-value").textContent = VALID(protection) ? ["ovp","ocp"].includes(protection.state) ? protection.state.toUpperCase() : this._t(protection.state) : "—";
      const batteryMode = this._state("battery_mode");
      const batteryModeReady = this._ready("battery_mode");
      const modeLabel = {"0":"off", "1":"on", off:"off", on:"on"}[batteryMode?.state] || "unknown";
      root.querySelector("#battery_mode-value").textContent = batteryModeReady ? this._t(modeLabel) : "—";
      root.querySelector('[data-key="battery_mode"]').classList.toggle("unavailable", !batteryModeReady);
      for (const [key, digits, unit] of [["battery_voltage",2,"V"],["charge",3,"Ah"],["energy",3,"Wh"]]) {
        const value = this._ready(key) ? this._number(key) : null;
        root.querySelector(`#${key}-value`).textContent = this._format(value, digits, unit);
        root.querySelector(`[data-key="${key}"]`).classList.toggle("unavailable", value === null);
      }
      const pendingOutput = this._pending.has("output");
      for (const [id, state] of [["output-on","on"],["output-off","off"]]) {
        const b = root.querySelector(`#${id}`);
        b.disabled = !online || pendingOutput;
        b.classList.toggle("active", online && output.state === state);
        b.classList.toggle("pending", pendingOutput);
        b.setAttribute("aria-pressed", String(online && output.state === state));
      }
      root.querySelector(".limits").textContent = `${this._t("limits")}: ${this._format(this._max("voltage_set"),3,"V")} / ${this._format(this._max("current_set"),4,"A")}`;
      for (const [id, attr] of [["web-link","web_control_url"],["diag-link","diagnostics_url"]]) {
        const link = root.querySelector(`#${id}`), url = output?.attributes[attr];
        try { const parsed = new URL(url); if (!["http:","https:"].includes(parsed.protocol)) throw new Error(); link.href = parsed.href; link.hidden = false; }
        catch {link.hidden = true; link.removeAttribute("href");}
      }
      root.querySelector(".message").textContent = this._discoveryError || this._error || "";
      root.querySelector("#chart-hint").textContent = this._pending.size ? this._t("pending") : "";
      this._draw();
    }
    _max(key) { return Number(this._state(key)?.attributes.max)*this._factor(key) || 0; }
    _parse(input) {
      const text = String(input).trim().replace(",", ".");
      return text && /^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text) ? Number(text) : NaN;
    }
    _step(key, direction) {
      const digits = key === "voltage_set" ? 3 : 4;
      const factor = 10 ** digits;
      const input = this.shadowRoot.querySelector(`#${key}-input`);
      const current = this._parse(input.value);
      const increment = Number(this._config[key === "voltage_set" ? "voltage_step" : "current_step"] || (key === "voltage_set" ? .1 : .001));
      if (!Number.isFinite(current) || !Number.isFinite(increment) || increment <= 0) return;
      const value = Math.max(0, Math.min(this._max(key), Math.round((current + direction * increment) * factor) / factor));
      input.value = value.toFixed(digits);
      this._draft[key] = input.value;
      this._dirty.add(key);
      this._syncDraft(key);
    }
    async _apply(key) {
      if (this._pending.has(key) || !this._dirty.has(key) || !this._ready(key)) return;
      const value = this._parse(this.shadowRoot.querySelector(`#${key}-input`).value);
      if (!Number.isFinite(value) || value < 0 || value > this._max(key)) {this._error = this._t("invalid"); this._sync(); return;}
      this._pending.add(key); this._error = ""; this._sync();
      try {
        await this._hass.callService("number", "set_value", {entity_id:this._ids[key], value:value/this._factor(key)});
        this._dirty.delete(key);
      } catch (e) {this._error = e.message || String(e);}
      finally {this._pending.delete(key); this._sync();}
    }
    async _output(on) {
      if (this._pending.has("output") || !this._outputReady()) return;
      this._pending.add("output"); this._error = ""; this._sync();
      try {await this._hass.callService("switch", on ? "turn_on" : "turn_off", {entity_id:this._ids.output});}
      catch (e) {this._error = e.message || String(e);}
      finally {this._pending.delete("output"); this._sync();}
    }
    async _loadHistory() {
      if (!this.isConnected || !this._hass || !this._ids.voltage || !this._ids.current || this._historyBusy) return;
      this._historyBusy = true;
      this._historyError = "";
      const hours = this._hours, serial = this._serial;
      const ids = [this._ids.voltage, this._ids.current];
      const end = new Date(), start = new Date(end.getTime() - hours * 3600000);
      this._draw();
      try {
        // Keep per-state units: a user may have changed A to mA within the
        // selected period. Assuming today's unit for old history is incorrect.
        const query = new URLSearchParams({filter_entity_id:ids.join(","), end_time:end.toISOString()});
        const response = await this._hass.callApi("GET", `history/period/${start.toISOString()}?${query}`);
        if (hours !== this._hours || serial !== this._serial) return;
        this._history = ids.map((id,index) => {
          const rows = response.find(series => series[0]?.entity_id === id) || [];
          return rows.map(row => {
            const valid=row.state !== "" && !["unknown","unavailable"].includes(row.state);
            const value=valid ? Number(row.state)*this._factor(index === 0 ? "voltage" : "current",row.attributes?.unit_of_measurement || (index === 0 ? "V" : "A")) : NaN;
            return {time:new Date(row.last_changed || row.last_updated).getTime(),value:Number.isFinite(value)?value:null};
          }).filter(p => Number.isFinite(p.time));
        });
        this._historyStart = start.getTime();
        this._historyEnd = end.getTime();
        this._historyAt = Date.now();
      } catch (e) {
        this._historyError = this._t("history_error");
        this._historyAt = Date.now();
      } finally {
        this._historyBusy = false;
        this._draw();
        if (hours !== this._hours || serial !== this._serial) this._loadHistory();
      }
    }
    _axisMax(series, fallback) {
      const observed = series.reduce((maximum,p) => Math.max(maximum,p.value ?? 0), 0);
      const target = observed > 0 ? observed * 1.12 : fallback;
      const exponent = 10 ** Math.floor(Math.log10(Math.max(target, .0001)));
      return Math.ceil(target / exponent / .5) * exponent * .5;
    }
    _draw() {
      const root = this.shadowRoot;
      const svg = root.querySelector("svg");
      if (!svg) return;
      const token = `${this._hours}|${this._historyAt}|${this._historyBusy}|${this._historyError}|${this._ru}`;
      if (this._drawToken === token) return;
      this._drawToken = token;
      const bounds = svg.getBoundingClientRect();
      const W=Math.max(320,Math.round(bounds.width || 780)), H=Math.max(240,Math.round(bounds.height || 420));
      const L=56, R=W-56, T=16, B=H-42;
      svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
      this._plotGeometry = {W,L,R};
      const start = this._historyStart || Date.now()-this._hours*3600000, end = this._historyEnd || Date.now();
      const voltage = this._history[0] || [], current = this._history[1] || [];
      const vmax = this._axisMax(voltage, 1), imax = this._axisMax(current, .001);
      const x = time => L+(Math.max(start, Math.min(end,time))-start)/(end-start)*(R-L);
      const y = (value,max) => B-Math.max(0, Math.min(max,value))/max*(B-T);
      const reduced = series => {
        if(series.length <= W*4) return series;
        const out=[]; let bucket=[], index=-1;
        const flush=()=>{
          if(!bucket.length)return;
          const low=bucket.reduce((a,b)=>a.value<b.value?a:b), high=bucket.reduce((a,b)=>a.value>b.value?a:b);
          out.push(...[...new Set([bucket[0],low,high,bucket.at(-1)])].sort((a,b)=>a.time-b.time)); bucket=[];
        };
        for(const point of series) {
          const next=Math.floor((point.time-start)/(end-start)*(R-L));
          if(point.value===null){flush();out.push(point);index=-1;continue;}
          if(next!==index){flush();index=next;}
          bucket.push(point);
        }
        flush(); return out;
      };
      const path = (series,max) => {
        let d="", previous=null;
        for (const p of reduced(series)) {
          if (p.value === null) {if(previous) d += ` H${x(p.time).toFixed(2)}`; previous=null; continue;}
          const px=x(p.time),py=y(p.value,max);
          // Recorder stores state changes, so a step path is truthful here.
          d += previous ? ` H${px.toFixed(2)} V${py.toFixed(2)}` : ` M${px.toFixed(2)},${py.toFixed(2)}`;
          previous=p;
        }
        if (previous) d += ` H${R}`;
        return d;
      };
      const label = value => new Intl.NumberFormat(this._ru ? "ru-RU":"en-US", {maximumSignificantDigits:4}).format(value);
      let content = `<title>${this._t("history")}</title>`;
      for(let i=0;i<=5;i++) {
        const yy=B-i/5*(B-T);
        content+=`<line x1="${L}" y1="${yy}" x2="${R}" y2="${yy}" stroke="currentColor" opacity=".10"/><text x="${L-10}" y="${yy+4}" text-anchor="end" fill="currentColor" opacity=".55" font-size="11">${label(vmax*i/5)}</text><text x="${R+10}" y="${yy+4}" fill="currentColor" opacity=".55" font-size="11">${label(imax*i/5)}</text>`;
      }
      for(let i=0;i<=4;i++) {
        const xx=L+i/4*(R-L), date = new Date(start+i/4*(end-start));
        content+=`<line x1="${xx}" y1="${T}" x2="${xx}" y2="${B}" stroke="currentColor" opacity=".10"/><text x="${xx}" y="${B+24}" text-anchor="middle" fill="currentColor" opacity=".55" font-size="11">${date.toLocaleTimeString(this._ru?"ru-RU":"en-US",{hour:"2-digit",minute:"2-digit",hour12:false})}</text>`;
      }
      content+=`<text x="18" y="${T+4}" fill="#5fc66b" font-size="12">V</text><text x="${W-23}" y="${T+4}" fill="#ef5d62" font-size="12">A</text><path d="${path(voltage,vmax)}" fill="none" stroke="#5fc66b" stroke-width="2" vector-effect="non-scaling-stroke"/><path d="${path(current,imax)}" fill="none" stroke="#ef5d62" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
      svg.innerHTML = content;
      const hasData = voltage.some(p=>p.value !== null) || current.some(p=>p.value !== null);
      root.querySelector(".chart-message").textContent = this._historyError || (!hasData ? this._t(this._historyBusy ? "history_loading" : "nohistory") : "");
    }
    _hover(event) {
      if (!this._historyStart) return;
      const bounds=this.shadowRoot.querySelector("svg").getBoundingClientRect();
      const geometry=this._plotGeometry || {W:780,L:56,R:724};
      const fraction=((event.clientX-bounds.left)/bounds.width*geometry.W-geometry.L)/(geometry.R-geometry.L);
      if(fraction<0||fraction>1) return;
      const timestamp=this._historyStart+fraction*(this._historyEnd-this._historyStart);
      const values=this._history.map(series=>{let value=null;for(const p of series){if(p.time>timestamp)break;value=p.value;}return value;});
      const tooltip=this.shadowRoot.querySelector(".plot-hover");
      tooltip.textContent=`${new Date(timestamp).toLocaleTimeString(this._ru?"ru-RU":"en-US")}  ·  ${this._format(values[0],3,"V")}  ·  ${this._format(values[1],4,"A")}`;
      tooltip.style.display="block";
    }
  }

  class RidenCardEditor extends HTMLElement {
    constructor() {super(); this.attachShadow({mode:"open"});}
    setConfig(config) {this._config={...config}; this._render();}
    set hass(hass) {this._hass=hass; this._render();}
    _render() {
      if(!this._config || this.shadowRoot.children.length) return;
      this.shadowRoot.innerHTML=`<style>label{display:block;margin:14px 0;font:14px Arial,sans-serif}input{display:block;width:95%;padding:10px;margin-top:6px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px}</style><label>Title / Название<input data-key="title" value="${ESC(this._config.title || "RD6006P")}"></label><label>Serial / Серийный номер (пусто = автоматически)<input data-key="serial" value="${ESC(this._config.serial || "")}"></label><label>Hours / Часов истории (0.25–24)<input data-key="hours_to_show" type="number" min="0.25" max="24" step="0.25" value="${Number(this._config.hours_to_show || 1)}"></label>`;
      this.shadowRoot.querySelectorAll("input").forEach(input=>input.addEventListener("change",()=>{
        const key=input.dataset.key;
        if(key==="serial" && !input.value.trim()) delete this._config.serial;
        else this._config[key]=key==="hours_to_show"?Number(input.value):input.value;
        this.dispatchEvent(new CustomEvent("config-changed",{detail:{config:this._config},bubbles:true,composed:true}));
      }));
    }
  }
  if(!customElements.get(TAG)) customElements.define(TAG,RidenCard);
  if(!customElements.get("rd6006p-card-editor")) customElements.define("rd6006p-card-editor",RidenCardEditor);
  window.customCards=window.customCards||[];
  if(!window.customCards.some(card=>card.type===TAG)) window.customCards.push({type:TAG,name:"RD6006P",description:"Local power supply control with V/A history",preview:true});
  console.info(`RD6006P card ${VERSION}`);
})();
