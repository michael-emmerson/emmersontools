"use strict";

const ROAST_LEVELS = ["Light", "Medium Light", "Medium", "Medium Dark", "Dark", "French"];
const LABEL_COLORS = { "medium dark": "#E3CFF0", light: "#EFF0BA", "medium light": "#F2E0A1", medium: "#F0CAE4", dark: "#CAE0F0", french: "#9AA4F2" };

let state = { beans: [], usage: [], settings: {}, app_settings: {} };
let review = null;
let editing = null; // bean being edited, or null when adding

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : String(c));
  return node;
}
const money = (v) => (v === null || v === undefined || isNaN(v) ? "" : (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2));
const lbs = (v) => (v === null || v === undefined ? "" : (+v.toFixed(2)).toString());
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const labelColor = (roast) => LABEL_COLORS[(roast || "").toLowerCase()] || "#";
function calculatedMsrp(diff) {
  const d = Math.round(diff * 1e6) / 1e6;
  if (d >= 1.5) return 24.99;
  if (d >= 1) return 20.99;
  if (d >= 0) return 19.99;
  return 18.99;
}

function toast(message, isError = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (isError ? " err" : "");
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), isError ? 7000 : 3000);
}

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-RRR-App": "1" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: `Unexpected response (${res.status})` }));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

async function run(action, okMessage) {
  try {
    const result = await action();
    if (okMessage) toast(okMessage);
    await load();
    return result;
  } catch (err) {
    toast(err.message, true);
    if (/changed since this page loaded/.test(err.message)) await load();
    throw err;
  }
}

// ---------- loading ----------
async function load() {
  state = await api("api/state");
  const banner = $("#banner");
  banner.hidden = !state.error;
  banner.textContent = state.error || "";
  const shown = state.app_settings?.cloud ? "Spreadsheet" : state.path;
  $("#sheet-info").textContent = state.error ? "No spreadsheet loaded" : `${shown} · saved ${state.modified.replace("T", " ")}`;
  // First run (or the file was moved): ask for the spreadsheet instead of showing empty tabs.
  const welcome = !!state.missing && !welcomeDismissed;
  $("#welcome").hidden = !welcome;
  $("main").hidden = welcome;
  $(".tabs").hidden = welcome;
  if (welcome) {
    banner.hidden = true;
    $("#welcome-where").textContent = state.app_settings.cloud ? "It will be stored in the app's Cloudflare storage, with a backup kept of every change."
      : `It will be saved as ${state.app_settings.spreadsheet}.`;
  }
  renderBeans();
  renderUsage();
  renderSettings();
}

// ---------- beans tab ----------
function renderBeans() {
  const q = $("#bean-search").value.trim().toLowerCase();
  const hideEmpty = $("#hide-empty").checked;
  const beans = state.beans.filter((b) => {
    if (hideEmpty && b.lbs_remaining !== null && b.lbs_remaining <= 0) return false;
    if (!q) return true;
    return [b.origin, b.varietal, b.provider, b.cultivar, b.tasting_notes, b.sku, b.upc, b.roast_level].join(" ").toLowerCase().includes(q);
  });
  const tbody = $("#bean-table tbody");
  tbody.replaceChildren(
    ...beans.map((b) => {
      const pct = b.lbs ? Math.max(0, Math.min(100, (b.lbs_remaining / b.lbs) * 100)) : 0;
      const leftClass = b.lbs_remaining !== null && b.lbs_remaining <= 0 ? "out" : pct < 25 ? "low" : "";
      return el("tr", { onclick: () => openBean(b) },
        el("td", {}, el("span", { class: "swatch" }, el("i", { style: `background:${b.label_color.length > 1 ? b.label_color : "transparent"}` }), b.roast_level || "—")),
        el("td", {}, b.origin), el("td", {}, el("b", {}, b.varietal)), el("td", {}, b.provider), el("td", {}, b.arrival_date),
        el("td", { class: "num" }, b.cupping_score ?? ""), el("td", { class: "num" }, lbs(b.lbs)), el("td", { class: "num" }, lbs(b.lbs_used)),
        el("td", { class: "num " + leftClass }, lbs(b.lbs_remaining), b.lbs ? el("span", { class: "bar" }, el("span", { style: `width:${pct}%` })) : ""),
        el("td", { class: "num" }, money(b.cost_per_lb)),
        el("td", { class: "num" }, money(b.msrp), b.msrp_manual ? el("span", { class: "manual-mark", title: "Typed in, not calculated" }, " ✎") : ""),
        el("td", { class: "num" }, money(b.cogs)), el("td", { class: "num" + (b.margin < 0 ? " neg" : "") }, money(b.margin)),
        el("td", {}, b.sku),
        el("td", {}, el("button", { class: "small", onclick: (e) => { e.stopPropagation(); openBean(b, true); } }, "Log usage")),
      );
    }),
  );
  if (!beans.length) tbody.append(el("tr", {}, el("td", { colspan: 15, class: "empty" }, state.beans.length ? "No beans match." : "No beans yet. Add one to get started.")));

  const totalLeft = state.beans.reduce((s, b) => s + (b.lbs_remaining || 0), 0);
  const value = state.beans.reduce((s, b) => s + Math.max(0, b.lbs_remaining || 0) * (b.cost_per_lb || 0), 0);
  const low = state.beans.filter((b) => b.lbs && b.lbs_remaining / b.lbs < 0.25).length;
  $("#totals").replaceChildren(
    el("div", { class: "stat" }, el("b", {}, state.beans.length), el("span", {}, "beans")),
    el("div", { class: "stat" }, el("b", {}, lbs(totalLeft)), el("span", {}, "lbs left")),
    el("div", { class: "stat" }, el("b", {}, money(value)), el("span", {}, "green coffee on hand (at cost)")),
    el("div", { class: "stat" }, el("b", {}, low), el("span", {}, "under 25% left")),
  );
}

function fillRoastSelect(select) {
  if (select.options.length > 1) return;
  ROAST_LEVELS.forEach((r) => select.append(el("option", { value: r }, r)));
}

function openBean(bean, focusUsage = false) {
  editing = bean || null;
  const form = $("#bean-form");
  form.reset();
  fillRoastSelect(form.roast_level);
  $("#bean-dialog-title").textContent = bean ? bean.varietal : "Add a bean";
  $("#bean-delete").hidden = !bean;
  const fields = ["origin", "varietal", "provider", "arrival_date", "cultivar", "cupping_score", "tasting_notes", "roast_level", "lbs", "cost_per_lb", "sku", "upc"];
  for (const f of fields) form[f].value = bean ? bean[f] ?? "" : "";
  if (!bean) form.arrival_date.value = today();
  form.msrp_manual.checked = !!(bean && bean.msrp_manual);
  form.msrp.value = bean && bean.msrp_manual ? bean.msrp : "";
  updateCalc();
  renderBeanUsage();
  $("#bean-dialog").showModal();
  if (focusUsage && bean) $("#bean-usage input[name=lbs]").focus();
}

function updateCalc() {
  const form = $("#bean-form");
  const s = state.settings;
  const cost = parseFloat(form.cost_per_lb.value) || 0;
  const diff = cost - s.average_cost;
  const manual = form.msrp_manual.checked;
  $("#msrp-field").hidden = !manual;
  const msrp = manual ? parseFloat(form.msrp.value) || 0 : calculatedMsrp(diff);
  const cogs = cost + s.cogs_adder;
  const color = labelColor(form.roast_level.value);
  $("#calc").replaceChildren(
    el("div", {}, el("span", {}, "Label color"), el("b", { class: "swatch" }, el("i", { style: `background:${color.length > 1 ? color : "transparent"}` }), color)),
    el("div", {}, el("span", {}, `Diff to ave (− ${s.average_cost})`), el("b", {}, money(diff))),
    el("div", {}, el("span", {}, manual ? `MSRP (typed; formula says ${money(calculatedMsrp(diff))})` : "MSRP (formula)"), el("b", {}, money(msrp))),
    el("div", {}, el("span", {}, `COGS (+ ${s.cogs_adder})`), el("b", {}, money(cogs))),
    el("div", {}, el("span", {}, "Margin"), el("b", { class: msrp - cogs < 0 ? "neg" : "" }, money(msrp - cogs))),
  );
}

function renderBeanUsage() {
  const box = $("#bean-usage");
  if (!editing) return box.replaceChildren();
  const entries = state.usage.filter((u) => u.varietal === editing.varietal);
  const quick = el("div", { class: "inline-form" },
    el("label", {}, "Lbs used", el("input", { name: "lbs", type: "number", step: "0.01", min: "0.01", form: "none" })),
    el("label", {}, "Date", el("input", { name: "date", type: "date", value: today(), form: "none" })),
    el("label", { class: "grow" }, "Note", el("input", { name: "note", form: "none" })),
    el("button", { type: "button", class: "primary", onclick: logFromDialog }, "Log usage"),
  );
  box.replaceChildren(
    el("h3", {}, `Usage · ${lbs(editing.lbs_used)} of ${lbs(editing.lbs) || 0} lbs used, ${lbs(editing.lbs_remaining) || 0} left`),
    quick,
    entries.length
      ? el("div", { class: "table-wrap" }, el("table", { class: "grid" },
          el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", { class: "num" }, "Lbs"), el("th", {}, "Note"), el("th", {}))),
          el("tbody", {}, entries.map((u) => el("tr", {}, el("td", {}, u.date), el("td", { class: "num" }, lbs(u.lbs)), el("td", { class: "wrap" }, u.note),
            el("td", {}, el("button", { type: "button", class: "small danger", onclick: () => deleteUsage(u) }, "Remove")))))))
      : el("p", { class: "hint" }, "No usage logged yet."),
  );
}

async function logFromDialog() {
  const box = $("#bean-usage");
  const lbsUsed = $("input[name=lbs]", box).value;
  if (!(parseFloat(lbsUsed) > 0)) return toast("Enter how many pounds you used.", true);
  await run(() => api("api/usage/add", { row: editing.row, expect: editing.varietal, lbs: lbsUsed, date: $("input[name=date]", box).value, note: $("input[name=note]", box).value }), "Usage saved to the spreadsheet");
  editing = state.beans.find((b) => b.varietal === editing.varietal) || null;
  renderBeanUsage();
}

async function deleteUsage(u) {
  if (!confirm(`Remove ${lbs(u.lbs)} lbs used on ${u.date} for ${u.varietal}?`)) return;
  await run(() => api("api/usage/delete", { id: u.id, expect: u.varietal }), "Usage entry removed");
  if (editing) {
    editing = state.beans.find((b) => b.varietal === editing.varietal) || null;
    renderBeanUsage();
  }
}

async function saveBean(e) {
  e.preventDefault();
  const form = $("#bean-form");
  const bean = {};
  for (const f of ["origin", "varietal", "provider", "arrival_date", "cultivar", "cupping_score", "tasting_notes", "roast_level", "lbs", "cost_per_lb", "sku", "upc"]) bean[f] = form[f].value;
  bean.msrp_manual = form.msrp_manual.checked;
  bean.msrp = form.msrp.value;
  try {
    if (editing) await run(() => api("api/beans/update", { row: editing.row, expect: editing.varietal, bean }), "Saved to the spreadsheet");
    else await run(() => api("api/beans/add", { bean }), "Bean added to the spreadsheet");
    $("#bean-dialog").close();
  } catch { /* toast already shown */ }
}

async function deleteBean() {
  const used = state.usage.filter((u) => u.varietal === editing.varietal).length;
  if (!confirm(`Delete ${editing.varietal} from the spreadsheet${used ? ` and its ${used} usage entries` : ""}? A backup of the file is kept in the backups folder.`)) return;
  await run(() => api("api/beans/delete", { row: editing.row, expect: editing.varietal }), "Bean deleted");
  $("#bean-dialog").close();
}

// ---------- usage tab ----------
function renderUsage() {
  const form = $("#usage-quick");
  const current = form.row.value;
  form.row.replaceChildren(...state.beans.map((b) => el("option", { value: b.row }, `${b.varietal}${b.lbs_remaining !== null ? ` (${lbs(b.lbs_remaining)} lbs left)` : ""}`)));
  if (current) form.row.value = current;
  if (!form.date.value) form.date.value = today();
  $("#usage-table tbody").replaceChildren(
    ...(state.usage.length
      ? state.usage.map((u) => el("tr", {}, el("td", {}, u.date), el("td", {}, u.varietal), el("td", {}, u.sku), el("td", { class: "num" }, lbs(u.lbs)), el("td", { class: "wrap" }, u.note),
          el("td", {}, el("button", { class: "small danger", onclick: () => deleteUsage(u) }, "Remove"))))
      : [el("tr", {}, el("td", { colspan: 6, class: "empty" }, "No usage logged yet."))]),
  );
}

async function quickUsage(e) {
  e.preventDefault();
  const form = e.target;
  const bean = state.beans.find((b) => String(b.row) === form.row.value);
  if (!bean) return;
  try {
    await run(() => api("api/usage/add", { row: bean.row, expect: bean.varietal, lbs: form.lbs.value, date: form.date.value, note: form.note.value }), `Logged ${form.lbs.value} lbs of ${bean.varietal}`);
    form.lbs.value = "";
    form.note.value = "";
  } catch { /* shown */ }
}

// ---------- settings tab ----------
function renderSettings() {
  const a = state.app_settings;
  if (!a || !a.shopify) return;
  $("#sheet-location").hidden = !!a.cloud;  // online, the app picks where the file lives
  for (const n of document.querySelectorAll(".cloud-only")) n.hidden = !a.cloud;
  for (const n of document.querySelectorAll(".local-only")) n.hidden = !!a.cloud;
  const sheet = $("#settings-sheet");
  if (document.activeElement?.form !== sheet) {
    sheet.spreadsheet.value = a.spreadsheet;
    sheet.average_cost.value = state.settings.average_cost ?? 8.5;
    sheet.cogs_adder.value = state.settings.cogs_adder ?? 2;
  }
  const sh = $("#settings-shopify");
  if (document.activeElement?.form !== sh) {
    sh.shop.value = a.shopify.shop;
    sh.client_id.value = a.shopify.client_id;
    sh.client_secret.placeholder = a.shopify.client_secret_set ? "Saved (type to replace)" : "";
    sh.access_token.placeholder = a.shopify.access_token_set ? "Saved (type to replace)" : "";
    sh.api_version.value = a.shopify.api_version;
    sh.lbs_per_bag.value = a.lbs_per_bag ?? "";
    const loc = sh.location_id;
    if (a.shopify.location_id && ![...loc.options].some((o) => o.value === a.shopify.location_id)) loc.append(el("option", { value: a.shopify.location_id }, "Saved location"));
    loc.value = a.shopify.location_id || "";
  }
}

async function saveSheetSettings(e) {
  e.preventDefault();
  const f = e.target;
  try {
    await run(() => api("api/settings", { spreadsheet: f.spreadsheet.value, average_cost: f.average_cost.value, cogs_adder: f.cogs_adder.value }), "Spreadsheet settings saved");
  } catch { /* shown */ }
}

async function saveShopifySettings(e) {
  e.preventDefault();
  const f = e.target;
  try {
    await run(() => api("api/settings", {
      shopify: { shop: f.shop.value, client_id: f.client_id.value, client_secret: f.client_secret.value, access_token: f.access_token.value, api_version: f.api_version.value, location_id: f.location_id.value },
      lbs_per_bag: f.lbs_per_bag.value,
    }), "Shopify settings saved");
    f.client_secret.value = "";
    f.access_token.value = "";
    review = null;
  } catch { /* shown */ }
}

// ---------- uploading a spreadsheet ----------
let welcomeDismissed = false;

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("Couldn't read that file."));
    reader.readAsDataURL(file);
  });
}

async function uploadSheet(e) {
  e.preventDefault();
  const form = e.target;
  const file = form.file.files[0];
  if (!file) return;
  const button = $("button[type=submit]", form);
  button.disabled = true;
  try {
    const body = { filename: file.name, data: await readAsBase64(file) };
    let res;
    try {
      res = await api("api/spreadsheet/upload", body);
    } catch (err) {
      if (!/already a spreadsheet/.test(err.message)) throw err;
      if (!confirm(`Replace the current spreadsheet with “${file.name}”?\n\nThe current file is copied to the backups folder first.`)) return;
      res = await api("api/spreadsheet/upload", { ...body, replace: true });
    }
    review = null;
    form.reset();
    const extra = res.warnings.length ? ` ${res.warnings.join(" ")}` : "";
    toast(`Loaded ${res.beans} bean${res.beans === 1 ? "" : "s"} from ${file.name}.${extra}`, false);
    await load();
    if (form.id === "welcome-form") switchTab("beans");
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

// ---------- shopify tab ----------
async function loadReview() {
  const body = $("#shopify-body");
  body.replaceChildren(el("p", { class: "empty" }, "Reading products from Shopify…"));
  $("#pushbar").hidden = true;
  try {
    review = await api("api/shopify/review");
  } catch (err) {
    review = null;
    body.replaceChildren(el("div", { class: "card" }, el("h2", {}, "Couldn't read Shopify"), el("p", {}, err.message),
      el("p", { class: "hint" }, "Check the Shopify connection in Settings."), el("button", { onclick: () => switchTab("settings") }, "Open Settings")));
    return;
  }
  const loc = $("#settings-shopify").location_id;
  for (const l of review.locations) if (![...loc.options].some((o) => o.value === l.id)) loc.append(el("option", { value: l.id }, l.name));
  loc.value = state.app_settings.shopify.location_id || "";
  $("#shopify-sub").textContent = `Comparing with ${review.shop}. Nothing is sent to Shopify until you tick a change and press Push.`;
  renderReview();
}

function linkSelect(row) {
  const select = el("select", { class: "link-select", onchange: async (e) => {
    const value = e.target.value;
    await run(() => api("api/shopify/link", { varietal: row.varietal, variant_id: value === "auto" ? "" : value }), "Link saved");
    loadReview();
  } },
  el("option", { value: "auto" }, "Match automatically"),
  el("option", { value: "none" }, "Not on Shopify"),
  review.choices.map((c) => el("option", { value: c.variant_id }, c.label)));
  const linked = state.app_settings && row.match === "linked by you" ? row.variant_id : row.match.startsWith("not linked") ? "none" : "auto";
  select.value = linked;
  return select;
}

function renderReview() {
  const rows = review.rows;
  const pending = rows.reduce((n, r) => n + r.changes.length, 0);
  const table = el("table", { class: "grid", id: "review-table" },
    el("thead", {}, el("tr", {}, el("th", {}, "Bean"), el("th", {}, "Shopify product"), el("th", { class: "num" }, "Sheet MSRP"), el("th", { class: "num" }, "Shopify price"),
      el("th", { class: "num" }, "Lbs left"), el("th", { class: "num" }, "Shopify stock"), el("th", {}, "Changes to approve"))),
    el("tbody", {}, rows.map((r) => {
      const changes = r.changes.map((c) => el("label", { class: "change" },
        el("input", { type: "checkbox", "data-id": c.id, "data-from": c.from ?? "", "data-to": c.to ?? "", "data-label": `${r.varietal}: ${c.label}`, onchange: updatePushbar }),
        el("b", {}, c.label), c.from !== null && c.from !== undefined && c.from !== "" ? el("span", { class: "from" }, fmtValue(c.type, c.from)) : c.type === "sku" ? el("span", { class: "from" }, "(none)") : "",
        c.type !== "create" ? "→" : "", el("span", {}, fmtValue(c.type, c.to)), c.note ? el("span", { class: "note" }, `(${c.note})`) : ""));
      if (r.variant_id && r.tracked && r.location_id && !r.changes.some((c) => c.type === "stock")) {
        const id = `stock|${r.variant_id}`;
        const box = el("input", { type: "checkbox", "data-id": id, "data-from": r.stock ?? "", "data-manual": "1", "data-label": `${r.varietal}: Stock`, onchange: updatePushbar });
        const input = el("input", { type: "number", min: "0", step: "1", class: "stock-input", placeholder: r.stock ?? "", oninput: (e) => { box.dataset.to = e.target.value; box.checked = e.target.value !== ""; updatePushbar(); } });
        changes.push(el("label", { class: "change" }, box, el("b", {}, "Set stock to"), input, el("span", { class: "note" }, r.location ? `at ${r.location}` : "")));
      }
      return el("tr", {},
        el("td", {}, el("b", {}, r.varietal), el("span", { class: "match" }, r.sku ? `SKU ${r.sku}` : "No SKU in sheet")),
        el("td", { class: "wrap" }, r.product_title ? el("div", {}, r.product_title, r.product_status !== "ACTIVE" ? el("span", { class: "match" }, r.product_status.toLowerCase()) : "") : el("i", {}, "—"),
          el("span", { class: "match" }, `Matched by: ${r.match}`), linkSelect(r)),
        el("td", { class: "num" }, money(r.msrp)),
        el("td", { class: "num" + (r.shopify_price !== undefined && Math.abs(r.shopify_price - r.msrp) >= 0.005 ? " low" : "") }, r.shopify_price !== undefined ? money(r.shopify_price) : ""),
        el("td", { class: "num" }, lbs(r.lbs_remaining)),
        el("td", { class: "num" }, r.stock ?? (r.variant_id ? (r.tracked ? "—" : "not tracked") : "")),
        el("td", {}, changes.length ? el("div", { class: "changes" }, changes) : el("span", { class: "ok" }, r.variant_id ? "✓ In sync" : "")),
      );
    })));
  const parts = [
    el("p", { class: "hint" }, pending ? `${pending} difference${pending === 1 ? "" : "s"} found. Tick the ones you want to send.` : "Prices and SKUs match. You can still set stock counts by hand."),
    el("div", { class: "table-wrap" }, table),
  ];
  if (review.extras.length) {
    parts.push(el("div", { class: "card", style: "margin-top:16px" }, el("h2", {}, "In Shopify but not in the spreadsheet"),
      el("ul", {}, review.extras.map((p) => el("li", {}, `${p.title} (${p.status.toLowerCase()}${p.price !== null ? `, ${money(p.price)}` : ""})`))),
      el("p", { class: "hint" }, "The app never changes these. Add them as beans if you want to track them.")));
  }
  parts.push(el("div", { id: "push-results" }));
  $("#shopify-body").replaceChildren(...parts);
  $("#pushbar").hidden = false;
  updatePushbar();
}

function fmtValue(type, v) {
  return type === "price" ? money(+v) : String(v);
}

function approvedItems() {
  return $$("#review-table input[type=checkbox]:checked").map((c) => ({ id: c.dataset.id, from: c.dataset.from === "" ? null : c.dataset.from, to: c.dataset.to === "" ? null : c.dataset.to, label: c.dataset.label, manual: !!c.dataset.manual }));
}

function updatePushbar() {
  const n = approvedItems().length;
  $("#push-count").textContent = `${n} change${n === 1 ? "" : "s"} approved`;
  $("#push-btn").disabled = n === 0;
}

function confirmPush() {
  const items = approvedItems();
  const bad = items.find((i) => i.manual && (i.to === null || !/^\d+$/.test(i.to)));
  if (bad) return toast(`${bad.label}: enter a whole number of bags.`, true);
  $("#push-list").replaceChildren(...items.map((i) => {
    const type = i.id.split("|")[0];
    return el("li", {}, `${i.label}: `, type === "create" ? i.to : `${i.from === null ? "(none)" : fmtValue(type, i.from)} → ${fmtValue(type, i.to)}`);
  }));
  $("#push-dialog").showModal();
}

async function doPush() {
  const items = approvedItems();
  const btn = $("#push-confirm");
  btn.disabled = true;
  btn.textContent = "Pushing…";
  try {
    const res = await api("api/shopify/push", { approved: items.map(({ id, from, to }) => ({ id, from, to })) });
    $("#push-dialog").close();
    await loadReview();
    const ok = res.results.filter((r) => r.ok).length;
    $("#push-results").replaceChildren(el("div", { class: "card", style: "margin-top:16px" },
      el("h2", {}, `Pushed ${ok} of ${res.results.length} change${res.results.length === 1 ? "" : "s"}`),
      el("ul", { class: "results" }, res.results.map((r) => el("li", { class: r.ok ? "good" : "bad" }, `${r.varietal} · ${r.change}: ${r.result}`))),
      res.log_error ? el("p", { class: "neg" }, res.log_error) : el("p", { class: "hint" }, "Recorded in the spreadsheet's “Shopify Sync Log” sheet.")));
    toast(ok === res.results.length ? "Shopify updated" : "Some changes didn't go through; see the list", ok !== res.results.length);
    load();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Yes, push to Shopify";
  }
}

// ---------- tabs and wiring ----------
function switchTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
  $("#pushbar").hidden = name !== "shopify" || !review;
  if (name === "shopify" && !review) loadReview();
}

$$(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
$("#bean-search").addEventListener("input", renderBeans);
$("#hide-empty").addEventListener("change", renderBeans);
$("#add-bean").addEventListener("click", () => openBean(null));
$("#bean-form").addEventListener("submit", saveBean);
$("#bean-form").addEventListener("input", updateCalc);
$("#bean-delete").addEventListener("click", deleteBean);
$$("[data-close]").forEach((b) => b.addEventListener("click", () => $("#bean-dialog").close()));
$("#usage-quick").addEventListener("submit", quickUsage);
$("#settings-sheet").addEventListener("submit", saveSheetSettings);
$("#settings-shopify").addEventListener("submit", saveShopifySettings);
$("#settings-upload").addEventListener("submit", uploadSheet);
$("#welcome-form").addEventListener("submit", uploadSheet);
$("#welcome-settings").addEventListener("click", (e) => {
  e.preventDefault();
  welcomeDismissed = true;
  load().then(() => switchTab("settings")).catch((err) => toast(err.message, true));
});
$("#shopify-test").addEventListener("click", async () => {
  try {
    const r = await api("api/shopify/test", {});
    toast(`Connected to ${r.shop.name} (${r.shop.myshopifyDomain})`);
  } catch (err) { toast(err.message, true); }
});
$("#shopify-clear").addEventListener("click", async () => {
  if (!confirm("Remove the saved Client secret and access token from this computer?")) return;
  await run(() => api("api/settings", { shopify: { clear_secrets: true } }), "Secrets removed");
});
$("#shopify-refresh").addEventListener("click", loadReview);
$("#push-btn").addEventListener("click", confirmPush);
$("#push-cancel").addEventListener("click", () => $("#push-dialog").close());
$("#push-confirm").addEventListener("click", doPush);
// Pick up edits made directly in the spreadsheet when you come back to the tab.
document.addEventListener("visibilitychange", () => { if (!document.hidden && !$("#bean-dialog").open) load().catch(() => {}); });

load().catch((err) => toast(err.message, true));
