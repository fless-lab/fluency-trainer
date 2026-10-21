"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const overlay = $("#overlay");
let STATE = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Click to hear: any element with data-say speaks its text with the app's voice (made once on the server, then cached),
// or with the browser voice if the app's voice is not available. Use sayable(text) in templates.
let sayAudio = null, sayEl = null;
function sayable(text, shown) {
  return `<span class="say" role="button" tabindex="0" data-say="${esc(text)}" title="Click to hear it">${esc(shown == null ? text : shown)}</span>`;
}
// words between quotes in a sentence become clickable too: Practice with 'red' vs 'led'
function sayQuoted(text) {
  return esc(text).replace(/(&#39;|&quot;|‘|“)([A-Za-z][A-Za-z -]{0,28}[A-Za-z])(&#39;|&quot;|’|”)/g, (m, a, w, b) => `${a}<span class="say" role="button" tabindex="0" data-say="${w}" title="Click to hear it">${w}</span>${b}`);
}
function stopSay() {
  if (sayEl) { sayEl.classList.remove("saying"); sayEl = null; }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  if (sayAudio) { const a = sayAudio; sayAudio = null; a.pause(); a.dispatchEvent(new Event("ended")); }
}
async function sayText(text, el) {
  stopSay();
  if (el) { el.classList.add("saying"); sayEl = el; }
  const a = new Audio(`/api/say?text=${encodeURIComponent(text)}`);
  sayAudio = a;
  try {
    await new Promise((res, rej) => { a.onended = res; a.onerror = () => rej(new Error("no audio")); a.play().catch(rej); });
  } catch (_) {
    if (sayAudio === a) await speakBrowser(text);  // the app's voice is not set up, capped, or failed: the browser voice
  }
  if (sayAudio === a) sayAudio = null;
  if (el && sayEl === el) { el.classList.remove("saying"); sayEl = null; }
}
document.addEventListener("click", (e) => {
  const el = e.target.closest && e.target.closest("[data-say]");
  if (!el) return;
  e.preventDefault();  // inside a label this must not tick the box next to it
  sayText(el.dataset.say, el);
});
// play a piece of your own recording
let clipAudio = null;
document.addEventListener("click", (e) => {
  const el = e.target.closest && e.target.closest("[data-clip]");
  if (!el) return;
  e.preventDefault(); stopSay();
  if (clipAudio) { clipAudio.pause(); clipAudio = null; }
  const a = new Audio(el.dataset.clip); clipAudio = a; el.classList.add("saying");
  const done = () => { el.classList.remove("saying"); if (clipAudio === a) clipAudio = null; };
  a.onended = a.onerror = done; a.play().catch(done);
});
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("[data-say]")) { e.preventDefault(); sayText(e.target.dataset.say, e.target); }
});

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    const err = new Error(`${res.status} ${detail}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function logEvent(type, payload = {}, sessionId = null) {
  api("/api/client-event", { method: "POST", body: { type, session_id: sessionId, payload } }).catch(() => {});
}

window.addEventListener("error", (e) => logEvent("js_error", { message: e.message, source: e.filename, line: e.lineno }));
window.addEventListener("unhandledrejection", (e) => logEvent("js_rejection", { reason: String(e.reason) }));

const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "n/a" : Number(v).toFixed(d));
const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function confetti(n = 36) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ["#fcd84a", "#f472b6", "#6ee7b7", "#5cc8f0", "#ff7b5c", "#7c3aed"];
  const box = document.createElement("div");
  box.className = "confetti";
  for (let i = 0; i < n; i++) {
    const p = document.createElement("i");
    p.style.cssText = `left:${Math.random() * 100}%;background:${colors[i % colors.length]};--dx:${Math.round(Math.random() * 160 - 80)}px;--rot:${Math.round(Math.random() * 720 - 360)}deg;animation-delay:${Math.round(Math.random() * 300)}ms;animation-duration:${1400 + Math.round(Math.random() * 900)}ms;${i % 3 === 0 ? "border-radius:50%;" : ""}`;
    box.appendChild(p);
  }
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 2800);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const METRIC_ORDER = [
  ["words_per_minute", "Words per minute", 1],
  ["articulation_rate_syll_per_s", "Articulation rate (syll/s, estimated)", 2],
  ["phonation_ratio", "Phonation ratio", 2],
  ["mean_length_of_run_words", "Mean length of run (words)", 1],
  ["pauses_per_100_words", "Pauses of 250 ms or more, per 100 words", 1],
  ["mid_clause_pauses_per_100_words", "Mid-clause pauses per 100 words (heuristic)", 1],
  ["boundary_pauses_per_100_words", "Clause-boundary pauses per 100 words (heuristic)", 1],
  ["long_pauses_per_100_words", "Pauses over 1 s per 100 words", 1],
  ["max_pause_ms", "Longest pause (ms)", 0],
  ["fillers_per_100_words", "Fillers per 100 words", 1],
  ["repetitions_per_100_words", "Repetitions per 100 words", 1],
  ["onset_latency_s", "Time to first word (s)", 1],
  ["content_words", "Words (without fillers)", 0],
  ["speech_span_s", "Speech span (s)", 0],
];

// ---------------------------------------------------------------- pending uploads (IndexedDB)
function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("fluency-trainer", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("pending", { keyPath: "sessionId" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function pendingPut(item) {
  try {
    const db = await idb();
    await new Promise((res, rej) => { const tx = db.transaction("pending", "readwrite"); tx.objectStore("pending").put(item); tx.oncomplete = res; tx.onerror = () => rej(tx.error); });
    return true;
  } catch (e) { logEvent("idb_put_failed", { error: String(e) }, item.sessionId); return false; }
}
async function pendingDelete(sessionId) {
  try {
    const db = await idb();
    await new Promise((res, rej) => { const tx = db.transaction("pending", "readwrite"); tx.objectStore("pending").delete(sessionId); tx.oncomplete = res; tx.onerror = () => rej(tx.error); });
  } catch (_) {}
}
async function pendingAll() {
  try {
    const db = await idb();
    return await new Promise((res, rej) => { const tx = db.transaction("pending", "readonly"); const r = tx.objectStore("pending").getAll(); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  } catch (_) { return []; }
}

async function uploadAudio(item) {
  const fd = new FormData();
  fd.append("file", item.blob, `rec.${item.mime.includes("mp4") ? "m4a" : item.mime.includes("ogg") ? "ogg" : "webm"}`);
  fd.append("duration_s", String(item.durationS));
  fd.append("mime", item.mime);
  return api(`/api/sessions/${item.sessionId}/audio`, { method: "POST", body: fd });
}

async function retryPending() {
  const items = await pendingAll();
  for (const item of items) {
    try {
      await uploadAudio(item);
      await pendingDelete(item.sessionId);
      logEvent("pending_upload_recovered", {}, item.sessionId);
    } catch (e) {
      logEvent("pending_upload_still_failing", { error: String(e) }, item.sessionId);
    }
  }
  return items.length;
}

// ---------------------------------------------------------------- shell
async function refreshState() {
  STATE = await api("/api/state");
  $("#day-badge").textContent = STATE.day === null ? "Not started" : `Day ${STATE.day}`;
  const pb = $("#provider-badge");
  pb.textContent = STATE.provider === "mock" ? "MOCK transcripts" : `Speech: ${STATE.provider}`;
  pb.className = "badge" + (STATE.provider === "mock" || STATE.provider_error ? " warn" : "");
  const banner = $("#banner");
  if (STATE.provider_error) { banner.hidden = false; banner.textContent = STATE.provider_error; }
  else if (STATE.provider === "mock") { banner.hidden = false; banner.textContent = "Mock mode: transcripts are invented. Use it to try the app, not to measure yourself."; }
  else banner.hidden = true;
}

const TABS = { today: renderToday, notebook: renderNotebook, history: renderHistory, progress: renderProgress, data: renderData };
let currentTab = "today";
async function show(tab) {
  currentTab = tab;
  document.body.dataset.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  view.innerHTML = '<p class="muted">Loading…</p>';
  try { await TABS[tab](); } catch (e) { view.innerHTML = `<p class="err">Something went wrong: ${esc(e.message)}</p>`; logEvent("render_error", { tab, error: String(e) }); }
}
$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) show(b.dataset.tab); });

// ---------------------------------------------------------------- today
function statusTag(sessions) {
  if (!sessions.length) return "";
  const best = sessions.find((s) => s.status === "done") || sessions[sessions.length - 1];
  return `<span class="status ${esc(best.status)}">${esc(best.status)}${best.provider === "mock" ? " (mock)" : ""}</span>`;
}

async function renderToday() {
  await refreshState();
  const pending = await pendingAll();
  let html = `<h2>Today</h2><p class="lead">Each test is 30 seconds of preparation, then 2 minutes of speaking. Do not write anything down. Speak the way you would to a person.</p>`;
  if (pending.length) html += `<div class="card"><strong>${pending.length} recording(s) saved on this device are not uploaded yet.</strong> <button class="secondary" id="retry-pending">Upload now</button></div>`;
  if (STATE.notebook_due) html += `<div class="card due"><strong>${STATE.notebook_due} notebook item(s) to review.</strong> <button class="secondary" id="go-nb">Review now</button></div>`;
  if (STATE.day === null) html += `<p class="muted small">The 30 day counter starts when you record your first session. You can also <a href="#" id="start-today">start it today</a>.</p>`;
  for (const cp of STATE.checkpoints) {
    html += `<div class="card ${cp.due ? "due" : ""}"><div class="row spread"><h3>${esc(cp.label)}</h3><span class="tag">${cp.due ? "due" : `from day ${cp.due_day}`}</span></div><p class="muted small">${esc(cp.hint)}</p>`;
    for (const t of cp.topics) {
      html += `<div class="topic"><div><div class="tag">${esc(t.type)}${t.is_repeat ? " · repeated from day 0" : ""}</div><p>${esc(t.text)}</p></div><div class="row">${statusTag(t.sessions)}<button class="primary" data-cp="${esc(cp.id)}" data-topic="${esc(t.topic_id)}">${t.sessions.length ? "Record again" : "Start"}</button></div></div>`;
    }
    html += `</div>`;
  }
  html += `<div class="card"><h3>Free monologue</h3><p class="muted small">Talk about anything. Logged and measured like the tests, but kept out of the checkpoint comparison.</p><div class="row"><input type="text" id="free-topic" placeholder="Topic (optional)" size="40"><select id="free-length"><option value="120">2 min</option><option value="300">5 min</option><option value="600">10 min</option></select><button class="primary" id="free-start">Start</button></div></div>`;
  html += `<div class="card"><h3>4/3/2 drill</h3><p class="muted small">Tell the same story three times, in 4, then 3, then 2 minutes. Same content each time, nothing new added. The shrinking time pushes you to find your words faster. About 10 minutes in all. This is training, not a test: it is kept out of the checkpoint comparison.</p><div class="row"><select id="drill-topic">${(STATE.drill_topics || []).map((t) => `<option value="${esc(t.text)}">${esc(t.text)}</option>`).join("")}<option value="">My own topic…</option></select></div><div class="row" style="margin-top:8px"><input type="text" id="drill-custom" placeholder="Your topic" size="50" hidden><button class="primary" id="drill-start">Start</button></div></div>`;
  html += await conversationCard();
  view.innerHTML = html;
  wireConversationCard();
  $("#drill-topic").addEventListener("change", (e) => { const c = $("#drill-custom"); c.hidden = e.target.value !== ""; if (!c.hidden) c.focus(); });
  $("#drill-start").addEventListener("click", () => {
    const chosen = $("#drill-topic").value || $("#drill-custom").value.trim();
    if (!chosen) { alert("Write your topic first."); return; }
    runDrill(chosen);
  });
  view.querySelectorAll("button[data-cp]").forEach((b) => b.addEventListener("click", () => runSession({ kind: "baseline", checkpoint: b.dataset.cp, topic_id: b.dataset.topic })));
  $("#free-start").addEventListener("click", () => runSession({ kind: "free", topic_text: $("#free-topic").value.trim() || null, planned_seconds: Number($("#free-length").value) }));
  const gnb = $("#go-nb"); if (gnb) gnb.addEventListener("click", () => show("notebook"));
  const rp = $("#retry-pending"); if (rp) rp.addEventListener("click", async () => { rp.disabled = true; await retryPending(); show("today"); });
  const st = $("#start-today"); if (st) st.addEventListener("click", async (e) => { e.preventDefault(); await api("/api/start", { method: "POST", body: {} }); show("today"); });
}

// ---------------------------------------------------------------- recording session
function pickMime() {
  const options = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return options.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
}

function closeOverlay() { document.body.classList.remove("busy"); overlay.hidden = true; overlay.innerHTML = ""; document.body.style.overflow = ""; }
function openOverlay(html) { document.body.classList.add("busy"); overlay.hidden = false; document.body.style.overflow = "hidden"; overlay.innerHTML = `<div class="inner">${html}</div>`; return overlay.firstElementChild; }

// ---------------------------------------------------------------- plan: phrases to aim for in a free talk or drill
// Never used in baseline tests: any help there would change the measurement.
async function planStage(stage, sid, isCancelled) {
  const llm = (STATE && STATE.llm) || {};
  const chosen = new Map();
  stage.innerHTML = `<h3 style="margin-top:0">Phrases for this talk</h3>
    <p class="muted small">Optional. Pick expressions from your notebook to try to use, and add what you expect to need but do not know how to say (any language). After the talk, the app checks which ones you really said.</p>
    <div id="plan-due"></div>
    <div class="card"><div class="row"><input type="text" id="plan-intent" placeholder="I want to be able to say…" maxlength="400" style="flex:1 1 260px"><button class="secondary" id="plan-add">Add</button></div><p class="err small" id="plan-err"></p><div id="plan-new"></div></div>
    <div id="plan-chosen" class="small" style="margin:8px 0"></div>
    <div class="row"><button class="primary" id="plan-go">Continue</button></div>`;
  const drawChosen = () => { $("#plan-chosen", stage).innerHTML = chosen.size ? `<span class="muted">Aiming to use:</span> ${[...chosen.values()].map((c) => `<span class="chip boundary">${esc(c.english)}</span>`).join(" ")}` : ""; };
  try {
    const due = (await api("/api/notebook/suggested-targets?limit=3")).items;
    $("#plan-due", stage).innerHTML = due.length ? `<div class="card"><div class="tag">Due in your notebook</div>${due.map((it) => `<label style="display:block;margin:6px 0"><input type="checkbox" data-id="${it.id}" checked> <strong>${sayable(it.english)}</strong> <span class="muted small">${esc(it.intent)}</span></label>`).join("")}</div>` : "";
    due.forEach((it) => chosen.set(it.id, { id: it.id, english: it.english, source: "due" }));
    stage.querySelectorAll("#plan-due input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => {
      const it = due.find((d) => String(d.id) === cb.dataset.id);
      if (cb.checked) chosen.set(it.id, { id: it.id, english: it.english, source: "due" }); else chosen.delete(it.id);
      drawChosen();
    }));
  } catch (_) {}
  drawChosen();
  const keep = (item, english) => { chosen.set(item.id, { id: item.id, english, source: "prep" }); drawChosen(); };
  $("#plan-add", stage).addEventListener("click", async () => {
    const intent = $("#plan-intent", stage).value.trim();
    const err = $("#plan-err", stage); err.textContent = "";
    if (intent.length < 2) { err.textContent = "Write a few words."; return; }
    const box = $("#plan-new", stage);
    try {
      let item = (await api("/api/notebook", { method: "POST", body: { kind: "gap", source: "prep", intent, session_id: sid } })).item;
      $("#plan-intent", stage).value = "";
      logEvent("plan_added", {}, sid);
      const holder = document.createElement("div"); box.appendChild(holder);
      const showOptions = (it) => {
        const opts = (it.suggestions && it.suggestions.options) || [];
        holder.innerHTML = `<p class="small" style="margin:8px 0 2px"><span class="muted">You want to say:</span> ${esc(it.intent)}</p>${opts.map((o, i) => `<label style="display:block;margin:4px 0"><input type="radio" name="p${it.id}" value="${i}" ${i === 0 ? "checked" : ""}> <strong>${sayable(o.english)}</strong> <span class="muted small">${esc(o.note)}</span></label>`).join("")}<div class="row" style="margin-top:4px">${opts.length ? `<button class="secondary" data-k>Keep selected</button>` : ""}<button class="secondary" data-own>Write my own</button></div>`;
        const k = holder.querySelector("[data-k]");
        if (k) k.addEventListener("click", async () => { const r = holder.querySelector("input:checked"); const english = opts[Number(r.value)].english; await api(`/api/notebook/${it.id}/choose`, { method: "POST", body: { english } }); keep(it, english); holder.innerHTML = ""; });
        holder.querySelector("[data-own]").addEventListener("click", async () => { const v = prompt("The English version you want to use:", ""); if (v && v.trim()) { await api(`/api/notebook/${it.id}/choose`, { method: "POST", body: { english: v.trim() } }); keep(it, v.trim()); holder.innerHTML = ""; } });
      };
      if (llm.configured) { holder.innerHTML = `<p class="muted small">Looking for natural ways to say it…</p>`; try { item = (await api(`/api/notebook/${item.id}/suggest`, { method: "POST", body: {} })).item; } catch (e) { err.textContent = e.message; } }
      showOptions(item);
    } catch (e) { err.textContent = e.message; }
  });
  let done = false;
  $("#plan-go", stage).addEventListener("click", () => { done = true; });
  while (!done && !isCancelled()) await sleep(150);
  return [...chosen.values()];
}

async function runSession(params, opts = {}) {
  let session;
  try {
    session = await api("/api/sessions", { method: "POST", body: { ...params, client_info: { ua: navigator.userAgent, tz: Intl.DateTimeFormat().resolvedOptions().timeZone, screen: `${screen.width}x${screen.height}` } } });
  } catch (e) { alert(`Could not create the session: ${e.message}`); return; }
  const sid = session.id;
  logEvent("session_screen_opened", { kind: params.kind }, sid);
  const box = openOverlay(`<button class="secondary" id="cancel">Cancel</button><p class="tag" style="margin-top:24px">${esc(opts.label || (params.kind === "baseline" ? "Test" : "Free monologue"))}</p><div class="big-topic">${esc(session.topic_text || "Talk about anything you like.")}</div>${opts.hint ? `<p class="muted">${esc(opts.hint)}</p>` : ""}<div id="stage"></div>`);
  const stage = $("#stage", box);
  let cancelled = false, recorder = null, stream = null;
  $("#cancel", box).addEventListener("click", () => {
    cancelled = true; logEvent("session_cancelled", {}, sid);
    try { if (recorder && recorder.state !== "inactive") recorder.stop(); } catch (_) {}
    if (stream) stream.getTracks().forEach((t) => t.stop());
    closeOverlay(); show(currentTab);
  });

  // 1. microphone first, so a refusal is known before the timer starts
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    logEvent("mic_granted", { tracks: stream.getAudioTracks().map((t) => t.label) }, sid);
  } catch (e) {
    logEvent("mic_error", { name: e.name, message: e.message }, sid);
    stage.innerHTML = `<p class="err">The microphone is not available (${esc(e.name)}). Allow microphone access for this page and try again.</p>`;
    return;
  }
  if (cancelled) return;
  const mime = pickMime();
  if (!window.MediaRecorder) { stage.innerHTML = `<p class="err">This browser cannot record audio.</p>`; return; }

  // 1b. optional plan of phrases to aim for (never in a baseline test)
  let plan = opts.plan || (params.kind !== "baseline" ? { items: null } : null);
  if (plan && plan.items === null) { plan.items = await planStage(stage, sid, () => cancelled); }
  if (cancelled) return;
  const aims = plan && plan.items ? plan.items : [];
  if (aims.length) {
    for (const src of ["due", "prep"]) {
      const ids = aims.filter((a) => a.source === src).map((a) => a.id);
      if (ids.length) { try { await api(`/api/sessions/${sid}/targets`, { method: "POST", body: { item_ids: ids, source: src } }); } catch (e) { logEvent("targets_failed", { error: String(e) }, sid); } }
    }
  }
  const aimLine = aims.length ? `<p class="small muted" id="aim">Aim to use: ${aims.map((a) => `<strong>${sayable(a.english)}</strong>`).join(" · ")}</p>` : "";

  // 2. preparation
  if (session.prep_seconds > 0) {
    logEvent("prep_started", { seconds: session.prep_seconds }, sid);
    let left = session.prep_seconds, ready = false;
    stage.innerHTML = `<p class="muted">${esc(opts.prepText || "Preparation. Think, do not write.")}</p><div class="clock" id="clock">${mmss(left)}</div>${aimLine}<button class="primary" id="ready">I'm ready</button>`;
    $("#ready", stage).addEventListener("click", () => { ready = true; });
    while (left > 0 && !ready && !cancelled) { await sleep(1000); left -= 1; const c = $("#clock", stage); if (c) c.textContent = mmss(Math.max(left, 0)); }
    if (cancelled) return;
    logEvent("prep_ended", { early: ready, seconds_left: Math.max(left, 0) }, sid);
  }

  // 3. recording
  const chunks = [];
  try { recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined); }
  catch (e) { logEvent("recorder_error", { message: String(e) }, sid); stage.innerHTML = `<p class="err">Could not start the recorder: ${esc(e.message)}</p>`; return; }
  recorder.ondataavailable = (ev) => { if (ev.data && ev.data.size) chunks.push(ev.data); };
  recorder.onerror = (ev) => logEvent("recorder_error", { message: String(ev.error || ev) }, sid);
  const planned = session.planned_seconds;
  const t0 = performance.now();
  recorder.start(1000);
  logEvent("record_started", { mime: recorder.mimeType, planned }, sid);
  stage.innerHTML = `<p><span class="rec-dot"></span>Recording. Keep going until the timer ends.</p><div class="clock" id="clock">${mmss(0)}</div><p class="wrapup" id="wrapup" hidden>20 seconds left. Finish your thought and conclude.</p>${aimLine}<button class="secondary" id="stop">Stop early</button>`;
  let stopped = false;
  let cueShown = false;
  $("#stop", stage).addEventListener("click", () => { stopped = true; });
  while (!stopped && !cancelled && (performance.now() - t0) / 1000 < planned) {
    await sleep(250);
    const elapsed = (performance.now() - t0) / 1000;
    const c = $("#clock", stage); if (c) c.textContent = mmss(elapsed);
    if (!cueShown && planned > 40 && planned - elapsed <= 20) {
      // visual cue only: the recording length is unchanged, so measurements stay comparable
      cueShown = true;
      const w = $("#wrapup", stage); if (w) w.hidden = false;
      logEvent("wrap_up_cue", { elapsed_s: Number(elapsed.toFixed(1)) }, sid);
    }
  }
  if (cancelled) return;
  const durationS = (performance.now() - t0) / 1000;
  await new Promise((resolve) => { recorder.onstop = resolve; recorder.stop(); });
  stream.getTracks().forEach((t) => t.stop());
  const blob = new Blob(chunks, { type: recorder.mimeType || mime || "audio/webm" });
  logEvent("record_stopped", { duration_s: Number(durationS.toFixed(2)), early: stopped, bytes: blob.size }, sid);
  if (!blob.size) { stage.innerHTML = `<p class="err">The recording is empty. Check your microphone and try again.</p>`; return; }

  // 4. keep a local copy, then upload
  const item = { sessionId: sid, blob, mime: (recorder.mimeType || mime || "audio/webm").split(";")[0], durationS };
  const kept = await pendingPut(item);
  stage.innerHTML = `<p class="muted">Saving…</p>`;
  let uploaded = false;
  for (let attempt = 1; attempt <= 3 && !uploaded; attempt++) {
    try { await uploadAudio(item); uploaded = true; await pendingDelete(sid); logEvent("upload_ok", { attempt }, sid); }
    catch (e) { logEvent("upload_failed", { attempt, error: String(e) }, sid); await sleep(1500 * attempt); }
  }
  if (!uploaded) {
    stage.innerHTML = `<p class="err">The upload failed. ${kept ? "Your recording is saved on this device and will be uploaded next time you open the app." : "Download it now so it is not lost."}</p><div class="row"><button class="primary" id="again">Try again</button><a class="secondary" id="dl" href="${URL.createObjectURL(blob)}" download="recording-${sid}.webm">Download recording</a></div>`;
    $("#again", stage).addEventListener("click", async () => { try { await uploadAudio(item); await pendingDelete(sid); showResult(box, sid); } catch (e) { alert(e.message); } });
    return null;
  }
  if (opts.drill) return sid; // a drill moves straight on to its next round; transcription runs in the background
  showResult(box, sid);
  return sid;
}

// ---------------------------------------------------------------- 4/3/2 drill
function newGroupId() {
  const b = new Uint8Array(16); crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

async function runDrill(topicText) {
  const gid = newGroupId();
  const secs = STATE.drill_seconds || [240, 180, 120];
  logEvent("drill_started", { group_id: gid }, null);
  const plan = { items: null };
  for (let round = 1; round <= 3; round++) {
    const mins = minutes(secs[round - 1]);
    const sid = await runSession(
      { kind: "drill", group_id: gid, drill_round: round, topic_text: round === 1 ? topicText : undefined },
      {
        drill: true, plan,
        label: `4/3/2 · round ${round} of 3 · ${mins} minutes`,
        hint: round === 1 ? "Tell the story in full, to someone who does not know it. You will tell it again, shorter, twice." : `Tell the same story again in ${mins} minutes. Imagine a new listener and use your own words, not the ones you remember. Same content, tighter and faster. Do not add new material.`,
        prepText: round === 1 ? "Preparation. Think, do not write." : "Short breath. Same story, less time.",
      });
    if (!sid) { logEvent("drill_stopped", { group_id: gid, at_round: round }, null); return; }
  }
  logEvent("drill_recorded", { group_id: gid }, null);
  await showDrillSummary(gid);
}

const minutes = (sec) => Number((sec / 60).toFixed(1));
const DRILL_ROWS = [
  ["words_per_minute", "Words per minute", 0],
  ["mean_length_of_run_words", "Words between pauses", 1],
  ["pauses_per_100_words", "Pauses per 100 words", 1],
  ["mid_clause_pauses_per_100_words", "Mid-clause pauses per 100 words", 1],
  ["mean_pause_ms", "Mean pause (ms)", 0],
  ["fillers_per_100_words", "Fillers per 100 words", 1],
  ["repetitions_per_100_words", "Repetitions per 100 words", 1],
  ["content_words", "Words (without fillers)", 0],
];

function changeCell(c) {
  if (!c) return '<span class="v-none">n/a</span>';
  const good = c.better === "info" ? null : (c.delta > 0) === (c.better === "higher");
  const cls = c.better === "info" || Math.abs(c.delta) < 1e-9 ? "v-none" : good ? "v-improved" : "v-worse";
  return `<span class="${cls}">${c.delta > 0 ? "+" : ""}${fmt(c.delta, 1)}</span>`;
}

function drillTable(g) {
  const cols = g.rounds.map((r) => `<th class="num">Round ${r.round}<div class="tag">${minutes(r.planned_seconds)} min</div></th>`).join("");
  const rows = DRILL_ROWS.map(([k, label, d]) => `<tr><td>${esc(label)}</td>${g.rounds.map((r) => `<td class="num">${r.status === "done" ? fmt(r.metrics[k], d) : esc(r.status)}</td>`).join("")}<td class="num">${changeCell(g.change[k])}</td></tr>`).join("");
  return `<div class="card scroll"><table><thead><tr><th></th>${cols}<th class="num">Change</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

const DRILL_NOTE = "Telling the same story again is easier each time, so some of the gain is rehearsal. What matters is the shape of it: fewer mid-clause pauses and restarts at the same or higher speed. This is not a measure of progress on new topics; the checkpoint tests are for that.";

async function showDrillSummary(gid) {
  const box = openOverlay(`<p class="tag">4/3/2 · result</p><div id="stage"><p class="muted">Transcribing the three rounds… this takes a few minutes.</p></div>`);
  const stage = $("#stage", box);
  const finish = () => { closeOverlay(); show("today"); };
  let g = null;
  for (let i = 0; i < 300; i++) {
    g = await api(`/api/drills/${gid}`, {}).catch(() => null);
    if (g && g.rounds.length === 3 && g.rounds.every((r) => r.status === "done" || r.status === "error")) break;
    await sleep(3000);
  }
  if (!g) { stage.innerHTML = `<p class="err">Lost contact with the server. Your recordings are safe. Open History later.</p><button class="primary" id="done">Close</button>`; $("#done", stage).addEventListener("click", finish); return; }
  const failed = g.rounds.filter((r) => r.status === "error");
  stage.innerHTML = `<h2>4/3/2 result</h2><p class="muted small">${esc(g.topic_text)}</p>${failed.length ? `<div class="banner" style="margin:10px 0;border-radius:8px">${failed.length} round(s) could not be transcribed. The audio is kept: open it in History and retry.</div>` : ""}${drillTable(g)}<p class="muted small">${esc(DRILL_NOTE)}</p><button class="primary" id="done">Done</button>`;
  $("#done", stage).addEventListener("click", finish);
  if (!failed.length) confetti();
}

// ---------------------------------------------------------------- result / session detail
async function showResult(box, sid) {
  const stage = $("#stage", box) || box;
  stage.innerHTML = `<p class="muted" id="wait">Transcribing… this usually takes less than a minute.</p>`;
  let s = null;
  for (let i = 0; i < 300; i++) {
    s = await api(`/api/sessions/${sid}`, {}).catch(() => null);
    if (s && (s.status === "done" || s.status === "error")) break;
    await sleep(2000);
  }
  if (!s) { stage.innerHTML = `<p class="err">Lost contact with the server. Your recording is safe. Open History later.</p><button class="primary" id="done">Close</button>`; $("#done", stage).addEventListener("click", () => { closeOverlay(); show("today"); }); return; }
  if (s.status === "error") {
    stage.innerHTML = `<p class="err">Transcription failed: ${esc(s.error)}</p><p class="muted small">The audio is kept. Fix the cause (for example the API key in .env), then open this session in History and retry.</p><button class="primary" id="done">Close</button>`;
    $("#done", stage).addEventListener("click", () => { closeOverlay(); show("today"); });
    return;
  }
  const detail = await api(`/api/sessions/${sid}?detail=true`);
  stage.innerHTML = "";
  renderSessionDetail(stage, detail);
  const closeBtn = document.createElement("button");
  closeBtn.className = "primary"; closeBtn.textContent = "Done"; closeBtn.style.marginTop = "18px";
  closeBtn.addEventListener("click", () => { closeOverlay(); show("today"); });
  stage.appendChild(closeBtn);
  confetti();
}

function renderTranscript(detail) {
  const words = detail.words || [], pauses = detail.pauses || [];
  const byIndex = new Map(pauses.map((p) => [p.before_word_index, p]));
  const fillers = new Set(["um", "umm", "uh", "uhh", "er", "erm", "uhm", "ehm", "hmm", "hm", "mm", "mmm"]);
  let out = "";
  words.forEach((w, i) => {
    const p = byIndex.get(i);
    if (p) out += `<span class="chip ${p.long ? "long" : p.type === "mid_clause" ? "mid" : "boundary"}" title="${esc(p.type)} pause">${(p.duration_ms / 1000).toFixed(1)}s</span>`;
    const isFiller = fillers.has(String(w.text).toLowerCase().replace(/[^a-z']/g, ""));
    out += `<span class="w${isFiller ? " filler" : ""}" data-i="${i}">${esc(w.text)}</span> `;
  });
  return out || '<span class="muted">No transcript.</span>';
}

function renderSessionDetail(container, detail) {
  const rows = METRIC_ORDER.filter(([k]) => detail.metrics && detail.metrics[k] !== undefined)
    .map(([k, label, d]) => `<tr><td>${esc(label)}</td><td class="num">${fmt(detail.metrics[k], d)}</td></tr>`).join("");
  const flags = (detail.flags || []).map((f) => ({ short_sample: "Short sample: treat the numbers with care.", no_fillers_found: "No fillers found. The provider may have removed them, which would understate your hesitations.", too_short: "Too little speech to compute metrics." }[f] || f));
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <h2>Result</h2>
    <p class="muted small">${esc(detail.topic_text || "Free monologue")} · ${esc(detail.provider || "no provider")}${detail.provider === "mock" ? " (invented transcript)" : ""} · ${fmt(detail.client_seconds, 0)} s recorded</p>
    ${flags.length ? `<div class="banner" style="margin:10px 0;border-radius:8px">${flags.map(esc).join("<br>")}</div>` : ""}
    <div class="card scroll"><table><tbody>${rows || '<tr><td class="muted">No metrics.</td></tr>'}</tbody></table></div>
    <h3>Transcript</h3>
    <div class="legend">Pause chips: <span class="chip mid">mid-clause</span> <span class="chip boundary">clause boundary</span> <span class="chip long">over 1 s</span> · <span class="filler">fillers</span>. Classification is a heuristic.</div>
    <div class="card transcript" id="tr">${renderTranscript(detail)}</div>
    <div id="uses"></div>
    <div id="gaps"></div>
    ${detail.has_audio ? `<audio id="sess-audio" controls preload="none" src="/api/sessions/${esc(detail.id)}/audio" style="width:100%"></audio>` : ""}
    <div id="pron"></div>
    <div id="coach"></div>
    <h3>How did it feel?</h3>
    <div class="row"><select id="fb-rating"><option value="">Rating…</option>${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${detail.self_rating === n ? "selected" : ""}>${n}${n === 1 ? " (very hard)" : n === 5 ? " (easy)" : ""}</option>`).join("")}</select></div>
    <textarea id="fb-notes" placeholder="Notes: what blocked you, words you could not find, anything you avoided saying">${esc(detail.notes || "")}</textarea>
    <div class="row" style="margin-top:8px"><button class="secondary" id="fb-save">Save notes</button><span id="fb-msg" class="muted small"></span></div>`;
  container.appendChild(wrap);
  setupPron(wrap, detail);
  setupCoach(wrap, detail);
  setupUses(wrap, detail);
  setupGaps(wrap, detail);
  $("#fb-save", wrap).addEventListener("click", async () => {
    const rating = $("#fb-rating", wrap).value;
    try {
      await api(`/api/sessions/${detail.id}/feedback`, { method: "POST", body: { self_rating: rating ? Number(rating) : null, notes: $("#fb-notes", wrap).value || null } });
      $("#fb-msg", wrap).textContent = "Saved.";
    } catch (e) { $("#fb-msg", wrap).textContent = `Not saved: ${e.message}`; }
  });
}

// ---------------------------------------------------------------- blocked moments and the notebook
function itemCard(it, onChange) {
  const el = document.createElement("div");
  el.className = "card";
  const llm = (STATE && STATE.llm) || {};
  const sug = it.suggestions;
  const opts = sug ? sug.options.map((o, i) => `<div class="small" style="margin:6px 0"><label><input type="radio" name="o${it.id}" value="${i}" ${(it.english ? it.english === o.english : i === 0) ? "checked" : ""}> <strong>${sayable(o.english)}</strong></label><div class="muted" style="margin-left:22px">${esc(o.note)}</div></div>`).join("") : "";
  el.innerHTML = `
    <div class="tag">${esc(it.kind)}${it.pause_ms ? ` · stuck ${(it.pause_ms / 1000).toFixed(1)}s` : ""}${it.status === "new" ? " · choose a version" : ""}</div>
    <p style="margin:4px 0"><span class="muted small">You wanted to say:</span> ${esc(it.intent)}</p>
    ${it.context ? `<p class="small muted" style="margin:0 0 6px">Around: “${esc(it.context)}”</p>` : ""}
    ${it.english ? `<p style="margin:4px 0"><span class="muted small">Keep:</span> <strong>${sayable(it.english)}</strong></p>` : ""}
    ${opts}${sug && sug.tip ? `<p class="small muted">${esc(sug.tip)}</p>` : ""}
    <div class="row" style="margin-top:6px"></div><p class="err small" data-err></p>`;
  const row = $(".row", el);
  const err = $("[data-err]", el);
  const call = async (path, body) => {
    err.textContent = "";
    try { const r = await api(path, { method: "POST", body: body || {} }); onChange(r.item); }
    catch (e) { err.textContent = e.message; }
  };
  const btn = (label, cls, fn) => { const b = document.createElement("button"); b.className = cls; b.textContent = label; b.addEventListener("click", async () => { b.disabled = true; await fn(); b.disabled = false; }); row.appendChild(b); return b; };
  if (llm.configured) btn(sug ? "Suggest again (uses credits)" : "Suggest English", sug ? "secondary" : "primary", () => call(`/api/notebook/${it.id}/suggest`));
  if (sug) btn("Keep selected", "primary", async () => {
    const r = el.querySelector(`input[name=o${it.id}]:checked`);
    if (!r) { err.textContent = "Pick one version first."; return; }
    await call(`/api/notebook/${it.id}/choose`, { english: sug.options[Number(r.value)].english });
  });
  btn("Write my own", "secondary", async () => {
    const v = prompt("The English version you want to keep:", it.english || "");
    if (v && v.trim()) await call(`/api/notebook/${it.id}/choose`, { english: v.trim() });
  });
  btn("Discard", "secondary", async () => { await call(`/api/notebook/${it.id}/archive`); });
  return el;
}

async function setupUses(root, detail) {
  const box = $("#uses", root);
  if (!box || detail.status !== "done") return;
  let uses = [];
  try { uses = (await api(`/api/sessions/${detail.id}/uses`, {})).uses; } catch (_) {}
  if (!uses.length) return;
  const said = uses.filter((u) => u.used).length;
  box.innerHTML = `<h3>Phrases you aimed for</h3><p class="muted small">${said} of ${uses.length} found in the transcript. The check looks for the words in order and ignores fillers, so a different wording of the same idea is not counted.</p>` +
    uses.map((u) => `<div class="card"><div class="tag">${u.used ? "said" : "not said"}</div> <strong>${sayable(u.english)}</strong>${u.used ? `<p class="small muted" style="margin:4px 0">Heard: “${esc(u.matched)}”</p>` : ""}${u.used ? `<div class="row"><button class="secondary" data-fluent="${u.item_id}">Counts as a fluent review</button></div>` : ""}</div>`).join("");
  box.querySelectorAll("[data-fluent]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await api(`/api/notebook/${b.dataset.fluent}/review`, { method: "POST", body: { result: "fluent" } }); b.textContent = "Recorded"; logEvent("use_counted_fluent", { item: Number(b.dataset.fluent) }, detail.id); }
    catch (e) { b.textContent = "Not recorded"; }
  }));
}

async function setupGaps(root, detail) {
  const box = $("#gaps", root);
  if (!box || detail.status !== "done") return;
  box.innerHTML = `<h3>Where you got stuck</h3><p class="muted small">The longest silences in the middle of a sentence are listed below: they often mark a word or phrase you were looking for. Click one, or click any word in the transcript, then write what you wanted to say (any language). You can also ask for a guess from the words around it.</p><div id="stuck"></div><div id="gap-form"></div><div id="gap-items"></div>`;
  const items = $("#gap-items", box);
  const draw = (list) => { items.innerHTML = ""; list.filter((i) => i.status !== "archived").forEach((it) => items.appendChild(itemCard(it, () => load()))); };
  const load = async () => { try { draw((await api(`/api/notebook?session_id=${detail.id}`)).items); } catch (_) {} };
  await load();

  const words = detail.words || [];
  const around = (i) => `${words.slice(Math.max(0, i - 4), i).map((w) => w.text).join(" ")} … ${words.slice(i, i + 3).map((w) => w.text).join(" ")}`;
  const stuck = (detail.pauses || []).filter((p) => p.type === "mid_clause" && p.duration_ms >= 700).sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 6).sort((a, b) => a.before_word_index - b.before_word_index);
  $("#stuck", box).innerHTML = stuck.length
    ? stuck.map((p) => `<button class="secondary stuck-pt" data-i="${p.before_word_index}" style="margin:0 6px 6px 0;text-align:left"><span class="chip long">${(p.duration_ms / 1000).toFixed(1)}s</span> ${esc(around(p.before_word_index))}</button>`).join("")
    : `<p class="muted small">No long mid-sentence silence in this session.</p>`;

  const selectWord = (idx) => {
    const tr = $("#tr", root);
    tr.querySelectorAll(".w.sel").forEach((x) => x.classList.remove("sel"));
    const w = tr.querySelector(`.w[data-i="${idx}"]`);
    if (w) w.classList.add("sel");
    const form = $("#gap-form", box);
    form.innerHTML = `<div class="card"><p class="small" style="margin:0 0 6px">Stuck at: <strong>${esc(around(idx))}</strong></p><input type="text" id="gap-intent" placeholder="What did you want to say?" style="width:100%" maxlength="400"><div class="row" style="margin-top:8px"><button class="primary" id="gap-save">Add to notebook</button><button class="secondary" id="gap-guess" title="The model guesses from the words around the silence">I do not remember, guess</button><span class="err small" id="gap-err"></span></div></div>`;
    $("#gap-intent", form).focus();
    const save = async (intent, guessed) => {
      if (intent.length < 2) { $("#gap-err", form).textContent = "Write a few words."; return; }
      try {
        const r = await api(`/api/sessions/${detail.id}/gaps`, { method: "POST", body: { intent, word_index: idx } });
        logEvent("gap_added", { word_index: idx, guessed }, detail.id);
        form.innerHTML = ""; if (w) w.classList.remove("sel");
        const llm = (STATE && STATE.llm) || {};
        if (llm.configured) { try { await api(`/api/notebook/${r.item.id}/suggest`, { method: "POST", body: {} }); } catch (_) {} }
        await load();
      } catch (e) { $("#gap-err", form).textContent = e.message; }
    };
    $("#gap-save", form).addEventListener("click", () => save($("#gap-intent", form).value.trim(), false));
    $("#gap-guess", form).addEventListener("click", () => save("Not sure what I wanted to say. Infer it from the words around the silence.", true));
  };
  $("#tr", root).addEventListener("click", (e) => { const w = e.target.closest(".w"); if (w) selectWord(Number(w.dataset.i)); });
  box.querySelectorAll(".stuck-pt").forEach((b) => b.addEventListener("click", () => selectWord(Number(b.dataset.i))));
}

function renderPatterns(box, p) {
  if (!p.patterns.length) { box.innerHTML = ""; return; }
  const f = p.focus;
  const ex = (t) => t.examples.slice(-1).map((e) => `<div class="small" style="margin:4px 0 0 12px"><span class="muted">You said:</span> “${esc(e.said)}” <span class="muted">Better:</span> “${sayable(e.better)}”</div>`).join("");
  box.innerHTML = `<h3 style="margin-top:0">What keeps coming back</h3>
    ${f ? `<div class="card due"><div class="tag">Focus for now</div><p style="margin:4px 0"><strong>${esc(f.tag)}</strong>: ${esc(f.issue)}</p><p class="small muted" style="margin:0">Raised in ${f.recent_sessions} of your last sessions.</p>${ex(f)}</div>` : `<p class="muted small">No pattern has come up in two recent sessions yet.</p>`}
    <div class="card scroll"><table><thead><tr><th>Pattern</th><th class="num">Sessions</th><th>Last seen</th></tr></thead><tbody>${p.patterns.slice(0, 8).map((t) => `<tr><td><strong>${esc(t.tag)}</strong><div class="small muted">${esc(t.issue)}</div></td><td class="num">${t.sessions}</td><td>${esc(t.last_seen)}</td></tr>`).join("")}</tbody></table></div>
    <p class="small muted">Counted from the model's feedback, so two tags can describe the same idea. A guide, not a measurement.</p>`;
}

async function renderNotebook() {
  await refreshState();
  const [dueR, allR] = await Promise.all([api("/api/notebook?due=true"), api("/api/notebook")]);
  const llm = (STATE && STATE.llm) || {};
  view.innerHTML = `<h2>Notebook</h2>
    <p class="lead">Expressions you could not find while speaking. They come back on a widening schedule until you can say them without stopping.</p>
    <div id="nb-patterns"></div>
    <div id="nb-due"></div>
    <div class="card"><h3>Add one yourself</h3><div class="row"><input type="text" id="nb-intent" placeholder="What do you want to be able to say?" size="50" maxlength="400"><input type="text" id="nb-english" placeholder="English version (optional)" size="40" maxlength="300"><button class="primary" id="nb-add">Add</button></div><p class="err small" id="nb-err"></p></div>
    <h3>All items</h3><div id="nb-all"></div>`;
  try { renderPatterns($("#nb-patterns"), await api("/api/patterns")); } catch (_) {}
  const dueBox = $("#nb-due");
  const reviewable = dueR.items;
  if (!reviewable.length) dueBox.innerHTML = `<div class="card"><strong>Nothing due.</strong> <span class="muted small">Items appear here when their review date comes.</span></div>`;
  else {
    let i = 0;
    const next = () => {
      if (i >= reviewable.length) { dueBox.innerHTML = `<div class="card"><strong>Review done.</strong> Nice work.</div>`; confetti(); return; }
      const it = reviewable[i];
      dueBox.innerHTML = `<div class="card due"><div class="row spread"><div class="tag">Review ${i + 1} of ${reviewable.length}</div></div>
        <p class="small muted" style="margin:6px 0 0">Say this out loud in English, in a full sentence if you can:</p>
        <p style="font-size:18px;margin:6px 0">${esc(it.intent)}</p>
        ${it.context ? `<p class="small muted">Around: “${esc(it.context)}”</p>` : ""}
        <div id="rv-answer"><button class="primary" id="rv-show">Show the English</button></div></div>`;
      $("#rv-show").addEventListener("click", () => {
        $("#rv-answer").innerHTML = `<p style="font-size:18px;margin:6px 0"><strong>${sayable(it.english)}</strong></p><p class="small muted">Be honest. How did it go?</p><div class="row"><button class="primary" data-r="fluent">Said it without stopping</button><button class="secondary" data-r="hesitated">Hesitated</button><button class="secondary" data-r="failed">Could not say it</button></div>`;
        dueBox.querySelectorAll("button[data-r]").forEach((b) => b.addEventListener("click", async () => {
          await api(`/api/notebook/${it.id}/review`, { method: "POST", body: { result: b.dataset.r } });
          logEvent("notebook_review", { item: it.id, result: b.dataset.r });
          i += 1; next();
        }));
      });
    };
    next();
  }
  const allBox = $("#nb-all");
  const drawAll = (list) => {
    allBox.innerHTML = "";
    if (!list.length) { allBox.innerHTML = `<p class="muted small">Empty. Mark where you got stuck after a session, or add items above.</p>`; return; }
    list.forEach((it) => {
      const el = it.status === "new" ? itemCard(it, () => renderNotebook()) : (() => {
        const d = document.createElement("div"); d.className = "card";
        d.innerHTML = `<div class="tag">${esc(it.status)} · ${it.reviews} review(s)${it.due_at ? ` · next ${esc(it.due_at.slice(0, 10))}` : ""}</div><p style="margin:4px 0"><span class="muted small">${esc(it.intent)}</span><br><strong>${sayable(it.english)}</strong></p>`;
        return d;
      })();
      allBox.appendChild(el);
    });
  };
  drawAll(allR.items);
  $("#nb-add").addEventListener("click", async () => {
    const intent = $("#nb-intent").value.trim();
    if (intent.length < 2) { $("#nb-err").textContent = "Write what you want to say."; return; }
    try { await api("/api/notebook", { method: "POST", body: { kind: "phrase", intent, english: $("#nb-english").value.trim() || null } }); renderNotebook(); }
    catch (e) { $("#nb-err").textContent = e.message; }
  });
}

// ---------------------------------------------------------------- pronunciation scores from the audio
const SCORE_LABELS = [["AccuracyScore", "Sounds"], ["FluencyScore", "Flow"], ["ProsodyScore", "Intonation"]];
const scoreClass = (v) => (v >= 80 ? "ok" : v >= 65 ? "mid" : "bad");

function renderPron(box, r, detail) {
  const sm = r.summary;
  const tiles = SCORE_LABELS.filter(([k]) => sm.scores[k] != null).map(([k, label]) => `<div class="chip ${scoreClass(sm.scores[k])}" style="margin-right:6px"><strong>${Math.round(sm.scores[k])}</strong> ${label}</div>`).join("");
  const sounds = (ph) => ph.map((p) => `<span class="chip ${scoreClass(p.s)}" style="padding:0 6px;margin-right:3px">${esc(p.p)} ${Math.round(p.s)}</span>`).join("");
  const words = sm.worst_words.map((w, i) => `<div class="small" style="margin:6px 0"><strong>${sayable(w.word)}</strong> <span class="muted">${Math.round(w.score)}${w.times > 1 ? ` · weak ${w.times} times` : ""}</span>
      <span class="hear"><button class="secondary mini" data-say="${esc(w.word)}" title="Hear the app say it">Model</button><button class="secondary mini" data-clip="/api/sessions/${esc(detail.id)}/clip?unit=${encodeURIComponent(w.unit)}&t=${w.t}&d=${w.d || 1}" title="Hear yourself say it">You</button></span><br>${sounds(w.sounds)}</div>`).join("");
  const weak = sm.weak_sounds.map((x) => `<li class="small"><strong>/${esc(x.sound)}/</strong> <span class="muted">average ${Math.round(x.mean)} in ${x.count} words${x.words.length ? `, for example ${x.words.map((w) => sayable(w)).join(", ")}` : ""}</span></li>`).join("");
  const earClips = r.ear ? r.ear.clips.map((c) => `<div class="small" style="margin:6px 0"><strong>${sayable(c.word)}</strong> <span class="muted">sounds like</span> ${esc(c.heard)}<br>${sayQuoted(c.issue)}${c.fix ? `<br><span class="muted">Try:</span> ${sayQuoted(c.fix)}` : ""}</div>`).join("") : "";
  const ear = r.ear ? `<p class="small muted" style="margin:10px 0 0">What an audio model heard when it listened to those words (${esc(r.ear.model)}):</p>${earClips}${r.ear.overall ? `<p class="small">${esc(r.ear.overall)}</p>` : ""}<p class="small muted">An AI model listening is less reliable than the scores above and can be wrong about a word. Trust a tip only if you can hear it yourself: click a word to hear the app say it, and “You” to hear yourself.</p>`
    : r.ear_pending ? `<p class="small muted" style="margin:10px 0 0">An audio model is listening to the weakest words…</p>` : "";
  box.innerHTML = `<h3>Pronunciation</h3>
    <div style="margin:6px 0">${tiles}</div>
    ${sm.worst_words.length ? `<p class="small muted" style="margin:8px 0 0">Words to work on (score out of 100, then each sound):</p>${words}` : `<p class="small">No word scored low. Nothing stands out in the sounds.</p>`}
    ${weak ? `<p class="small muted" style="margin:8px 0 0">Sounds that were weak most often:</p><ul>${weak}</ul>` : ""}
    ${ear}
    <p class="small muted">Scored from the audio by Azure Speech, not from the transcript, so it hears what you really said. It judges against a standard accent: a different accent is not an error, so treat the scores as a guide. ${sm.n_words} words, ${Math.round(sm.seconds)} s.</p>
    <button class="secondary" id="pron-again" style="margin-top:4px">Score again</button>`;
  if (r.ear_pending) setTimeout(() => { if (document.body.contains(box)) setupPron(box.parentElement, detail); }, 2500);
  $("#pron-again", box).addEventListener("click", async () => {
    try { await api(`/api/sessions/${detail.id}/pronunciation`, { method: "POST" }); } catch (_) {}
    setupPron(box.parentElement, detail);
  });
}

async function setupPron(root, detail) {
  const box = $("#pron", root);
  if (!box || detail.status !== "done") return;
  let r;
  try { r = await api(`/api/sessions/${detail.id}/pronunciation`, {}); } catch (_) { return; }
  if (r.state === "off") {
    box.innerHTML = `<h3>Pronunciation</h3><p class="small muted">Not set up. A transcript often corrects what you said, so it cannot show pronunciation. Add AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to .env to score it from the audio.</p>`;
  } else if (r.state === "pending") {
    box.innerHTML = `<h3>Pronunciation</h3><p class="small muted">Scoring the sounds in your audio…</p>`;
    setTimeout(() => { if (document.body.contains(box)) setupPron(root, detail); }, 2500);
  } else if (r.state === "done") {
    renderPron(box, r, detail);
  } else if (r.state === "error") {
    box.innerHTML = `<h3>Pronunciation</h3><p class="err small">Could not score it: ${esc(r.error || "unknown error")}</p><button class="secondary" id="pron-go">Try again</button>`;
    $("#pron-go", box).addEventListener("click", async () => { try { await api(`/api/sessions/${detail.id}/pronunciation`, { method: "POST" }); } catch (_) {} setupPron(root, detail); });
  } else if (r.state === "none") {
    box.innerHTML = `<h3>Pronunciation</h3><button class="secondary" id="pron-go">Score my pronunciation</button>`;
    $("#pron-go", box).addEventListener("click", async () => { try { await api(`/api/sessions/${detail.id}/pronunciation`, { method: "POST" }); } catch (_) {} setupPron(root, detail); });
  }
}

// ---------------------------------------------------------------- feedback from a language model
function renderCoaching(body, c) {
  const k = c.content;
  const examples = (p) => p.examples.map((e) => `<div class="small" style="margin:4px 0 4px 12px"><span class="muted">You said:</span> “${esc(e.said)}”${e.quote_found ? "" : ' <span class="chip mid" title="The model quoted words that are not in the transcript">not found in transcript</span>'}<br><span class="muted">Better:</span> “${esc(e.better)}”</div>`).join("");
  body.innerHTML = `
    <p>${esc(k.summary)}</p>
    ${k.strengths.length ? `<p class="small"><strong>What worked.</strong> ${k.strengths.map(esc).join(" ")}</p>` : ""}
    ${k.patterns.map((p) => `<div class="card"><div class="tag">${esc(p.tag)}</div><p style="margin:4px 0">${esc(p.issue)}</p>${p.why ? `<p class="small muted" style="margin:0 0 4px">${esc(p.why)}</p>` : ""}${examples(p)}</div>`).join("")}
    ${k.phrases.length ? `<h3 style="margin-top:14px">Phrases to reuse</h3><ul>${k.phrases.map((ph, i) => `<li class="small"><strong>${sayable(ph.phrase)}</strong>${ph.use ? ` <span class="muted">${esc(ph.use)}</span>` : ""} <button class="secondary keep" data-i="${i}" style="padding:0 8px">Keep</button></li>`).join("")}</ul>` : ""}
    ${k.focus_next ? `<div class="card due"><div class="tag">Next time, one thing</div><p style="margin:4px 0 0">${esc(k.focus_next)}</p></div>` : ""}
    <p class="small muted">From ${esc(c.provider)} (${esc(c.model)}). A language model reads an automatic transcript, which tends to correct what you said. It can miss pronunciation and grammar slips that only the audio shows, so “no mistake found” does not mean there was none${c.provider === "mock" ? ". This one is a placeholder." : "."}</p>`;
}

function wireKeep(body, c, sessionId) {
  body.querySelectorAll("button.keep").forEach((b) => b.addEventListener("click", async () => {
    const ph = c.content.phrases[Number(b.dataset.i)];
    try {
      await api("/api/notebook", { method: "POST", body: { kind: "phrase", source: "coach", session_id: sessionId, intent: ph.use || ph.phrase, english: ph.phrase } });
      b.textContent = "Kept"; b.disabled = true;
    } catch (e) { b.textContent = "Not saved"; }
  }));
}

async function setupCoach(root, detail) {
  const box = $("#coach", root);
  if (!box) return;
  box.innerHTML = `<h3>Feedback</h3>`;
  const body = document.createElement("div");
  box.appendChild(body);
  if (!STATE || !STATE.llm) { try { await refreshState(); } catch (_) {} }
  const llm = (STATE && STATE.llm) || {};
  let existing = null;
  try { existing = (await api(`/api/sessions/${detail.id}/coach`, {})).coaching; } catch (_) {}
  const request = async (regenerate) => {
    body.innerHTML = `<p class="muted small">Analysing the transcript…</p>`;
    try {
      const r = await api(`/api/sessions/${detail.id}/coach${regenerate ? "?regenerate=true" : ""}`, { method: "POST" });
      logEvent("coach_shown", { regenerate }, detail.id);
      show_(r.coaching);
    } catch (e) {
      body.innerHTML = `<p class="err small">Could not get feedback: ${esc(e.message)}</p><button class="secondary" id="coach-retry">Try again</button>`;
      $("#coach-retry", body).addEventListener("click", () => request(regenerate));
    }
  };
  const show_ = (c) => {
    renderCoaching(body, c);
    wireKeep(body, c, detail.id);
    if (llm.configured) {
      const b = document.createElement("button");
      b.className = "secondary"; b.textContent = "Ask again (uses credits)"; b.style.marginTop = "8px";
      b.addEventListener("click", () => request(true));
      body.appendChild(b);
    }
  };
  if (existing) { show_(existing); return; }
  if (!llm.configured) { body.innerHTML = `<p class="muted small">${esc(llm.error || "Feedback is not configured.")}</p>`; return; }
  if (detail.status !== "done") return;
  body.innerHTML = `<p class="muted small">A language model reads your transcript and points out a few patterns worth fixing, plus phrases to reuse. It costs a small amount of your credits each time.</p><button class="primary" id="coach-go">Get feedback</button>`;
  $("#coach-go", body).addEventListener("click", () => request(false));
}

// ---------------------------------------------------------------- history
async function renderHistory() {
  const sessions = await api("/api/sessions");
  if (!sessions.length) { view.innerHTML = `<h2>History</h2><p class="lead">No sessions yet.</p>`; return; }
  const rows = sessions.map((s) => `<tr data-id="${esc(s.id)}" style="cursor:pointer"><td>${esc(s.created_at.slice(0, 16).replace("T", " "))}</td><td>${esc(s.checkpoint || (s.kind === "drill" ? `4/3/2 · round ${s.drill_round}` : "free"))}</td><td>${esc((s.topic_text || "").slice(0, 60))}</td><td><span class="status ${esc(s.status)}">${esc(s.status)}</span>${s.provider === "mock" ? ' <span class="muted small">mock</span>' : ""}</td><td class="num">${fmt(s.metrics.words_per_minute, 0)}</td><td class="num">${fmt(s.metrics.mid_clause_pauses_per_100_words, 1)}</td></tr>`).join("");
  view.innerHTML = `<h2>History</h2><p class="lead">Click a session to review it.</p><div class="card scroll"><table><thead><tr><th>When (UTC)</th><th>Checkpoint</th><th>Topic</th><th>Status</th><th>WPM</th><th>Mid-clause pauses /100</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  view.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => openSession(tr.dataset.id)));
}

async function openSession(sid) {
  const detail = await api(`/api/sessions/${sid}?detail=true`);
  const box = openOverlay(`<button class="secondary" id="close">Close</button><div id="stage" style="margin-top:20px"></div>`);
  $("#close", box).addEventListener("click", () => { closeOverlay(); show(currentTab); });
  const stage = $("#stage", box);
  if (detail.status === "error" || detail.status === "uploaded") {
    stage.innerHTML = `<p class="${detail.status === "error" ? "err" : "muted"}">${esc(detail.error || "Waiting to be transcribed.")}</p><button class="primary" id="retry">Retry transcription</button>`;
    $("#retry", stage).addEventListener("click", async () => { try { await api(`/api/sessions/${sid}/retry`, { method: "POST" }); await showResult(box, sid); } catch (e) { alert(e.message); } });
    return;
  }
  renderSessionDetail(stage, detail);
  const extra = document.createElement("div");
  extra.innerHTML = `<h3>Outside rating</h3><p class="muted small">Ask someone to listen and rate how easy you were to understand, from 1 to 5.</p><div class="row"><input type="text" id="rater" placeholder="Who is rating" size="18"><select id="score">${[1, 2, 3, 4, 5].map((n) => `<option>${n}</option>`).join("")}</select><button class="secondary" id="rate">Add rating</button><span id="rate-msg" class="muted small"></span></div>${(detail.ratings || []).map((r) => `<p class="small">${esc(r.rater)}: ${r.score}/5</p>`).join("")}`;
  stage.appendChild(extra);
  $("#rate", extra).addEventListener("click", async () => {
    const rater = $("#rater", extra).value.trim(); if (!rater) { $("#rate-msg", extra).textContent = "Add a name."; return; }
    try { await api(`/api/sessions/${sid}/ratings`, { method: "POST", body: { rater, score: Number($("#score", extra).value) } }); $("#rate-msg", extra).textContent = "Added."; } catch (e) { $("#rate-msg", extra).textContent = e.message; }
  });
}

// ---------------------------------------------------------------- progress
function verdictCell(v) {
  if (!v) return '<span class="v-none">n/a</span>';
  const cls = { improved: "v-improved", worse: "v-worse", "within noise": "v-noise", changed: "v-changed" }[v.verdict] || "v-none";
  const sign = v.delta > 0 ? "+" : "";
  return `<span class="${cls}">${v.delta === null ? "n/a" : `${sign}${fmt(v.delta, 2)}`} · ${esc(v.verdict)}</span>`;
}

async function renderProgress() {
  const mock = new URLSearchParams(location.search).has("mock");
  const p = await api(`/api/progress${mock ? "?include_mock=true" : ""}`);
  const drills = await api(`/api/drills${mock ? "?include_mock=true" : ""}`);
  const drillHtml = drills.length ? `<h3>4/3/2 drills</h3><p class="muted small">${esc(DRILL_NOTE)}</p>${drills.map((g) => `<p class="small" style="margin-bottom:4px"><strong>${esc(g.local_date)}</strong> · ${esc(g.topic_text)}</p>${drillTable(g)}`).join("")}` : "";
  const cps = p.checkpoints;
  const rows = Object.entries(p.metrics).map(([k, m]) => `<tr><td>${esc(m.label)}<div class="tag">${m.better === "info" ? "context" : `better: ${m.better}`}</div></td>
    <td class="num">${fmt(cps.base0.means[k], 2)}</td><td class="num">${fmt(cps.base2.means[k], 2)}</td><td class="num">${fmt(p.noise[k], 2)}</td>
    <td class="num">${fmt(cps.day15.means[k], 2)}</td><td>${verdictCell(p.comparisons.day15[k])}</td>
    <td class="num">${fmt(cps.day30.means[k], 2)}</td><td>${verdictCell(p.comparisons.day30[k])}</td></tr>`).join("");
  const repeats = p.repeats.length ? `<h3>Repeated topics</h3><div class="card scroll"><table><thead><tr><th>Checkpoint</th><th>Topic</th><th>Metric</th><th>Day 0</th><th>Now</th></tr></thead><tbody>${p.repeats.flatMap((r) => Object.entries(r.metrics).map(([m, v]) => `<tr><td>${esc(r.checkpoint)}</td><td>${esc(r.topic_id)}</td><td>${esc(p.metrics[m].label)}</td><td class="num">${fmt(v.day0, 1)}</td><td class="num">${fmt(v.now, 1)}</td></tr>`)).join("")}</tbody></table></div>` : "";
  view.innerHTML = `<h2>Progress</h2><p class="lead">Averages over three topics per checkpoint. A change only counts when it is larger than the noise column (the gap between your day 0 and day 2 baselines).</p>
    <div class="card scroll"><table><thead><tr><th>Metric</th><th>Day 0</th><th>Day 2</th><th>Noise</th><th>Day 15</th><th>Change vs baseline</th><th>Day 30</th><th>Change vs baseline</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted">Topics recorded: day 0 ${cps.base0.n_topics}/${cps.base0.topics_expected}, day 2 ${cps.base2.n_topics}/${cps.base2.topics_expected}, day 15 ${cps.day15.n_topics}/${cps.day15.topics_expected}, day 30 ${cps.day30.n_topics}/${cps.day30.topics_expected}.${mock ? " Mock sessions included." : ""}</p>
    ${repeats}${drillHtml}<h3>How to read this</h3><ul>${p.notes.map((n) => `<li class="small">${esc(n)}</li>`).join("")}</ul>`;
}

// ---------------------------------------------------------------- data
async function renderData() {
  await refreshState();
  view.innerHTML = `<h2>Data</h2><p class="lead">Everything is stored on this computer, in the data folder next to the app.</p>
    <div class="card"><div class="row"><a class="secondary" href="/api/export" download>Export sessions and metrics (JSON)</a><a class="secondary" href="/api/export?detail=true" download>Detailed export: every word, pause and flag (JSON)</a><a class="secondary" href="/api/events.jsonl" download>Download the full event log</a></div>
    <p class="small muted">Every action is logged: sessions, uploads, transcription attempts and errors, recording events and microphone problems. Recordings and raw transcripts are never deleted by the app.</p></div>
    <div class="card"><h3>Counter</h3><p class="small">Start date: ${esc(STATE.start_date || "not started")} · app ${esc(STATE.version)} · metrics definitions v${esc(STATE.metrics_version)}</p><button class="secondary" id="reset-start">Set start date to today</button></div>`;
  $("#reset-start").addEventListener("click", async () => { if (confirm("Reset the 30 day counter to today?")) { await api("/api/start", { method: "POST", body: {} }); show("data"); } });
}

// ---------------------------------------------------------------- boot
(function themeToggle() {
  const b = $("#theme");
  if (!b) return;
  const paint = () => {
    const cur = document.documentElement.dataset.theme || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    b.textContent = cur === "dark" ? "\u2600" : "\u263E";
  };
  paint();
  b.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (_) {}
    paint();
  });
})();

// Measure the real frame rate for a moment after load. On a slow device or browser the decorative motion is
// switched off ("calm" mode) so the app itself stays smooth. The result is written to the event log.
function checkSmoothness() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let frames = 0, start = null, aborted = false;
  const onHide = () => { aborted = true; };
  document.addEventListener("visibilitychange", onHide, { once: true });
  const tick = (t) => {
    if (aborted) return;
    if (start === null) start = t;
    frames += 1;
    if (t - start < 2000) { requestAnimationFrame(tick); return; }
    document.removeEventListener("visibilitychange", onHide);
    const fps = Math.round((frames - 1) / ((t - start) / 1000));
    const calm = fps < 45;
    if (calm) document.body.classList.add("calm");
    logEvent("perf_check", { fps, calm, ua: navigator.userAgent.slice(0, 160) });
  };
  setTimeout(() => requestAnimationFrame(tick), 1200); // let the first render settle
}

(async function boot() {
  checkSmoothness();
  try { await refreshState(); } catch (e) { view.innerHTML = `<p class="err">Cannot reach the server: ${esc(e.message)}</p>`; return; }
  retryPending().catch(() => {});
  logEvent("page_loaded", { ua: navigator.userAgent });
  show("today");
})();

// ---------------------------------------------------------------- voice conversation
const SCENARIOS = [
  "A relaxed chat about my week and my plans",
  "A job interview for a role in my field",
  "Explaining my work to someone who knows nothing about it",
  "Ordering food and asking about the menu in a restaurant abroad",
  "Giving my opinion on a news topic and defending it",
];

function speakBrowser(text) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) { resolve(); return; }
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US"; u.rate = 0.95;
    const guard = setTimeout(resolve, text.split(/\s+/).length * 600 + 4000);
    u.onend = u.onerror = () => { clearTimeout(guard); resolve(); };
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
  });
}

function playUrl(url, onBlocked, handle) {
  return new Promise((resolve) => {
    const a = new Audio(url);
    a.onended = a.onerror = () => resolve();
    if (handle) handle.stop = () => { a.pause(); resolve(); };
    a.play().catch(() => { onBlocked && onBlocked(); resolve(); });
  });
}

// Live transcription: audio goes from the microphone straight to AssemblyAI over a websocket, with a short-lived
// token from our server. It only makes the reply start sooner. The timed words used for the measures still come from
// the normal transcription of each recording, done in the background.
const LIVE_MIN_FRAME = 1600;  // 100 ms at 16 kHz
class LiveSTT {
  constructor(ctx, stream) { this.ctx = ctx; this.stream = stream; this.ws = null; this.ready = false; this.sending = false; this.buffered = []; this.turns = {}; this.carry = []; this.pos = 0; this.pending = []; }
  async init() {
    if (!this.ctx.audioWorklet) return false;
    const src = "class P extends AudioWorkletProcessor{process(i){const c=i[0]&&i[0][0];if(c)this.port.postMessage(c.slice(0));return true}}registerProcessor('pcm-tap',P);";
    const url = URL.createObjectURL(new Blob([src], { type: "application/javascript" }));
    try { await this.ctx.audioWorklet.addModule(url); } catch (_) { return false; }
    this.node = new AudioWorkletNode(this.ctx, "pcm-tap");
    this.node.port.onmessage = (e) => this.onAudio(e.data);
    const mute = this.ctx.createGain(); mute.gain.value = 0;
    this.ctx.createMediaStreamSource(this.stream).connect(this.node); this.node.connect(mute); mute.connect(this.ctx.destination);
    return true;
  }
  onAudio(f32) {
    if (!this.sending) return;
    const ratio = this.ctx.sampleRate / 16000;
    for (let i = 0; i < f32.length; i++) {
      this.pos += 1;
      if (this.pos >= ratio) { this.pos -= ratio; this.carry.push(Math.max(-1, Math.min(1, f32[i]))); }
    }
    while (this.carry.length >= LIVE_MIN_FRAME) {
      const frame = this.carry.splice(0, LIVE_MIN_FRAME), i16 = new Int16Array(frame.length);
      for (let i = 0; i < frame.length; i++) i16[i] = frame[i] < 0 ? frame[i] * 0x8000 : frame[i] * 0x7fff;
      if (this.ready) this.ws.send(i16.buffer); else if (this.buffered.length < 80) this.buffered.push(i16.buffer);
    }
  }
  async begin() {  // called while the partner talks, so the connection is ready when the person starts
    this.close(); this.turns = {}; this.completeAt = null; this.carry = []; this.buffered = []; this.ready = false; this.terminated = null;
    try {
      const t = await api("/api/conversations/stream-token");
      const ws = new WebSocket(`${t.url}?sample_rate=16000&token=${encodeURIComponent(t.token)}&end_of_turn_confidence_threshold=0.7&min_end_of_turn_silence_when_confident=500&max_turn_silence=3000`);
      this.ws = ws;
      ws.onmessage = (e) => {
        let m; try { m = JSON.parse(e.data); } catch (_) { return; }
        if (m.type === "Turn" && typeof m.turn_order === "number") {
          this.turns[m.turn_order] = m.transcript || "";
          // AssemblyAI's own end-of-turn call (it listens to the words and the sound): complete until new speech arrives
          if (m.end_of_turn) this.completeAt = performance.now(); else if (m.transcript) this.completeAt = null;
        }
        if (m.type === "SpeechStarted") this.completeAt = null;
        if (m.type === "Termination" && this.terminated) this.terminated();
      };
      await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("websocket")); setTimeout(() => rej(new Error("timeout")), 4000); });
      this.ready = true;
      for (const b of this.buffered) ws.send(b);
      this.buffered = [];
    } catch (e) { logEvent("live_stt_unavailable", { error: String(e) }); this.ready = false; }
  }
  listen() { this.sending = true; }
  heard() { return Object.keys(this.turns).map(Number).sort((x, y) => x - y).map((k) => this.turns[k]).join(" ").replace(/\s+/g, " ").trim(); }
  async finish() {  // stop sending, ask AssemblyAI to close the open turn, and return everything it heard
    this.sending = false;
    if (this.ready && this.ws && this.ws.readyState === 1) {
      const done = new Promise((r) => { this.terminated = r; setTimeout(r, this.completeAt ? 250 : 1500); });  // already complete: no need to wait long
      try { this.ws.send(JSON.stringify({ type: "Terminate" })); } catch (_) {}
      await done;
    }
    const text = Object.keys(this.turns).map(Number).sort((x, y) => x - y).map((k) => this.turns[k]).join(" ").replace(/\s+/g, " ").trim();
    this.close();
    return text;
  }
  close() { try { if (this.ws) this.ws.close(); } catch (_) {} this.ws = null; this.ready = false; }
}

// A neural voice activity detector (Silero VAD, MIT licence), run in the browser from files bundled in
// app/static/vendor/vad. It tells speech from noise, breathing and echo far better than a loudness threshold.
// If it cannot load, the conversation falls back to the loudness threshold.
function loadScript(src) { return new Promise((res, rej) => { const el = document.createElement("script"); el.src = src; el.onload = res; el.onerror = () => rej(new Error(`cannot load ${src}`)); document.head.appendChild(el); }); }
async function startVad(ctx, stream, onProb) {
  const dir = "/static/vendor/vad/";
  try {
    if (!(window.ort && window.vad)) { await loadScript(`${dir}ort.wasm.min.js`); await loadScript(`${dir}bundle.min.js`); }
    window.ort.env.wasm.wasmPaths = dir; window.ort.env.wasm.numThreads = 1;
    return await window.vad.MicVAD.new({
      model: "v5", audioContext: ctx, baseAssetPath: dir, onnxWASMBasePath: dir, startOnLoad: true,
      getStream: async () => stream, pauseStream: async () => {}, resumeStream: async () => stream,
      onFrameProcessed: (p) => onProb(p.isSpeech), onSpeechStart: () => {}, onSpeechEnd: () => {}, onVADMisfire: () => {},
    });
  } catch (e) { logEvent("vad_unavailable", { error: String(e && e.message || e) }); return null; }
}

async function readNdjson(response, onEvent) {
  const reader = response.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (line) { const stop = await onEvent(JSON.parse(line)); if (stop === "stop") { try { await reader.cancel(); } catch (_) {} return; } }
    }
  }
}

async function runConversation(scenario, minutes, patienceMs, research, liveSearch, bargeIn) {
  const info = { ua: navigator.userAgent, tz: Intl.DateTimeFormat().resolvedOptions().timeZone, screen: `${screen.width}x${screen.height}` };
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
  catch (e) { alert(`The microphone is not available (${e.name}). Allow microphone access for this page and try again.`); return; }
  const box = openOverlay(`<button class="secondary" id="cancel">Cancel</button><p class="tag" style="margin-top:24px">Conversation</p><div class="big-topic">${esc(scenario || "A relaxed chat.")}</div><div id="stage"><p class="muted">${research ? "Your partner is reading up on this first. A few seconds." : "Starting…"}</p></div>`);
  const stage = $("#stage", box);
  let sid = null, over = false, doneNow = false, ending = false;
  const stopMic = () => stream.getTracks().forEach((t) => t.stop());
  $("#cancel", box).addEventListener("click", () => { over = true; stopMic(); window.speechSynthesis && window.speechSynthesis.cancel(); closeOverlay(); show(currentTab); });

  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try { await ctx.resume(); } catch (_) {}
  const cfg = await api("/api/conversations/config").catch(() => ({}));
  const live = cfg.live_stt ? new LiveSTT(ctx, stream) : null;
  const liveOk = live ? await live.init() : false;

  // the partner's first line
  let first;
  try { first = await api("/api/conversations", { method: "POST", body: { scenario, minutes, research: !!research, live_search: !!liveSearch, client_info: info } }); }
  catch (e) { stopMic(); stage.innerHTML = `<p class="err">Could not start: ${esc(e.message)}</p>`; return; }
  sid = first.id;
  logEvent("conv_opened", { patience_ms: patienceMs, minutes, live_stt: liveOk, researched: first.researched }, sid);
  stage.innerHTML = `<div class="conv-top"><span class="pill" id="pill"><i class="dot"></i><span id="state"></span></span><div class="clock small" id="clock">00:00</div></div>
    <div class="meter" aria-hidden="true"><div id="meter"></div></div>
    <div id="say" class="saybox"></div>
    <div class="conv-actions">
      <button class="primary big" id="done-turn" disabled>I'm done</button>
      <button class="secondary" id="interrupt" hidden>Interrupt</button>
      <div class="seg" role="group" aria-label="Who decides when you have finished"><button id="m-manual">I press when done</button><button id="m-auto">Auto</button></div>
      <button class="secondary" id="end">End and get feedback</button>
    </div>
    <p class="small muted" id="vnote"></p>`;
  if (first.research_note) { const n = document.createElement("p"); n.className = "small muted"; n.textContent = first.research_note; stage.prepend(n); }
  const say = $("#say", stage), stateEl = $("#state", stage), doneBtn = $("#done-turn", stage), note = $("#vnote", stage);
  const pill = $("#pill", stage), meter = $("#meter", stage), interruptBtn = $("#interrupt", stage);
  const setPhase = (phase, text) => { pill.dataset.phase = phase; stateEl.textContent = text; };
  // who decides that you have finished: you (button) or the app (a pause), switchable at any time
  let auto = patienceMs > 0; const basePatience = patienceMs || 4000;
  let maxInnerPause = 0;  // the longest pause you made in the middle of an answer so far: a hesitant speaker gets more time
  const quietAfterComplete = () => Math.max(Math.round(basePatience * 0.45), Math.min(Math.round(maxInnerPause * 1.2), 4500));  // patient 2.7 s, natural 1.8 s, quick 1.1 s, at least
  const drawMode = () => { $("#m-manual", stage).classList.toggle("on", !auto); $("#m-auto", stage).classList.toggle("on", auto); };
  $("#m-manual", stage).addEventListener("click", () => { auto = false; drawMode(); });
  $("#m-auto", stage).addEventListener("click", () => { auto = true; drawMode(); });
  drawMode();
  // a hesitating speaker is not finished: wait longer after a filler or a connector, answer sooner after a full stop
  // A sentence that ends on one of these cannot be finished, even if the recogniser put a full stop after it
  const STRONG = "the a an this that these those my your his her its our their some any to of in on at by for from with about into over and but or because if when while although so than as not can could will would shall should may might must is are was were be been am have has had do does did i you he she we they it uh um er eh like";
  const lastWord = (t) => (t.toLowerCase().replace(/[^a-z' ]+/g, " ").trim().split(/\s+/).pop() || "");
  const STRONG_SET = new Set(STRONG.split(" "));
  const endsSentence = (t) => /[.?!]["')\]]?\s*$/.test(t) && !/(\.\.\.|…)\s*$/.test(t);
  // "so" and "it" can end a real sentence ("I think so."), so they only count when there is no full stop
  const WEAK_OK = new Set(["so", "it", "i", "you", "like", "that", "not", "do", "did", "does", "can", "will", "are", "is", "was", "were", "am", "have", "has", "had", "be"]);
  const unfinished = (t) => {
    if (!t) return false;
    const w = lastWord(t), words = t.split(/\s+/).length;
    if (/[,;:…-]\s*$/.test(t) || /(\.\.\.)\s*$/.test(t)) return true;
    if (!endsSentence(t)) return STRONG_SET.has(w) || words < 3 || /(^|\s)(i mean|you know|kind of|sort of)$/i.test(t.toLowerCase());
    return STRONG_SET.has(w) && !WEAK_OK.has(w);
  };
  const patienceFor = (text) => {
    if (!text) return basePatience;
    if (unfinished(text)) return Math.min(basePatience * 2, 10000);
    if (endsSentence(text)) return Math.max(basePatience * 0.6, 1500);
    return basePatience;
  };
  const add = (who, text) => { const d = document.createElement("div"); d.className = `say-${who}`; d.textContent = text; say.appendChild(d); say.scrollTop = say.scrollHeight; return d; };
  $("#end", stage).addEventListener("click", () => { ending = true; doneNow = true; });
  doneBtn.addEventListener("click", () => { doneNow = true; });
  let partnerPlayer = null, bargedIn = false;
  const interrupt = () => { if (partnerPlayer) { bargedIn = true; partnerPlayer.stop(); } };
  interruptBtn.addEventListener("click", interrupt);

  let vadProb = 0;
  const vadInst = await startVad(ctx, stream, (p) => { vadProb = p; });
  const vadOk = !!vadInst;
  logEvent("conv_vad", { neural: vadOk }, sid);
  // loudness-based voice detection, calibrated on the room noise (used for the meter, and if the neural detector is missing)
  const analyser = ctx.createAnalyser(); analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const level = () => { analyser.getFloatTimeDomainData(buf); let s = 0; for (const v of buf) s += v * v; return Math.sqrt(s / buf.length); };
  await sleep(300);
  let ambient = 0; for (let i = 0; i < 6; i++) { ambient = Math.max(ambient, level()); await sleep(50); }
  const threshold = Math.min(0.08, Math.max(0.02, ambient * 3));
  // Interrupting by voice: you have to be clearly louder than the partner's own voice coming back through the microphone
  // (the peaks of its level, measured while it talks) and sound like speech, in most of the last 0.8 s. Speech has gaps, so
  // it is a count over a window and not an unbroken stretch. With headphones the echo is close to nothing.
  let echoPeak = 0, partnerSince = 0, recent = [];
  const watch = setInterval(() => {  // also draws the microphone level
    const lv = level(), nowMs = performance.now();
    meter.style.width = `${Math.min(100, Math.round((lv / Math.max(threshold * 3, 0.06)) * 100))}%`;
    if (!partnerPlayer) { partnerSince = 0; echoPeak = 0; recent = []; return; }
    if (!partnerPlayer.isPlaying()) { partnerSince = 0; recent = []; return; }  // measure the echo only while its voice is really playing
    if (!partnerSince) partnerSince = nowMs;
    const calibrating = nowMs - partnerSince < 700;
    const floor = Math.max(threshold * 1.5, echoPeak * 1.8);
    const loud = (vadOk ? vadProb > 0.5 : true) && lv > floor;
    if (calibrating || lv <= echoPeak * 1.3) echoPeak = Math.max(echoPeak * 0.998, lv);  // much louder than the echo's peaks: that is you, not the echo
    recent.push(loud); if (recent.length > 10) recent.shift();
    if (bargeIn && !calibrating && recent.length === 10 && recent.filter(Boolean).length >= 6) {
      recent = []; logEvent("conv_barge_in", { level: Math.round(lv * 1000) / 1000, echo_peak: Math.round(echoPeak * 1000) / 1000 }, sid); interrupt();
    }
  }, 80);
  const isVoice = () => (vadOk ? vadProb > 0.5 : level() > threshold);
  const t0 = performance.now();
  const tick = setInterval(() => { const c = $("#clock", stage); if (c) c.textContent = mmss((performance.now() - t0) / 1000); }, 500);

  // The partner's sentences are played in order, as they arrive.
  const makePlayer = () => {
    const queue = []; let finished = false, wake = null, bubble = null, text = "", cur = {}, stopped = false, playing = false;
    const run = (async () => {
      for (;;) {
        if (queue.length) {
          const sn = queue.shift();
          if (!bubble) bubble = add("partner", ""); text = (text + " " + sn.text).trim(); bubble.textContent = text; bubble.dataset.say = text; bubble.title = "Click to hear it again"; say.scrollTop = say.scrollHeight;
          note.textContent = sn.audio_url ? "" : (sn.voice_note || "");
          playing = true;
          if (sn.audio_url) await playUrl(sn.audio_url, () => { note.textContent = "Your browser blocked the sound. Click anywhere, then it works."; }, cur);
          else await speakBrowser(sn.text);
          playing = false;
          if (stopped) break;
        } else if (finished) break;
        else await new Promise((r) => { wake = r; });
      }
    })();
    const stop = () => { stopped = true; finished = true; queue.length = 0; if (cur.stop) cur.stop(); window.speechSynthesis && window.speechSynthesis.cancel(); if (wake) { const w = wake; wake = null; w(); } };
    return { stop, isPlaying: () => playing, push: (sn) => { if (!stopped) queue.push(sn); if (wake) { const w = wake; wake = null; w(); } }, end: () => { finished = true; if (wake) { const w = wake; wake = null; w(); } }, done: run };
  };
  let partnerEndedAt = performance.now();
  setPhase("partner", "Your partner is speaking…"); interruptBtn.hidden = false;
  if (liveOk) live.begin();
  { const pl = makePlayer(); partnerPlayer = pl; first.sentences.forEach(pl.push); pl.end(); await pl.done; partnerPlayer = null; interruptBtn.hidden = true; partnerEndedAt = performance.now(); }

  while (!over) {
    setPhase("you", auto ? "Your turn. I answer when you pause." : "Your turn. Press “I'm done” when you finish."); doneBtn.disabled = false; doneNow = false; bargedIn = false;
    if (liveOk) live.listen();
    const mt = pickMime();
    const rec = new MediaRecorder(stream, mt ? { mimeType: mt } : undefined);
    const chunks = []; rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.start(500);
    const turnStart = performance.now();
    let spoke = false, firstSpeechAt = null, lastVoiceAt = performance.now(), voiceMs = 0, endReason = "button";
    while (!over && !doneNow) {
      await sleep(80);
      const now = performance.now();
      // the first 0.35 s after the partner stops are ignored: its own voice can still come back through the microphone
      const echoGrace = !bargedIn && now - turnStart < 350;
      if (!echoGrace && isVoice()) { if (spoke && now - lastVoiceAt >= 600) maxInnerPause = Math.max(maxInnerPause, now - lastVoiceAt); voiceMs += 80; lastVoiceAt = now; if (!spoke && voiceMs >= 240) { spoke = true; firstSpeechAt = now - 240; setPhase("you", auto ? "Listening… I answer when you pause." : "Listening… press “I'm done” when you finish."); } }
      if (auto && spoke) {
        // like a voice assistant: answer soon after a finished sentence, wait longer when you are still searching for words
        const heard = liveOk ? live.heard() : "", quiet = now - lastVoiceAt;
        if (liveOk && !heard) { /* the detector heard something but no word was recognised: noise or echo, keep waiting */ }
        else if (liveOk && live.completeAt && endsSentence(heard) && !unfinished(heard) && quiet > quietAfterComplete()) { endReason = "sentence_complete"; break; }
        else if (quiet > patienceFor(heard)) { endReason = "patience"; break; }
      }
      if (now - turnStart > 150000) { endReason = "cap"; break; }
    }
    const durationS = (performance.now() - turnStart) / 1000;
    logEvent("conv_turn_end", { reason: doneNow ? "button" : endReason, auto, last_words: liveOk ? live.heard().split(/\s+/).slice(-6).join(" ") : null, inner_pause_ms: Math.round(maxInnerPause), spoke, voice_ms: voiceMs, seconds: Math.round(durationS * 10) / 10, heard_words: liveOk ? live.heard().split(/\s+/).filter(Boolean).length : null, neural_vad: vadOk }, sid);
    await new Promise((resolve) => { rec.onstop = resolve; if (rec.state !== "inactive") rec.stop(); });
    if (over) break;
    if (!spoke) { if (ending) break; continue; }  // nothing said: keep waiting, nothing is sent
    setPhase("thinking", "…"); doneBtn.disabled = true;
    const liveText = liveOk ? await live.finish() : "";
    const fd = new FormData();
    fd.append("file", new Blob(chunks, { type: rec.mimeType || "audio/webm" }), "turn.webm");
    fd.append("duration_s", String(durationS)); fd.append("mime", (rec.mimeType || "audio/webm").split(";")[0]);
    fd.append("latency_ms", String(Math.max(0, Math.round(firstSpeechAt - partnerEndedAt))));
    fd.append("live_text", liveText);
    const sentAt = performance.now();
    let gotAudio = false, errMsg = null, empty = false;
    try {
      const r = await fetch(`/api/conversations/${sid}/turn`, { method: "POST", body: fd });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || r.statusText); }
      if (liveOk && !ending) live.begin();  // reconnect while the partner answers
      setPhase("partner", "Your partner is answering…"); interruptBtn.hidden = false;
      const pl = makePlayer(); partnerPlayer = pl;
      await readNdjson(r, async (ev) => {
        if (ev.type === "empty") { empty = true; }
        else if (ev.type === "user") { add("user", ev.text); if (ending) return "stop"; }
        else if (bargedIn) { return "stop"; }
        else if (ev.type === "sentence") { if (!gotAudio) { gotAudio = true; logEvent("conv_first_sentence", { ms_after_send: Math.round(performance.now() - sentAt) }, sid); } pl.push(ev); }
        else if (ev.type === "error") { errMsg = ev.message; }
      });
      pl.end(); await pl.done; partnerPlayer = null; interruptBtn.hidden = true; partnerEndedAt = performance.now();
    } catch (e) { errMsg = e.message; logEvent("conv_turn_failed", { error: String(e) }, sid); }
    partnerPlayer = null; interruptBtn.hidden = true;
    if (empty) { setPhase("you", "I did not catch any words. Try again."); if (ending) break; if (liveOk) live.begin(); continue; }
    if (errMsg) { stateEl.innerHTML = `<span class="err">${esc(errMsg)}</span> Say it again, or end the conversation.`; if (liveOk && !ending) live.begin(); if (ending) break; continue; }
    if (ending || (performance.now() - t0) / 60000 >= minutes) break;
  }
  clearInterval(tick); clearInterval(watch); if (vadInst) { try { vadInst.destroy(); } catch (_) {} } stopMic(); if (live) live.close(); ctx.close();
  if (over) return;
  stage.innerHTML = `<p class="muted">Finishing… the last recordings are being transcribed.</p>`;
  const end = await api(`/api/conversations/${sid}/end`, { method: "POST", body: {} }).catch((e) => ({ status: "error", error: e.message }));
  if (end.status === "error") { stage.innerHTML = `<p class="err">${esc(end.error || "No speech was captured.")}</p><button class="primary" id="done">Close</button>`; $("#done", stage).addEventListener("click", () => { closeOverlay(); show("today"); }); return; }
  logEvent("conv_ended", { delays_ms: end.delays_ms }, sid);
  await showResult(box, sid);
  if (end.delays_ms && end.delays_ms.length) {
    const sorted = [...end.delays_ms].sort((a, b) => a - b), med = sorted[Math.floor(sorted.length / 2)];
    const p = document.createElement("p"); p.className = "small muted";
    p.textContent = `Time to start answering after your partner finished: median ${(med / 1000).toFixed(1)} s over ${end.delays_ms.length} turns. A shorter delay with the same content is a sign of faster formulation.`;
    $("#stage", box).prepend(p);
  }
}

async function conversationCard() {
  const cfg = await api("/api/conversations/config").catch(() => null);
  if (!cfg) return "";
  const ready = cfg.llm_ready;
  return `<div class="card"><h3>Talk with a partner</h3><p class="muted small">A spoken conversation. The partner asks, you answer out loud, you choose whether you press “I'm done” or it answers by itself when your sentence is finished (and waits when you hesitate), and you can talk over it to interrupt. Your speech is measured like any other session, then you get the same feedback. ${ready ? "" : `<span class="err">Not available: ${esc(cfg.llm_error || "no language model")}</span>`}</p>
    <div class="row"><select id="conv-scn">${SCENARIOS.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("")}<option value="">My own scenario…</option></select><select id="conv-len"><option value="5">5 min</option><option value="10" selected>10 min</option><option value="20">20 min</option></select><select id="conv-wait" title="Who decides that you have finished speaking"><option value="0" selected>I press “I'm done”</option><option value="6000">Auto, patient</option><option value="4000">Auto, natural</option><option value="2500">Auto, quick</option></select></div>
    <div class="row" style="margin-top:8px"><input type="text" id="conv-custom" placeholder="Describe the situation" size="50" hidden><button class="primary" id="conv-start" ${ready ? "" : "disabled"}>Start</button></div>
    <label class="small" style="display:block;margin-top:6px"><input type="checkbox" id="conv-barge"> Let me interrupt the partner by talking over it (headphones only: with speakers its own voice can cut it off)</label><label class="small" style="display:block;margin-top:6px"><input type="checkbox" id="conv-live"> Let the partner search the web during the conversation when it needs a fact (those turns answer a few seconds slower, about a cent per search)</label><label class="small" style="display:block;margin-top:6px"><input type="checkbox" id="conv-research"> Let the partner read up on the topic first (searches the web, a few seconds and about a cent)</label><p class="small muted">Voice: ${cfg.voice ? `${esc(cfg.voice)} (${esc(cfg.voice_model || "")})` : esc(cfg.voice_note || "browser voice")}</p></div>`;
}

function wireConversationCard() {
  const sel = $("#conv-scn"); if (!sel) return;
  const rs = $("#conv-research");
  const autoResearch = () => { rs.checked = sel.value === "" || /news/i.test(sel.value); };
  sel.addEventListener("change", () => { const c = $("#conv-custom"); c.hidden = sel.value !== ""; if (!c.hidden) c.focus(); autoResearch(); });
  $("#conv-start").addEventListener("click", () => {
    const scn = sel.value || $("#conv-custom").value.trim();
    if (!scn) { alert("Describe the situation first."); return; }
    runConversation(scn, Number($("#conv-len").value), Number($("#conv-wait").value), $("#conv-research").checked, $("#conv-live").checked, $("#conv-barge").checked);
  });
}
