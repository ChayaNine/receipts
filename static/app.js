/* Receipts front end: streams a check from /api/check and renders the document, receipt, findings and trace. */
(() => {
  "use strict";

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "html") el.innerHTML = v; // only used with static, trusted SVG strings below
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  };
  const host = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url || ""; } };
  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);
  const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n)));

  const ICON = {
    supported: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M4.8 8.2l2.1 2.1 4.3-4.4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    partial: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 1a7 7 0 0 1 0 14z" fill="currentColor"/></svg>',
    misrepresented: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l6.8 12H1.2z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6v3.6M8 11.6v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    contradicted: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    not_found: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10.7 10.7L14.5 14.5M5 5l4 4M9 5L5 9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    unverifiable: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 2"/><path d="M6.2 6.2a1.9 1.9 0 1 1 2.6 1.8c-.6.3-.8.6-.8 1.2M8 11.7v.1" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    pending: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="3 2.2"/></svg>',
    external: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M5 2H2v8h8V7M7 2h3v3M10 2L5.5 6.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  const V = {
    supported: { label: "Supported", code: "OK" },
    partial: { label: "Partly supported", code: "PART" },
    misrepresented: { label: "Misrepresented", code: "WRONG" },
    contradicted: { label: "Contradicted", code: "FALSE" },
    not_found: { label: "Not found", code: "VOID" },
    unverifiable: { label: "Can't verify", code: "N/A" },
    pending: { label: "Checking", code: "..." },
  };
  const PROBLEMS = new Set(["misrepresented", "contradicted", "not_found", "partial"]);
  const ACTOR = {
    fast: { name: "Nemotron 3 Nano", job: "Finds cited claims, matches search results" },
    judge: { name: "Nemotron 3 Super", job: "Judges each claim against its source" },
    reason: { name: "Nemotron 3 Ultra", job: "Second opinions and the final report" },
    tavily: { name: "Tavily", job: "Searches for sources and reads them" },
  };
  const STATUS_TEXT = { searching: "Finding the source", reading: "Reading the source", judging: "Comparing with the source", escalating: "Getting a second opinion" };

  // ---------- state ----------
  let config = null;
  let S = null;
  const freshState = () => ({
    mode: "live", text: "", originalText: "", title: "", claims: [], byId: {}, results: {}, status: {},
    trace: [], summary: null, stats: null, filter: "all", active: null, open: null, startedAt: performance.now(),
    done: false, recordedAt: null, liveAvailable: false, request: null, controller: null, actors: {},
  });

  // ---------- boot ----------
  async function init() {
    try {
      config = await (await fetch("/api/config")).json();
    } catch {
      config = { mode: "live", samples: [], max_chars: 40000 };
    }
    setModePill(config.mode);
    if (config.mode === "simulated") banner("Simulated mode: no API keys are set, so results are scripted for testing. Set NEBIUS_API_KEY and TAVILY_API_KEY for real checks.");
    if (config.mode === "replay-only") banner("This server has no API keys. The samples replay recorded live runs; pasted text can't be checked here.");
    const list = $("#sampleList");
    for (const s of config.samples || []) {
      list.append(h("button", { class: "sample", type: "button", onclick: () => start({ sample_id: s.id }, s.title) },
        h("span", { class: "s-title", text: s.title }),
        h("span", { class: "s-sub", text: s.subtitle }),
        s.cached && config.mode !== "simulated" ? h("span", { class: "s-cached", text: "Replays a recorded live run" }) : null,
      ));
    }
    wireInputs();
    wireReview();
  }

  function setModePill(mode, recordedAt) {
    const pill = $("#modePill");
    const text = {
      live: "Live on Nebius Token Factory",
      replay: `Replay of a live run${recordedAt ? ", " + new Date(recordedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""}`,
      simulated: "Simulated",
      "replay-only": "Samples only",
    }[mode] || mode;
    pill.className = `mode-pill ${mode}`;
    pill.replaceChildren(h("span", { class: "dot" }), text);
    pill.hidden = false;
  }

  function banner(message, kind = "") {
    const b = $("#banner");
    if (!message) { b.hidden = true; return; }
    b.className = `banner ${kind}`;
    b.replaceChildren(message);
    b.hidden = false;
  }

  // ---------- input ----------
  function wireInputs() {
    const input = $("#input");
    const count = $("#charCount");
    const updateCount = () => {
      const n = input.value.length;
      count.textContent = n ? `${n.toLocaleString()} characters${n > config.max_chars ? " (only the first " + config.max_chars.toLocaleString() + " are checked)" : ""}` : "";
    };
    input.addEventListener("input", updateCount);
    $("#pasteForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (text.length < 40) { input.focus(); banner("Paste a few sentences (or a link) to check.", "error"); return; }
      banner(null);
      start({ text });
    });
    $("#fileInput").addEventListener("change", (e) => { if (e.target.files[0]) startFile(e.target.files[0]); });
    const drop = $("#drop");
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("dragging"); }));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("dragging"); }));
    drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) startFile(f); });
    $("#howBtn").addEventListener("click", () => $("#howDialog").showModal());
    $("#newBtn").addEventListener("click", reset);
    $("#exportBtn").addEventListener("click", exportReport);
  }

  function startFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    start({ formData: fd }, file.name);
  }

  function reset() {
    if (S && S.controller) S.controller.abort();
    S = null;
    $("#review").hidden = true;
    $("#start").hidden = false;
    $("#newBtn").hidden = true;
    $("#exportBtn").hidden = true;
    $("#docTitle").hidden = true;
    setModePill(config.mode);
    banner(config.mode === "simulated" ? "Simulated mode: results are scripted for testing." : null);
    window.scrollTo({ top: 0 });
  }

  // ---------- run ----------
  async function start(request, title) {
    if (S && S.controller) S.controller.abort();
    S = freshState();
    S.request = request;
    S.title = title || "";
    S.controller = new AbortController();
    $("#start").hidden = true;
    $("#review").hidden = false;
    $("#newBtn").hidden = false;
    $("#exportBtn").hidden = true;
    $("#sheet").replaceChildren(h("p", { class: "empty", text: "Loading the document..." }));
    $("#cards").replaceChildren();
    $("#rItems").replaceChildren();
    $("#rTotals").replaceChildren();
    $("#rStamp").replaceChildren();
    $("#note").hidden = true;
    $("#trace").replaceChildren();
    $("#roles").replaceChildren();
    $("#findCount").textContent = "";
    setProgress("Starting the check", true);
    renderReceiptMeta();
    window.scrollTo({ top: 0 });

    const opts = { method: "POST", signal: S.controller.signal };
    if (request.formData) opts.body = request.formData;
    else { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(request); }
    let resp;
    try {
      resp = await fetch("/api/check", opts);
    } catch (err) {
      if (err.name !== "AbortError") showError("Couldn't reach the Receipts server. Check your connection and try again.");
      return;
    }
    if (!resp.ok) {
      let msg = `The server returned ${resp.status}.`;
      try { msg = (await resp.json()).error || msg; } catch {}
      showError(msg);
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    const mine = S;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          if (S !== mine) return;
          try { onEvent(JSON.parse(line.slice(6))); } catch (e) { console.error(e); }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") showError("The connection dropped before the check finished. Run it again.");
      return;
    }
    if (S === mine && !S.done && !S.errored) showError("The check ended early. Run it again.");
  }

  function showError(message) {
    if (!S) return;
    S.errored = true;
    setProgress(message, false);
    banner(message, "error");
    if (!S.text) $("#sheet").replaceChildren(h("p", { class: "empty", text: "Nothing to show yet." }));
  }

  function onEvent(e) {
    switch (e.type) {
      case "meta":
        S.mode = e.mode;
        S.text = S.originalText = e.text || "";
        S.title = e.title || S.title;
        S.recordedAt = e.recorded_at || null;
        S.liveAvailable = !!e.live_available;
        setModePill(e.mode === "replay-only" ? "replay-only" : e.mode, e.recorded_at);
        $("#docTitle").textContent = S.title;
        $("#docTitle").hidden = false;
        if (e.mode === "replay") {
          const again = S.liveAvailable ? h("button", { class: "btn small", type: "button", onclick: () => start({ ...S.request, fresh: true }, S.title) }, "Run it live again") : null;
          banner(h("span", {}, `You're watching a recorded live run from ${new Date(e.recorded_at).toLocaleString()}, so it plays instantly and costs nothing. `, again));
        } else if (e.mode === "simulated") {
          banner("Simulated mode: results are scripted for testing, not real checks.");
        } else {
          banner(e.truncated ? `Only the first ${config.max_chars.toLocaleString()} characters are being checked.` : null);
        }
        renderDocument();
        renderReceiptMeta();
        break;
      case "models":
        S.models = e.models;
        break;
      case "stage":
        setProgress(e.message, true);
        break;
      case "claims":
        S.claims = e.claims;
        S.byId = Object.fromEntries(e.claims.map((c, i) => [c.id, { ...c, n: i + 1 }]));
        renderDocument();
        renderCards();
        renderReceiptItems();
        announce(`Found ${e.claims.length} cited claims. Checking each one.`);
        break;
      case "claim_status":
        S.status[e.id] = e;
        updateCard(e.id);
        markWorking(e.id, true);
        break;
      case "claim_result":
        S.results[e.id] = e;
        delete S.status[e.id];
        markWorking(e.id, false);
        paintMark(e.id);
        updateCard(e.id);
        renderReceiptItems(e.id);
        renderTotals();
        updateCount();
        announce(`Claim ${S.byId[e.id]?.n}: ${V[e.verdict]?.label || e.verdict}.`);
        {
          const doneN = Object.keys(S.results).length;
          setProgress(`Checked ${doneN} of ${S.claims.length} claims`, doneN < S.claims.length);
        }
        break;
      case "trace":
        addTrace(e);
        break;
      case "summary":
        S.summary = e;
        renderTotals();
        renderNote();
        break;
      case "done":
        S.done = true;
        S.stats = e.stats;
        renderRoles();
        const secs = e.stats?.seconds;
        setProgress(S.claims.length ? `Done${secs ? ` in ${secs}s` : ""}. Click a highlighted sentence to see what its source says.` : "Done.", false);
        $("#exportBtn").hidden = !S.claims.length;
        if (!S.open) {
          const first = S.claims.find((c) => PROBLEMS.has(S.results[c.id]?.verdict));
          if (first) select(first.id, { open: true, scroll: false });
        }
        break;
      case "error":
        showError(e.message);
        break;
    }
  }

  function setProgress(message, busy) {
    const p = $("#progress");
    const kids = [message || ""];
    if (busy) kids.unshift(h("span", { class: "spinner", "aria-hidden": "true" }));
    p.replaceChildren(...kids);
  }
  function announce(msg) { $("#announcer").textContent = msg; }

  // ---------- document ----------
  function renderDocument() {
    const sheet = $("#sheet");
    const text = S.text || "";
    const spans = S.claims.filter((c) => c.start !== null && c.start !== undefined).sort((a, b) => a.start - b.start);
    const frag = document.createDocumentFragment();
    let pos = 0;
    for (const c of spans) {
      if (c.start < pos) continue; // skip overlaps
      if (c.start > pos) frag.append(text.slice(pos, c.start));
      const mark = h("mark", {
        class: "claim", "data-id": c.id, tabindex: "0", role: "button",
        "aria-label": `Claim ${S.byId[c.id]?.n}: ${text.slice(c.start, c.end).slice(0, 80)}`,
        onclick: () => select(c.id, { open: true, from: "doc" }),
        onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(c.id, { open: true, from: "doc" }); } },
      }, text.slice(c.start, c.end));
      frag.append(mark, h("span", { class: "claim-tag", "aria-hidden": "true", text: S.byId[c.id]?.n }));
      pos = c.end;
    }
    frag.append(text.slice(pos));
    sheet.replaceChildren(frag);
    for (const c of S.claims) { paintMark(c.id); if (S.status[c.id]) markWorking(c.id, true); }
    if (S.active) document.querySelector(`mark[data-id="${S.active}"]`)?.classList.add("active");
  }
  function paintMark(id) {
    const m = document.querySelector(`mark[data-id="${id}"]`);
    const r = S.results[id];
    if (!m) return;
    if (r) m.dataset.v = r.verdict;
    m.classList.toggle("fixed", !!S.byId[id]?.fixed);
  }
  function markWorking(id, on) {
    document.querySelector(`mark[data-id="${id}"]`)?.classList.toggle("working", on);
  }

  // ---------- cards ----------
  function visibleClaims() {
    return S.claims.filter((c) => {
      const v = S.results[c.id]?.verdict;
      if (S.filter === "problems") return v && PROBLEMS.has(v);
      if (S.filter === "supported") return v === "supported";
      return true;
    });
  }
  function renderCards() {
    const wrap = $("#cards");
    const list = visibleClaims();
    if (!S.claims.length) {
      wrap.replaceChildren(h("p", { class: "empty", text: "No cited claims yet." }));
      return;
    }
    if (!list.length) {
      wrap.replaceChildren(h("p", { class: "empty", text: S.filter === "problems" ? "No problems found in the claims checked so far." : "Nothing here yet." }));
      return;
    }
    wrap.replaceChildren(...list.map(cardFor));
    updateCount();
  }
  function updateCard(id) {
    const old = document.querySelector(`.card[data-id="${id}"]`);
    const shouldShow = visibleClaims().some((c) => c.id === id);
    if (old && shouldShow) old.replaceWith(cardFor(S.byId[id]));
    else if (!old && shouldShow) renderCards();
    else if (old && !shouldShow) old.remove();
  }
  function updateCount() {
    const problems = S.claims.filter((c) => PROBLEMS.has(S.results[c.id]?.verdict)).length;
    $("#findCount").textContent = S.claims.length ? `${problems ? problems + " to fix · " : ""}${S.claims.length}` : "";
  }

  function shortCite(c) {
    const cite = (c.citation || "").trim().replace(/^(?:in|see also|see|cf\.|accord|but see)\s+/i, "");
    const caseName = cite.match(/([A-Z][\w.'&-]+(?:\s+[\w.'&-]+){0,4}\s+v\.\s+[A-Z][\w.'&-]+(?:\s+[A-Z][\w.'&-]+){0,3})/);
    if (caseName) return caseName[1].replace(/,$/, "");
    if (/rule|r\.\s*civ|u\.s\.c\.|§/i.test(cite) && cite.length < 80) return cite;
    const quoted = cite.match(/["“]([^"”]{6,80})["”]/);
    if (quoted) return quoted[1];
    const author = cite.match(/^([A-Z][A-Za-z'-]+),\s+[A-Z]\./);
    const year = cite.match(/\((\d{4})\)/);
    if (author) return `${author[1]}${/&|et al/.test(cite) ? " et al." : ""}${year ? " (" + year[1] + ")" : ""}`;
    if (c.url) return host(c.url);
    return cite.slice(0, 60) || c.claim.slice(0, 60);
  }

  function verdictTag(v) {
    return h("span", { class: "verdict", "data-v": v, html: ICON[v] || ICON.pending }, h("span", { text: V[v]?.label || v }));
  }

  function cardFor(c) {
    const r = S.results[c.id];
    const st = S.status[c.id];
    const v = r ? r.verdict : "pending";
    const open = S.open === c.id;
    const card = h("div", {
      class: `card${S.active === c.id ? " active" : ""}${open ? " open" : ""}`, "data-id": c.id, "data-v": r ? v : null,
      tabindex: "0", role: "button", "aria-expanded": open ? "true" : "false",
      onclick: (ev) => { if (ev.target.closest("a,button")) return; select(c.id, { open: S.open !== c.id, from: "card" }); },
      onkeydown: (ev) => { if ((ev.key === "Enter" || ev.key === " ") && ev.target === card) { ev.preventDefault(); select(c.id, { open: S.open !== c.id, from: "card" }); } },
    },
      h("div", { class: "card-top" }, verdictTag(v), r ? h("span", { class: "conf", text: `${Math.round((r.confidence || 0) * 100)}% sure` }) : null),
      h("div", { class: "cite", text: `${c.n ?? S.byId[c.id]?.n}. ${shortCite(c)}` }),
      !r ? h("div", { class: "status-line", text: st ? (st.detail || STATUS_TEXT[st.status] || "Working") : "Waiting its turn" }) : null,
      r ? h("p", { class: "explain", text: r.explanation }) : null,
    );
    if (r) card.append(cardBody(c, r));
    return card;
  }

  function cardBody(c, r) {
    const body = h("div", { class: "card-body" });
    const doc = S.text.slice(c.start ?? 0, c.end ?? 0) || c.claim;
    body.append(h("div", { class: "said" }, h("h4", { text: "The document says" }), h("blockquote", { text: doc })));
    if (r.evidence) {
      body.append(h("div", { class: "said source" },
        h("h4", { text: r.verdict === "not_found" ? "What the search found" : "The source says" }),
        h("blockquote", { text: r.evidence }),
        h("div", { class: `ground ${r.evidence_verified ? "ok" : "no"}` },
          r.evidence_verified ? "Quote found word-for-word in the source" : "Couldn't match this quote word-for-word, so confidence was lowered"),
      ));
    }
    if (r.quote_check && /NOT found/.test(r.quote_check)) {
      body.append(h("div", { class: "ground no" }, "The document's quotation does not appear word-for-word in the source."));
    }
    if (r.source && r.source.url) {
      body.append(h("div", {},
        h("a", { class: "src-link", href: r.source.url, target: "_blank", rel: "noopener noreferrer" },
          h("span", { text: r.source.title ? `${r.source.title} (${host(r.source.url)})` : host(r.source.url) }),
          h("span", { html: ICON.external })),
        r.source.secondary ? h("div", { class: "status-line", text: r.verdict === "not_found" ? "Closest search result, not the cited source" : "Secondary source that discusses the cited work" }) : null,
      ));
    }
    const by = (r.judged_by || []).map((role) => h("span", {}, h("i", { class: `actor-${role}` }), ACTOR[role]?.name || role));
    const readBy = S.trace.some((t) => t.claim_id === c.id && t.actor === "tavily") ? [h("span", {}, h("i", { class: "actor-tavily" }), "Tavily")] : [];
    body.append(h("div", { class: "by", "aria-label": "Checked by" }, ...readBy, ...by));
    if (r.overturned) {
      body.append(h("p", { class: "overturn", text: `Nemotron 3 Ultra overturned the first verdict (${V[r.first_verdict]?.label || r.first_verdict}).` }));
    }
    if (r.corrected_claim && r.verdict !== "supported") {
      const fixed = S.byId[c.id]?.fixed;
      body.append(h("div", { class: "fix" },
        h("h4", { text: fixed ? "Fixed in the document" : "Suggested rewrite" }),
        h("p", { text: r.corrected_claim }),
        h("div", { class: "card-actions" },
          fixed
            ? h("button", { class: "btn small", type: "button", onclick: () => undoFix(c.id) }, "Undo")
            : h("button", { class: "btn small", type: "button", onclick: () => applyFix(c.id) }, "Apply to document"),
          h("button", { class: "btn small ghost", type: "button", onclick: (ev) => copy(r.corrected_claim, ev.currentTarget) }, "Copy"),
        ),
      ));
    }
    if (["not_found", "misrepresented", "contradicted", "unverifiable"].includes(r.verdict)) {
      const slot = h("div", {});
      const btn = h("button", { class: "btn small", type: "button", onclick: () => findSource(c, btn, slot) }, "Find a source that supports this");
      body.append(h("div", { class: "card-actions" }, btn), slot);
    }
    return body;
  }

  async function copy(text, btn) {
    try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; } catch { btn.textContent = "Copy failed"; }
    setTimeout(() => (btn.textContent = "Copy"), 1500);
  }

  async function findSource(c, btn, slot) {
    btn.disabled = true;
    btn.textContent = "Searching with Tavily...";
    try {
      const resp = await fetch("/api/find-source", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ claim: c.assertion || c.claim }) });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Search failed");
      if (!data.found) { slot.replaceChildren(h("div", { class: "found-source", text: "No source on the open web clearly supports this claim. Consider removing it." })); btn.remove(); return; }
      slot.replaceChildren(h("div", { class: "found-source" },
        h("a", { class: "src-link", href: data.url, target: "_blank", rel: "noopener noreferrer" }, h("span", { text: data.title || host(data.url) }), h("span", { html: ICON.external })),
        data.evidence ? h("blockquote", { class: "said", text: data.evidence }) : null,
        data.suggested_citation ? h("div", {}, h("strong", { text: "Cite as: " }), data.suggested_citation) : null,
        h("div", { class: "status-line", text: `Picked by ${data.model || "Nemotron 3 Super"}${data.evidence_verified ? ", quote verified in the search result" : ""}` }),
      ));
      btn.remove();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Try the search again";
      slot.replaceChildren(h("div", { class: "found-source", text: String(err.message || err) }));
    }
  }

  // ---------- fixes ----------
  function shiftAfter(pos, delta, exceptId) {
    for (const c of S.claims) {
      if (c.id === exceptId || c.start === null || c.start === undefined) continue;
      if (c.start >= pos) { c.start += delta; c.end += delta; }
    }
  }
  function applyFix(id) {
    const c = S.byId[id];
    const r = S.results[id];
    const claim = S.claims.find((x) => x.id === id);
    if (!r?.corrected_claim || claim.start === null) return;
    const before = S.text.slice(claim.start, claim.end);
    const after = r.corrected_claim;
    S.text = S.text.slice(0, claim.start) + after + S.text.slice(claim.end);
    shiftAfter(claim.end, after.length - before.length, id);
    claim.end = claim.start + after.length;
    c.fixed = { before };
    Object.assign(c, { start: claim.start, end: claim.end });
    renderDocument();
    updateCard(id);
    announce("Fix applied to the document.");
  }
  function undoFix(id) {
    const c = S.byId[id];
    const claim = S.claims.find((x) => x.id === id);
    if (!c.fixed) return;
    const before = c.fixed.before;
    const cur = S.text.slice(claim.start, claim.end);
    S.text = S.text.slice(0, claim.start) + before + S.text.slice(claim.end);
    shiftAfter(claim.end, before.length - cur.length, id);
    claim.end = claim.start + before.length;
    Object.assign(c, { start: claim.start, end: claim.end });
    delete c.fixed;
    renderDocument();
    updateCard(id);
  }

  // ---------- selection & keyboard ----------
  function select(id, { open = false, from = "", scroll = true } = {}) {
    S.active = id;
    if (open !== undefined) S.open = open ? id : (S.open === id ? null : S.open);
    document.querySelectorAll("mark.claim.active, .card.active").forEach((el) => el.classList.remove("active"));
    const mark = document.querySelector(`mark[data-id="${id}"]`);
    mark?.classList.add("active");
    if (!visibleClaims().some((c) => c.id === id)) { S.filter = "all"; syncFilters(); }
    renderCards();
    const card = document.querySelector(`.card[data-id="${id}"]`);
    if (scroll) {
      if (from !== "doc") mark?.scrollIntoView({ behavior: "smooth", block: "center" });
      card?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    if (from === "card" || from === "key") card?.focus({ preventScroll: true });
  }

  function syncFilters() {
    document.querySelectorAll(".chip[data-filter]").forEach((b) => {
      const on = b.dataset.filter === S.filter;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function wireReview() {
    document.querySelectorAll(".chip[data-filter]").forEach((b) => b.addEventListener("click", () => {
      if (!S) return;
      S.filter = b.dataset.filter;
      syncFilters();
      renderCards();
    }));
    const tabs = [$("#tabFindings"), $("#tabTrace")];
    const panels = [$("#panelFindings"), $("#panelTrace")];
    tabs.forEach((t, i) => t.addEventListener("click", () => {
      tabs.forEach((x, j) => { x.setAttribute("aria-selected", i === j ? "true" : "false"); x.tabIndex = i === j ? 0 : -1; panels[j].hidden = i !== j; });
    }));
    document.addEventListener("keydown", (e) => {
      if (!S || $("#review").hidden || e.target.matches("textarea, input") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j" || e.key === "k") {
        const list = visibleClaims();
        if (!list.length) return;
        const i = list.findIndex((c) => c.id === S.active);
        const next = e.key === "j" ? list[Math.min(list.length - 1, i + 1)] : list[Math.max(0, i - 1)];
        select(next.id, { open: true, from: "key" });
      } else if (e.key === "Escape" && S.open) {
        S.open = null;
        renderCards();
      }
    });
  }

  // ---------- receipt ----------
  function renderReceiptMeta() {
    const d = new Date(S?.recordedAt || Date.now());
    $("#rMeta").replaceChildren(
      h("div", { text: (S?.title || "Document").slice(0, 38) }),
      h("div", { text: `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` }),
    );
  }
  function renderReceiptItems(newId) {
    const ol = $("#rItems");
    ol.replaceChildren(...S.claims.map((c) => {
      const r = S.results[c.id];
      const label = shortCite(S.byId[c.id] || c);
      return h("li", {
        class: `${r ? "" : "waiting"}${newId === c.id ? " new" : ""}`, title: label,
        onclick: () => select(c.id, { open: true }),
      },
        h("span", { text: `#${S.byId[c.id]?.n}` }),
        h("span", { class: "r-label", text: label }),
        h("span", { class: `r-code${r && PROBLEMS.has(r.verdict) && r.verdict !== "partial" ? " bad" : ""}`, text: r ? V[r.verdict]?.code : "..." }),
      );
    }));
  }
  function renderTotals() {
    const counts = { supported: 0, partial: 0, misrepresented: 0, contradicted: 0, not_found: 0, unverifiable: 0 };
    for (const r of Object.values(S.results)) counts[r.verdict] = (counts[r.verdict] || 0) + 1;
    const rows = [["Supported", counts.supported], ["Partly supported", counts.partial], ["Misrepresented", counts.misrepresented],
      ["Contradicted", counts.contradicted], ["Not found", counts.not_found], ["Can't verify", counts.unverifiable]].filter(([, n]) => n);
    const checkable = Object.values(counts).reduce((a, b) => a + b, 0) - counts.unverifiable;
    const score = S.summary?.score ?? (checkable ? Math.round((100 * (counts.supported + 0.5 * counts.partial)) / checkable) : null);
    $("#rTotals").replaceChildren(...[
      ...rows.map(([k, n]) => h("div", {}, h("span", { text: k }), h("span", { text: String(n) }))),
      score !== null ? h("div", { class: "r-score" }, h("span", { text: "HOLDS UP" }), h("span", { text: `${score}%` })) : null,
    ].filter(Boolean));
  }
  function renderNote() {
    const s = S.summary;
    if (!s) return;
    const stamp = { high: "DO NOT FILE", medium: "REVIEW FIRST", low: "LOOKS GOOD" }[s.risk] || "";
    $("#rStamp").replaceChildren(...[stamp ? h("span", { class: `r-stamp ${s.risk}`, text: stamp }) : null].filter(Boolean));
    const note = $("#note");
    note.replaceChildren(...[
      h("h2", { class: "note-head", text: s.headline }),
      s.summary ? h("p", { text: s.summary }) : null,
      s.actions?.length ? h("ul", {}, ...s.actions.map((a) => h("li", { text: a }))) : null,
      h("p", { class: "note-by", text: "Reviewer's note written by Nemotron 3 Ultra" }),
    ].filter(Boolean));
    note.dataset.risk = s.risk || "low";
    note.hidden = false;
  }

  // ---------- trace ----------
  function addTrace(e) {
    S.trace.push({ ...e, at: performance.now() - S.startedAt });
    const a = (S.actors[e.actor] ||= { calls: 0, tokens: 0, ms: 0, credits: 0 });
    a.calls += 1;
    a.tokens += (e.tokens_in || 0) + (e.tokens_out || 0);
    a.ms += e.ms || 0;
    a.credits += Number(e.credits || 0);
    const n = e.claim_id ? S.byId[e.claim_id]?.n : null;
    $("#trace").append(h("li", {},
      h("i", { class: `actor-${e.actor}` }),
      h("span", { class: "t-act", title: e.action }, h("b", { text: (ACTOR[e.actor]?.name || e.label) + (n ? ` · #${n}` : "") + " " }), e.action),
      h("span", { class: "t-ms", text: `${fmtMs(e.ms || 0)}${e.tokens_out ? " · " + fmtK(e.tokens_in + e.tokens_out) + " tok" : ""}` }),
    ));
    renderRoles();
  }
  function renderRoles() {
    const usage = S.stats?.usage?.by_role || {};
    const rows = ["fast", "judge", "reason", "tavily"].filter((k) => S.actors[k]).map((k) => {
      const a = S.actors[k];
      const nums = k === "tavily" ? `${a.calls} calls${a.credits ? " · " + a.credits.toFixed(1) + " credits" : ""}` : `${a.calls} calls · ${fmtK(a.tokens)} tokens`;
      return h("div", { class: "role" }, h("i", { class: `actor-${k}` }),
        h("span", {}, h("b", { text: ACTOR[k].name }), h("span", { class: "job", text: ACTOR[k].job })),
        h("span", { class: "nums", text: nums }));
    });
    const usd = usage && Object.values(usage).reduce((s, r) => s + (r.usd || 0), 0);
    const escalated = S.claims.filter((c) => (S.results[c.id]?.judged_by || []).includes("reason")).length;
    if (S.claims.length && Object.keys(S.results).length) {
      rows.push(h("div", { class: "totals-line", text:
        `Ultra reviewed ${escalated} of ${S.claims.length} claims; the rest were settled by Super.` +
        (S.stats ? ` Total ${S.stats.seconds}s${usd ? `, about $${usd.toFixed(3)} in model tokens` : ""}.` : "") }));
    }
    $("#roles").replaceChildren(...rows);
  }

  // ---------- export ----------
  function exportReport() {
    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
    const rows = S.claims.map((c) => {
      const r = S.results[c.id] || {};
      return `<tr><td>${S.byId[c.id].n}</td><td><strong>${esc(V[r.verdict]?.label || "Not checked")}</strong><br><small>${Math.round((r.confidence || 0) * 100)}% sure</small></td>
<td><blockquote>${esc(c.claim)}</blockquote><p><em>Cites:</em> ${esc(c.citation)}</p></td>
<td>${esc(r.explanation)}${r.evidence ? `<blockquote>${esc(r.evidence)}</blockquote><small>${r.evidence_verified ? "Quote verified in source" : "Quote not verified word-for-word"}</small>` : ""}
${r.source?.url ? `<p><a href="${esc(r.source.url)}">${esc(r.source.title || r.source.url)}</a></p>` : ""}
${r.corrected_claim ? `<p><em>Suggested rewrite:</em> ${esc(r.corrected_claim)}</p>` : ""}
<small>Checked by ${esc((r.judged_by || []).map((x) => ACTOR[x]?.name || x).join(", "))}</small></td></tr>`;
    }).join("\n");
    const s = S.summary || {};
    const edited = S.text !== S.originalText;
    const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Receipts report: ${esc(S.title)}</title><style>
body{font:15px/1.55 system-ui,sans-serif;color:#18223a;max-width:980px;margin:40px auto;padding:0 20px}h1{font-family:Georgia,serif;font-weight:500}
table{border-collapse:collapse;width:100%}td{border-top:1px solid #d9dde5;padding:10px 8px;vertical-align:top}blockquote{margin:4px 0;padding-left:10px;border-left:2px solid #d9dde5;font-family:Georgia,serif}
small{color:#6b7489}.sum{background:#f3f4f6;padding:14px 16px;border-radius:6px}pre{white-space:pre-wrap;font-family:Georgia,serif;background:#fafafa;padding:16px;border:1px solid #eee}</style></head><body>
<h1>Receipts report</h1><p><strong>${esc(S.title)}</strong><br><small>Checked ${new Date(S.recordedAt || Date.now()).toLocaleString()} with NVIDIA Nemotron 3 (Nano, Super, Ultra) on Nebius Token Factory and Tavily.${S.mode === "simulated" ? " SIMULATED RUN, NOT REAL RESULTS." : ""}</small></p>
<div class="sum"><strong>${esc(s.headline || "")}</strong> ${s.score !== null && s.score !== undefined ? `(${s.score}% of checkable citations hold up)` : ""}<p>${esc(s.summary || "")}</p>${(s.actions || []).length ? `<ol>${s.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>` : ""}</div>
<h2>Citations</h2><table>${rows}</table>
${edited ? `<h2>Revised document</h2><pre>${esc(S.text)}</pre>` : ""}
<p><small>Receipts gives evidence, not a ruling. Open each source before acting on a flag.</small></p></body></html>`;
    const blob = new Blob([html], { type: "text/html" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `receipts-${(S.title || "report").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)}.html` });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  init();
})();
