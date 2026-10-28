/* TENNIS ORACLE dashboard. All predictions are computed in-browser by engine.js from data/ratings.json
   (model version + params exported by the Python pipeline). No odds or market data are loaded anywhere. */
(function () {
  "use strict";
  const $ = s => document.querySelector(s), esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (x, d = 1) => (100 * x).toFixed(d) + "%";
  const TZ = "America/New_York";
  let ORACLE = null, RATINGS = null, BACKTEST = null, SLATES = { dates: [] }, LAST_SLATE = null;

  const todayNY = () => new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const fmtNY = iso => iso ? new Date(iso).toLocaleString("en-US", { timeZone: TZ, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " ET" : "";
  const getJSON = async u => { const r = await fetch(u, { cache: "no-store" }); if (!r.ok) throw new Error(u + " " + r.status); return r.json(); };
  const tick = () => new Promise(r => setTimeout(r, 0));

  /* ---------- tabs ---------- */
  document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
  function showTab(t) {
    document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
    document.querySelectorAll(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + t));
  }

  /* ---------- init ---------- */
  async function init() {
    const Q = new URLSearchParams(location.search);
    $("#slateDate").value = /^\d{4}-\d{2}-\d{2}$/.test(Q.get("date") || "") ? Q.get("date") : todayNY();
    try { RATINGS = await getJSON("data/ratings.json"); } catch (e) { $("#slateOut").innerHTML = `<div class="card warn">Could not load data/ratings.json: ${esc(e.message)}</div>`; return; }
    ORACLE = new TennisOracle.Oracle(RATINGS);
    const d = RATINGS.data;
    $("#badges").innerHTML = `<span class="badge acc">Model <b>${esc(RATINGS.model_version)}</b></span>
      <span class="badge">Ratings built <b>${esc((RATINGS.generated_at_utc || "").slice(0, 10))}</b></span>
      <span class="badge">Point-stats cutoff <b>${esc(d.atp.point_stats_cutoff)}</b></span>
      <span class="badge">Results through <b>${esc(d.atp.results_cutoff)}</b> ATP / <b>${esc(d.wta.results_cutoff)}</b> WTA</span>
      <span class="badge acc">No market data</span>`;
    $("#plist").innerHTML = RATINGS.players.filter(p => p.t === $("#mTour").value).map(p => `<option value="${esc(p.n)}">`).join("");
    $("#mTour").onchange = () => $("#plist").innerHTML = RATINGS.players.filter(p => p.t === $("#mTour").value).map(p => `<option value="${esc(p.n)}">`).join("");
    try { SLATES = await getJSON("data/slates.json"); } catch (e) { }
    $("#slateDate").onchange = loadSlateInfo; $("#slateTour").onchange = loadSlateInfo;
    if (Q.get("tour")) $("#slateTour").value = Q.get("tour");
    await loadSlateInfo();
    $("#simulateBtn").onclick = runSlate; $("#predictBtn").onclick = runSingle;
    renderAbout();
    if (Q.get("run") === "1") runSlate();
    getJSON("data/backtest.json").then(b => { BACKTEST = b; renderBacktest(); }).catch(e => $("#btOut").innerHTML = `<div class="card warn">No backtest file: ${esc(e.message)}</div>`);
  }

  let SLATE_FILE = null;
  async function loadSlateInfo() {
    const day = $("#slateDate").value; SLATE_FILE = null;
    try {
      SLATE_FILE = await getJSON(`data/slate-${day}.json`);
      const ms = filtTour(SLATE_FILE.matches), sched = ms.filter(m => m.status === "STATUS_SCHEDULED").length;
      $("#slateInfo").innerHTML = `Schedule for <b>${day}</b> (${TZ}): <b>${ms.length}</b> singles matches (${sched} not yet started) from ${esc(SLATE_FILE.source)}.
        Fetched ${fmtNY(SLATE_FILE.generated_at_utc)}. Frozen server-side forecasts: model ${esc(SLATE_FILE.model_version)}.`;
    } catch (e) {
      $("#slateInfo").innerHTML = `No prepared schedule file for <b>${day}</b> yet (the GitHub Action fetches today and tomorrow every few hours). Available: ${(SLATES.dates || []).slice(-10).join(", ") || "none"}. Use manual matchups below.`;
    }
  }
  const filtTour = ms => { const t = $("#slateTour").value; return t ? ms.filter(m => m.tour === t) : ms; };

  /* ---------- manual entry ---------- */
  function parseManual(text) {
    const out = [], errs = [];
    for (const raw of text.split("\n")) {
      const line = raw.trim(); if (!line || line.startsWith("#")) continue;
      const parts = line.split(/[,;\t]|\s+vs\.?\s+/i).map(s => s.trim()).filter(Boolean);
      if (parts.length < 2) { errs.push(line); continue; }
      const surfRaw = (parts[2] || "hard").toLowerCase(), surface = surfRaw.startsWith("c") ? "Clay" : surfRaw.startsWith("g") ? "Grass" : "Hard";
      const bo = parts[3] === "5" ? 5 : 3, tourIn = (parts[4] || "").toLowerCase(), fr = (parts[5] || (bo === 5 ? "tb10" : "tb7")).toLowerCase();
      const tours = tourIn ? [tourIn] : ["atp", "wta"];
      let A = null, B = null, tour = null;
      for (const t of tours) { A = ORACLE.find(parts[0], t); B = ORACLE.find(parts[1], t); if (A && B) { tour = t; break; } }
      if (!A || !B) { errs.push(`${line} — player not found in ratings (${!A ? parts[0] : parts[1]})`); continue; }
      out.push({ manual: true, tour, tournament: "Manual entry", round: "", status: "MANUAL", players: [{ name: A.n, id: A.id }, { name: B.n, id: B.id }],
        surface, surface_source: "manual", indoor: /indoor/i.test(line), best_of: bo, final_rule: ["tb7", "tb10", "adv"].includes(fr) ? fr : "tb7" });
    }
    return [out, errs];
  }

  /* ---------- slate simulation ---------- */
  async function runSlate() {
    const btn = $("#simulateBtn"), day = $("#slateDate").value, nS = +$("#slateSims").value;
    btn.disabled = true; btn.textContent = "Simulating…";
    const [manual, merrs] = parseManual($("#manualText").value);
    let list = [];
    if (!$("#manualOnly").checked && SLATE_FILE) list = filtTour(SLATE_FILE.matches).filter(m => $("#slateIncludeDone").checked || m.status === "STATUS_SCHEDULED");
    list = list.concat(manual);
    const prog = $("#progress"), bar = prog.firstElementChild; prog.classList.remove("hidden");
    const rows = [], skipped = merrs.map(e => ({ why: e }));
    for (let i = 0; i < list.length; i++) {
      const m = list[i]; bar.style.width = (100 * i / list.length) + "%"; await tick();
      let [pa, pb] = m.players;
      let A = pa.id && ORACLE.byId.get(pa.id), B = pb.id && ORACLE.byId.get(pb.id);
      if (!A) A = ORACLE.find(pa.name, m.tour); if (!B) B = ORACLE.find(pb.name, m.tour);
      if (!A || !B) { skipped.push({ m, why: `no rating history for ${!A ? pa.name : pb.name}` }); continue; }
      const f = ORACLE.forecast({ a: A.id, b: B.id, tour: m.tour, surface: m.surface, bestOf: m.best_of, finalRule: m.final_rule, indoor: !!m.indoor,
        date: day + "T12:00:00", sims: nS, skipAnalytic: true, tournament: m.tournament, round: m.round });
      rows.push({ m, f });
    }
    bar.style.width = "100%"; setTimeout(() => prog.classList.add("hidden"), 400);
    btn.disabled = false; btn.textContent = "▶ Simulate";
    LAST_SLATE = { day, rows, skipped, ranAt: new Date().toISOString(), nS };
    const ver = freezeLocal(LAST_SLATE);
    renderSlate(LAST_SLATE, ver);
  }

  function slim(r) {
    const f = r.f, m = r.m;
    return { espn_id: m.espn_id || null, tournament: m.tournament, round: m.round, tour: m.tour, start_utc: m.start_utc || null,
      players: { A: f.A.n, B: f.B.n }, surface: f.surface, surface_source: m.surface_source, best_of: f.best_of, final_rule: f.final_rule,
      predicted_winner: f.predicted_winner, win_prob: { A: +f.win_prob[0].toFixed(4), B: +f.win_prob[1].toFixed(4) },
      fair_odds: { A: f.fair_odds[0], B: f.fair_odds[1] }, most_likely_set_score: f.most_likely_set_score,
      set_score_probs: Object.fromEntries(f.set_score_probs.map(([k, v]) => [k, +v.toFixed(4)])),
      final_score: f.final_score, games: { mean: +f.games.mean.toFixed(2), median: f.games.median, p10: f.games.p10, p90: f.games.p90, sd: +f.games.sd.toFixed(2) },
      p_tiebreak: +f.p_tiebreak.toFixed(4), p_decider: +f.p_decider.toFixed(4), strongest: { selection: f.strongest.selection, probability: +f.strongest.probability.toFixed(4), confidence: f.strongest.confidence },
      data_quality: f.data_quality, data_quality_notes: f.data_quality_notes };
  }
  function freezeLocal(S) {
    // browser-side freeze: append-only versions in localStorage; earlier versions are never modified
    const key = "oracle-frozen-" + S.day; let arr = [];
    try { arr = JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { }
    const rec = { version: arr.length + 1, frozen_at_utc: S.ranAt, model_version: ORACLE.version, seed: ORACLE.P.mc.seed, n_sims: S.nS,
      data_cutoff: RATINGS.data, matches: S.rows.filter(r => r.m.status === "STATUS_SCHEDULED" || r.m.manual).map(slim) };
    arr.push(rec); try { localStorage.setItem(key, JSON.stringify(arr)); } catch (e) { }
    S.frozen = rec; return rec.version;
  }

  function resultCell(m, f) {
    if (!m.result || !m.result.sets) return m.status && m.status !== "STATUS_SCHEDULED" && m.status !== "MANUAL" ? `<span class="muted">${esc(m.status.replace("STATUS_", "").toLowerCase())}</span>` : "";
    const w = m.result.winner, sc = m.result.sets.map(s => w === 0 ? `${s[0]}-${s[1]}` : `${s[1]}-${s[0]}`).join(" ");
    const wn = m.players[w].name, ok = wn === m.players[f.win_prob[0] >= 0.5 ? 0 : 1].name;
    return `<span class="${ok ? "hit" : "miss"}">${ok ? "✓" : "✗"}</span> ${esc(wn)} <span class="score">${sc}</span>`;
  }

  function renderSlate(S, ver) {
    const out = $("#slateOut");
    if (!S.rows.length) { out.innerHTML = `<div class="card warn">No matches to simulate for ${S.day}. ${S.skipped.length ? "Skipped: " + S.skipped.map(s => esc(s.why)).join("; ") : "Tick “include started/finished” or add manual matchups."}</div>`; return; }
    const order = [...S.rows].sort((a, b) => Math.max(...b.f.win_prob) - Math.max(...a.f.win_prob));
    const confRank = { High: 3, Medium: 2, Low: 1 };
    const strong = [...S.rows].sort((a, b) => (confRank[b.f.strongest.confidence] - confRank[a.f.strongest.confidence]) || (b.f.strongest.probability - a.f.strongest.probability));
    let h = `<div class="card"><h2>Slate ${S.day} — ${S.rows.length} matches simulated with model ${esc(ORACLE.version)}</h2>
      <p class="muted">${S.nS.toLocaleString()} Monte Carlo sims per match, seed ${ORACLE.P.mc.seed}. Run ${fmtNY(S.ranAt)}. Browser freeze v${ver} saved locally (append-only).
      The official pre-match freeze is written by the Python pipeline to <code>forecasts/${S.day}/</code> in the repo.</p>
      <button class="small" id="dlSlate">Download frozen slate JSON</button> <button class="small" id="copyList">Copy vertical score list</button></div>`;
    // match cards
    h += `<div class="grid2">` + S.rows.map((r, i) => {
      const f = r.f, m = r.m, aFav = f.win_prob[0] >= 0.5, fs = f.final_score;
      const fz = m.forecast ? `<div class="muted">Frozen pre-match (Python ${esc(m.forecast.model_version)} v${m.forecast.frozen_version}): ${esc(m.forecast.predicted_winner)} ${pct(Math.max(m.forecast.win_prob.A, m.forecast.win_prob.B))}, ${esc(m.forecast.final_score.sets.join(" "))}</div>` : "";
      return `<div class="card"><div class="muted">${esc(m.tour.toUpperCase())} · ${esc(m.tournament)}${m.round ? " · " + esc(m.round) : ""} · ${esc(f.surface)}${m.surface_source === "assumed" ? " (assumed)" : ""} · Bo${f.best_of}${m.start_utc ? " · " + fmtNY(m.start_utc) : ""}</div>
        <div class="mcard"><div><div class="pname ${aFav ? "fav" : ""}">${esc(f.A.n)}</div><div class="pct ${aFav ? "fav" : ""}">${pct(f.win_prob[0])}</div></div>
        <div class="muted">vs</div><div style="text-align:right"><div class="pname ${!aFav ? "fav" : ""}">${esc(f.B.n)}</div><div class="pct ${!aFav ? "fav" : ""}">${pct(f.win_prob[1])}</div></div></div>
        <div class="bar2"><span style="width:${100 * f.win_prob[0]}%;background:var(--acc)"></span><span style="flex:1;background:var(--acc2)"></span></div>
        <p><b>Predicted winner:</b> ${esc(f.predicted_winner)} · <b>Final score:</b> <span class="score">${fs.A}-${fs.B} (${esc(fs.sets.join(", "))})</span>
        <br><b>Most likely sets:</b> ${esc(f.most_likely_set_score)} (${pct(f.set_score_probs[0][1])}) · <b>Games:</b> ${f.games.mean.toFixed(1)} · <b>TB:</b> ${pct(f.p_tiebreak, 0)}
        <br><b>Strongest:</b> ${esc(f.strongest.selection)} ${pct(f.strongest.probability)} <span class="conf ${f.strongest.confidence}">${f.strongest.confidence}</span> · <span class="dq ${f.data_quality}">${f.data_quality}</span></p>
        ${fz}<div>${resultCell(m, f)}</div><button class="small" data-open="${i}">Full report →</button></div>`;
    }).join("") + `</div>`;
    // summary table
    h += `<div class="card"><h2>Multi-match summary</h2><div style="overflow:auto"><table><tr><th>Match</th><th>Predicted winner</th><th class="num">Win %</th><th>Most likely sets</th><th>Final score</th><th class="num">Exp. games</th><th>Strongest prediction</th><th>Conf.</th><th>DQ</th><th>Result</th></tr>` +
      S.rows.map((r, i) => { const f = r.f; return `<tr class="click" data-open="${i}"><td>${esc(f.A.n)} vs ${esc(f.B.n)}</td><td>${esc(f.predicted_winner)}</td><td class="num">${pct(Math.max(...f.win_prob))}</td>
        <td>${esc(f.most_likely_set_score)}</td><td class="score">${esc(f.final_score.sets.join(" "))}</td><td class="num">${f.games.mean.toFixed(1)}</td><td>${esc(f.strongest.selection)} (${pct(f.strongest.probability)})</td>
        <td class="conf ${f.strongest.confidence}">${f.strongest.confidence}</td><td><span class="dq ${f.data_quality}">${f.data_quality}</span></td><td>${resultCell(r.m, f)}</td></tr>`; }).join("") + `</table></div></div>`;
    // rankings + vertical list
    h += `<div class="grid2"><div class="card"><h2>Ranking by winner probability</h2><table>` + order.map((r, k) => `<tr><td>${k + 1}</td><td>${esc(r.f.predicted_winner)}</td><td class="num">${pct(Math.max(...r.f.win_prob))}</td></tr>`).join("") + `</table>
      <h3>Strongest-prediction ranking</h3><table>` + strong.slice(0, 15).map((r, k) => `<tr><td>${k + 1}</td><td>${esc(r.f.strongest.selection)}</td><td class="num">${pct(r.f.strongest.probability)}</td><td class="conf ${r.f.strongest.confidence}">${r.f.strongest.confidence}</td></tr>`).join("") + `</table></div>
      <div class="card"><h2>Vertical score list</h2><pre id="vlist" class="score">${esc(verticalList(S))}</pre></div></div>`;
    if (S.skipped.length) h += `<div class="card warn">Skipped: ${S.skipped.map(s => esc(s.m ? `${s.m.players[0].name} vs ${s.m.players[1].name}: ${s.why}` : s.why)).join("<br>")}</div>`;
    out.innerHTML = h;
    out.querySelectorAll("[data-open]").forEach(el => el.onclick = () => { const r = S.rows[+el.dataset.open]; renderMatch(r.f, $("#matchOut")); prefill(r.f); showTab("match"); window.scrollTo(0, 0); });
    $("#dlSlate").onclick = () => download(`oracle-slate-${S.day}-browser-v${S.frozen.version}.json`, S.frozen);
    $("#copyList").onclick = () => navigator.clipboard && navigator.clipboard.writeText(verticalList(S));
  }
  function verticalList(S) {
    return S.rows.map(r => { const f = r.f, fs = f.final_score, aW = fs.A > fs.B, w = aW ? f.A.n : f.B.n, l = aW ? f.B.n : f.A.n;
      const sets = fs.sets.map(s => { const [x, y] = s.split("-"); return aW ? `${x}-${y}` : `${y}-${x}`; });
      return `${w} d. ${l}  ${sets.join(" ")}  (${pct(Math.max(...f.win_prob), 0)})`; }).join("\n");
  }
  function download(name, obj) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 1)], { type: "application/json" })); a.download = name; a.click(); }
  function prefill(f) { $("#mTour").value = f.tour; $("#mA").value = f.A.n; $("#mB").value = f.B.n; $("#mSurf").value = f.surface; $("#mBo").value = f.best_of; $("#mFr").value = f.final_rule; $("#mIndoor").checked = f.indoor; $("#mTourn").value = f.tournament; $("#mRound").value = f.round; }

  /* ---------- single match ---------- */
  async function runSingle() {
    const tour = $("#mTour").value, A = ORACLE.find($("#mA").value, tour), B = ORACLE.find($("#mB").value, tour), out = $("#matchOut");
    if (!A || !B) { out.innerHTML = `<div class="card warn">Player not found in ${tour.toUpperCase()} ratings: ${esc(!A ? $("#mA").value : $("#mB").value)}</div>`; return; }
    if (A.id === B.id) { out.innerHTML = `<div class="card warn">Pick two different players.</div>`; return; }
    const btn = $("#predictBtn"); btn.disabled = true; btn.textContent = "Running…"; await tick();
    const f = ORACLE.forecast({ a: A.id, b: B.id, tour, surface: $("#mSurf").value, bestOf: +$("#mBo").value, finalRule: $("#mFr").value, indoor: $("#mIndoor").checked,
      sims: Math.max(100000, +$("#mSims").value || 100000), seed: +$("#mSeed").value, aAdj: +$("#mAadj").value || 0, bAdj: +$("#mBadj").value || 0,
      injuryUncertain: $("#mInj").checked, tournament: $("#mTourn").value, round: $("#mRound").value, date: todayNY() + "T12:00:00" });
    btn.disabled = false; btn.textContent = "Predict"; renderMatch(f, out);
  }

  function renderMatch(f, out) {
    const A = f.A.n, B = f.B.n, m = f.model, g = f.games;
    const ana = f.analytic, xc = ana ? `MC vs exact Markov mixture: win ${pct(f.win_prob[0], 2)} vs ${pct(ana.pWin, 2)}, tiebreak ${pct(f.p_tiebreak, 2)} vs ${pct(ana.pTb, 2)}, games ${g.mean.toFixed(2)} vs ${ana.gamesMean.toFixed(2)} (MC s.e. ≈ ${(100 * Math.sqrt(f.win_prob[0] * f.win_prob[1] / m.n_sims)).toFixed(2)} pp)` : "Analytic cross-check skipped for slate speed — run from Single Match for the cross-check.";
    const s = f.strongest, fs = f.final_score;
    let h = `<div class="card"><h2>MATCH: ${esc(A)} vs ${esc(B)}</h2><div class="kv">
      <div>Tournament</div><div>${esc(f.tournament || "(not specified)")}</div><div>Round</div><div>${esc(f.round || "(not specified)")}</div>
      <div>Surface</div><div>${esc(f.surface)}${f.indoor ? " (indoor)" : ""}</div><div>Format</div><div>${esc(f.format)}</div>
      <div>Data cutoff</div><div>${esc(f.data_cutoff)} (point-level stats); results through ${esc(f.results_cutoff)}</div>
      <div>Data quality</div><div><span class="dq ${f.data_quality}">${f.data_quality}</span> ${f.data_quality_notes.map(esc).join("; ")}</div>
      <div>Model</div><div>${esc(m.version)} · ${m.n_sims.toLocaleString()} sims · seed ${m.seed} · composite Elo diff ${m.composite_elo_diff >= 0 ? "+" : ""}${m.composite_elo_diff.toFixed(0)} · σ ${m.sigma}, ρ ${m.rho}</div>
      <div>Cross-check</div><div>${xc}</div></div></div>`;
    h += `<div class="grid2"><div class="card"><h3>1. Match winner prediction</h3><p>Predicted winner: <b class="fav">${esc(f.predicted_winner)}</b></p>
      <table><tr><th>Player</th><th class="num">Win %</th><th class="num">Fair decimal</th><th class="num">Fair American</th></tr>
      <tr><td>${esc(A)}</td><td class="num">${pct(f.win_prob[0])}</td><td class="num">${f.fair_odds[0].decimal.toFixed(2)}</td><td class="num">${f.fair_odds[0].american}</td></tr>
      <tr><td>${esc(B)}</td><td class="num">${pct(f.win_prob[1])}</td><td class="num">${f.fair_odds[1].decimal.toFixed(2)}</td><td class="num">${f.fair_odds[1].american}</td></tr></table>
      <h3>2. Exact set-score probabilities</h3>${setBars(f)}
      <p>Most likely exact outcome: ${esc(A)} ${esc(f.most_likely_set_score)} ${esc(B)}</p>
      <h3>3. Projected individual set scores</h3><p>Representative sequence for ${esc(f.most_likely_set_score)}: <span class="score">${esc(f.representative_sets.join(", "))}</span>
      <span class="muted">(most frequent sequence, ${pct(f.representative_p)} of sims — representative, not definitive)</span></p></div>
      <div class="card"><h3>4. Expected total games</h3><p>Mean <b>${g.mean.toFixed(1)}</b> · Median <b>${g.median}</b> · 10th–90th <b>${g.p10}–${g.p90}</b> · SD <b>${g.sd.toFixed(1)}</b></p>${gamesChart(g)}
      <h3>7. Tiebreak and set probabilities</h3><div class="kv">
      <div>At least one tiebreak</div><div>${pct(f.p_tiebreak)}</div><div>${esc(A)} wins a set</div><div>${pct(f.p_a_set)}</div><div>${esc(B)} wins a set</div><div>${pct(f.p_b_set)}</div>
      <div>Deciding set</div><div>${pct(f.p_decider)}</div><div>Expected breaks</div><div>${f.breaks.total.toFixed(2)} (${esc(A)} ${f.breaks.A.toFixed(2)}, ${esc(B)} ${f.breaks.B.toFixed(2)})</div>
      <div>Break points created</div><div>${esc(A)} ${f.bp.A.toFixed(1)}, ${esc(B)} ${f.bp.B.toFixed(1)}</div>
      <div>Serve-point win (model)</div><div>${esc(A)} ${pct(m.point_probs[0])} · ${esc(B)} ${pct(m.point_probs[1])}; hold ${pct(m.hold[0])} / ${pct(m.hold[1])}</div></div></div></div>`;
    h += `<div class="grid2"><div class="card"><h3>5. Game-total probabilities</h3><table><tr><th>Line</th><th class="num">Over</th><th class="num">Under</th></tr>` +
      f.over_under.map(o => `<tr><td>${o.line}</td><td class="num">${pct(o.over)}</td><td class="num">${pct(o.under)}</td></tr>`).join("") + `</table></div>
      <div class="card"><h3>6. Game-handicap probabilities</h3><table><tr><th>Line</th><th class="num">${esc(A)} −</th><th class="num">${esc(B)} +</th><th class="num">${esc(B)} −</th><th class="num">${esc(A)} +</th></tr>` +
      f.handicaps.map(x => `<tr><td>${x.line}</td><td class="num">${pct(x.Aminus)}</td><td class="num">${pct(x.Bplus)}</td><td class="num">${pct(x.Bminus)}</td><td class="num">${pct(x.Aplus)}</td></tr>`).join("") + `</table>
      <p class="muted">Handicaps on total games won (A − B). Fair odds = 1/p; these are model prices, not sportsbook prices.</p></div></div>`;
    const prof = (p, k) => p.st && p.st[k] != null ? pct(p.st[k]) : "—";
    const rows = [["1st serve in", "first_in"], ["1st serve pts won", "first_won"], ["2nd serve pts won", "second_won"], ["Hold %", "hold"], ["Break points saved", "bp_saved"], ["Return pts won", "ret_pts_won"], ["Return games won", "ret_games_won"], ["BP converted", "bp_conv"]];
    h += `<div class="card"><h3>8. Matchup assessment</h3><ul>` + f.drivers.map(d => `<li>[statistical] ${esc(d.label)}: favours <b>${esc(d.favours)}</b> (${d.pts >= 0 ? "+" : ""}${d.pts.toFixed(0)} Elo pts weighted; raw ${d.raw >= 0 ? "+" : ""}${d.raw.toFixed(0)})</li>`).join("") +
      `<li>[descriptive] Elo overall ${f.A.e.toFixed(0)} vs ${f.B.e.toFixed(0)}; ${esc(f.surface)} Elo ${f.A.se[TennisOracle.SURF.indexOf(f.surface)].toFixed(0)} vs ${f.B.se[TennisOracle.SURF.indexOf(f.surface)].toFixed(0)}; last-10 ${f.A.l10[0]}-${f.A.l10[1]} vs ${f.B.l10[0]}-${f.B.l10[1]}; 52-week ${f.A.l52[0]}-${f.A.l52[1]} vs ${f.B.l52[0]}-${f.B.l52[1]}</li>
      <li>[qualitative] Fatigue, injury, weather and conditions are not inferred; enter verified facts as context adjustments (currently ${f.context.aAdj}/${f.context.bAdj} Elo${f.context.injuryUncertain ? ", injury flag on" : ""}).</li></ul>
      <table><tr><th>52-week stat</th><th class="num">${esc(A)}</th><th class="num">${esc(B)}</th></tr>` + rows.map(([l, k]) => `<tr><td>${l}</td><td class="num">${prof(f.A, k)}</td><td class="num">${prof(f.B, k)}</td></tr>`).join("") + `</table>
      <p class="muted">Stats window ends at each player's latest point-level record (${esc(f.A.ls || "—")} / ${esc(f.B.ls || "—")}).</p></div>`;
    h += `<div class="grid2"><div class="card"><h3>9. Strongest prediction</h3><div class="kv"><div>Highest-confidence outcome</div><div><b>${esc(s.selection)}</b></div>
      <div>Modeled probability</div><div>${pct(s.probability)}</div><div>Fair mathematical odds</div><div>${s.fair_odds.decimal.toFixed(2)} / ${s.fair_odds.american} <span class="muted">(model price; not a proven +EV bet)</span></div>
      <div>Confidence</div><div class="conf ${s.confidence}">${s.confidence}</div><div>Main risk</div><div>${esc(s.main_risk)}</div></div></div>
      <div class="card"><h3>10. Final score prediction</h3><p class="pct">${esc(A.toUpperCase())} ${fs.A} – ${esc(B.toUpperCase())} ${fs.B}</p>
      <p>Projected set scores: <span class="score">${esc(fs.sets.join(", "))}</span></p><button class="small" id="dlMatch">Download forecast JSON</button></div></div>`;
    out.innerHTML = h;
    $("#dlMatch") && ($("#dlMatch").onclick = () => { const o = JSON.parse(JSON.stringify(f)); o.frozen_at_utc = new Date().toISOString(); delete o.games.pmf; delete o.diff_pmf; download(`oracle-${f.A.n}-vs-${f.B.n}.json`.replace(/\s+/g, "_"), o); });
  }

  function setBars(f) {
    const W = 420, rh = 22, items = f.set_score_probs, mx = Math.max(...items.map(x => x[1]));
    return `<svg viewBox="0 0 ${W} ${items.length * rh + 4}" width="100%">` + items.map(([k, v], i) => {
      const [a, b] = k.split("-").map(Number), w = (W - 190) * v / mx, col = a > b ? "var(--acc)" : "var(--acc2)";
      return `<text x="0" y="${i * rh + 15}">${esc(f.A.n.split(" ").slice(-1)[0])} ${k} ${esc(f.B.n.split(" ").slice(-1)[0])}</text><rect x="130" y="${i * rh + 4}" width="${w}" height="${rh - 8}" rx="3" fill="${col}"/><text x="${136 + w}" y="${i * rh + 15}">${pct(v)}</text>`;
    }).join("") + `</svg>`;
  }
  function gamesChart(g) {
    const ks = Object.keys(g.pmf).map(Number).sort((a, b) => a - b).filter(k => g.pmf[k] > 2e-4), W = 460, H = 150, pad = 22;
    const lo = ks[0], hi = ks[ks.length - 1], mx = Math.max(...ks.map(k => g.pmf[k])), bw = (W - pad) / (hi - lo + 1);
    let s = `<svg viewBox="0 0 ${W} ${H + 18}" width="100%">`;
    for (const k of ks) { const h = (H - 10) * g.pmf[k] / mx, x = pad + (k - lo) * bw; s += `<rect x="${x}" y="${H - h}" width="${Math.max(1, bw - 1)}" height="${h}" fill="${k >= g.p10 && k <= g.p90 ? "var(--acc2)" : "#3a4670"}"><title>${k} games: ${pct(g.pmf[k], 2)}</title></rect>`; }
    for (let k = Math.ceil(lo / 5) * 5; k <= hi; k += 5) s += `<text x="${pad + (k - lo) * bw}" y="${H + 14}">${k}</text>`;
    const mxPos = pad + (g.mean - lo) * bw; s += `<line x1="${mxPos}" x2="${mxPos}" y1="0" y2="${H}" stroke="var(--acc)" stroke-dasharray="3 3"/><text x="${mxPos + 3}" y="10">mean ${g.mean.toFixed(1)}</text>`;
    return s + `</svg><p class="muted">Total-games distribution from the simulations (highlighted: 10th–90th percentile).</p>`;
  }

  /* ---------- backtest ---------- */
  function renderBacktest() {
    const b = BACKTEST, v = b.promoted === "v1.1" ? "v1.1-candidate" : "v1.0", T = b.test, V = b.validation, f4 = x => x.toFixed(4);
    const row = (name, w) => `<tr><td>${name}</td><td class="num">${w.n}</td><td class="num">${f4(w.log_loss)}</td><td class="num">${f4(w.brier)}</td><td class="num">${pct(w.accuracy)}</td></tr>`;
    let h = `<div class="card"><h2>Walk-forward backtest (chronological, no look-ahead)</h2>
      <p class="muted">Ratings are updated match by match; every prediction uses only matches completed before it. Train ${b.counts.train.toLocaleString()} · validation ${b.counts.validation.toLocaleString()} (2024) · test ${b.counts.test.toLocaleString()} (2025 – ${esc(b.data.atp.results_cutoff)}). Promoted model: <b>${esc(b.promoted)}</b> (chosen on validation only).</p>
      <div class="grid2"><div><h3>Test set — match winner</h3><table><tr><th>Model</th><th class="num">n</th><th class="num">Log loss</th><th class="num">Brier</th><th class="num">Accuracy</th></tr>
      ${row("Oracle v1.0 (spec weights)", T["v1.0"].winner)}${row("Oracle v1.1 (refit weights)", T["v1.1-candidate"].winner)}${row("Plain surface Elo baseline", T["v1.0"].surface_elo_baseline)}${row("538-style blended Elo", T["v1.0"].blend_elo_baseline)}</table>
      <h3>Validation 2024</h3><table><tr><th>Model</th><th class="num">n</th><th class="num">Log loss</th><th class="num">Brier</th><th class="num">Accuracy</th></tr>
      ${row("Oracle v1.0", V["v1.0"].winner)}${row("Oracle v1.1", V["v1.1-candidate"].winner)}${row("Surface Elo", V["v1.0"].surface_elo_baseline)}</table>
      <h3>Score-model checks (test, ${esc(b.promoted)})</h3><div class="kv"><div>Total games MAE</div><div>${T[v].games_mae.toFixed(2)} (naive mean: ${T[v].games_mae_naive_mean.toFixed(2)})</div>
      <div>Tiebreak Brier</div><div>${f4(T[v].tiebreak_brier)} (base rate: ${f4(T[v].tiebreak_base_rate_brier)})</div><div>Exact set-score accuracy</div><div>${pct(T[v].exact_set_score_accuracy)}</div></div></div>
      <div><h3>Calibration (test, ${esc(b.promoted)})</h3>${relChart(T[v].calibration)}</div></div></div>`;
    h += `<div class="card"><h3>By tour / surface (test, ${esc(b.promoted)})</h3><table><tr><th>Segment</th><th class="num">n</th><th class="num">Oracle log loss</th><th class="num">Surface-Elo log loss</th><th class="num">Oracle acc.</th><th class="num">Games MAE</th></tr>` +
      Object.entries(T[v].by_tour_surface).map(([k, x]) => `<tr><td>${esc(k)}</td><td class="num">${x.oracle.n}</td><td class="num">${f4(x.oracle.log_loss)}</td><td class="num">${f4(x.surface_elo.log_loss)}</td><td class="num">${pct(x.oracle.accuracy)}</td><td class="num">${x.games_mae != null ? x.games_mae.toFixed(2) : "—"}</td></tr>`).join("") +
      Object.entries(T[v].by_source).map(([k, x]) => `<tr><td>source: ${esc(k)}</td><td class="num">${x.oracle.n}</td><td class="num">${f4(x.oracle.log_loss)}</td><td class="num">${f4(x.surface_elo.log_loss)}</td><td class="num">${pct(x.oracle.accuracy)}</td><td class="num">—</td></tr>`).join("") +
      `</table><p class="muted">ESPN-sourced test matches (after the Sackmann cutoff) have scores only; serve/return ratings are frozen at the last point-level data, so this segment shows how the model degrades without fresh serve stats.</p></div>`;
    h += `<div class="card"><h3>Calibration table</h3><table><tr><th>Bucket</th><th class="num">n</th><th class="num">Mean predicted</th><th class="num">Observed</th></tr>` + T[v].calibration.map(c => `<tr><td>${c.bucket}</td><td class="num">${c.n}</td><td class="num">${pct(c.mean_pred)}</td><td class="num">${pct(c.observed)}</td></tr>`).join("") + `</table>
      <p class="muted">Per-match variation fitted from data: σ=${b.variation_fit.sigma}, ρ=${b.variation_fit.rho}. Live post-match audit of frozen forecasts: <code>forecasts/audit.json</code> (updated by the daily Action).</p><div id="liveAudit"></div></div>`;
    $("#btOut").innerHTML = h;
    getJSON("data/audit.json").then(a => { if (!a || !a.summary || !a.summary.n) return;
      const s = a.summary; $("#liveAudit").innerHTML = `<h3>Live audit of frozen pre-match forecasts</h3><div class="kv"><div>Settled forecasts</div><div>${s.n}</div><div>Accuracy</div><div>${pct(s.accuracy)}</div><div>Brier</div><div>${f4(s.brier)}</div><div>Log loss</div><div>${f4(s.log_loss)}</div><div>Games MAE</div><div>${s.games_mae != null ? s.games_mae.toFixed(2) : "—"}</div></div><p class="muted">Updated ${fmtNY(a.generated_at_utc)}</p>`; }).catch(() => { });
  }
  function relChart(cal) {
    const W = 300, P = 30, S = W - P - 8, x = v => P + (v - 0.5) / 0.5 * S, y = v => 8 + S - (v - 0.5) / 0.5 * S;
    let s = `<svg viewBox="0 0 ${W} ${W}" width="100%" style="max-width:360px"><rect x="${P}" y="8" width="${S}" height="${S}" fill="none" stroke="#2a3557"/><line x1="${x(.5)}" y1="${y(.5)}" x2="${x(1)}" y2="${y(1)}" stroke="#55628a" stroke-dasharray="4 3"/>`;
    for (const t of [.5, .6, .7, .8, .9, 1]) s += `<text x="${x(t) - 8}" y="${W - 6}">${t * 100}</text><text x="2" y="${y(t) + 3}">${t * 100}</text>`;
    s += `<polyline fill="none" stroke="var(--acc)" stroke-width="2" points="${cal.map(c => `${x(c.mean_pred)},${y(c.observed)}`).join(" ")}"/>`;
    for (const c of cal) s += `<circle cx="${x(c.mean_pred)}" cy="${y(c.observed)}" r="${3 + Math.sqrt(c.n) / 12}" fill="var(--acc)" fill-opacity=".6"><title>${c.bucket}: predicted ${pct(c.mean_pred)}, observed ${pct(c.observed)} (n=${c.n})</title></circle>`;
    return s + `</svg><p class="muted">Favourite's predicted win % (x) vs observed (y). Dashed = perfect calibration.</p>`;
  }

  function renderAbout() {
    const P = ORACLE.P, w = P.weights;
    $("#aboutOut").innerHTML = `<h2>How TENNIS ORACLE works</h2>
    <p><b>Inputs (independent statistics only).</b> Match results and point-level serve/return statistics from Jeff Sackmann's tennis_atp / tennis_wta datasets (via an archival mirror, since the upstream repositories are no longer online), plus completed scores from ESPN's public scoreboard after that dataset ends. No betting odds, market prices, consensus picks, tipster or media predictions are used in any form.</p>
    <p><b>Ratings.</b> Overall and surface Elo (FiveThirtyEight-style decaying K), opponent-adjusted serve and return ratings in logit space shrunk toward tour/surface baselines, and opponent-adjusted recent form. The composite rating difference uses weights (model ${esc(ORACLE.version)}): surface Elo ${w.surface_elo}, serve/return ${w.serve_return}, overall Elo ${w.overall_elo}, form ${w.form}, verified context ≤ ±${P.context_max_elo} Elo. ${esc(ORACLE.version)} weights were refit on 2014–2023 and promoted only because they beat the spec weights on 2024 validation.</p>
    <p><b>Point model.</b> Serve-point probabilities are calibrated so the match-win probability equals the composite forecast while keeping each player's serve/return style, then fed to an exact Markov model of games, tiebreaks (correct serve rotation), sets and matches (Bo3/Bo5, 7-point, 10-point or advantage deciding sets), and to a point-by-point Monte Carlo simulation (≥100,000 sims, fixed seed ${P.mc.seed}) with per-match random variation in serve level (σ=${P.variation.sigma}, ρ=${P.variation.rho}, fitted from data).</p>
    <p><b>Daily schedule.</b> A GitHub Action fetches fixtures for today and tomorrow (America/New_York) from ESPN's public tennis scoreboard API, which carries fixtures and scores only; any odds-like field is stripped defensively. It refreshes ratings with newly completed results, re-exports this dashboard's ratings, freezes pre-match forecasts in <code>forecasts/</code> (append-only, versioned, timestamped), and audits settled ones.</p>
    <h3>Limitations</h3><ul>
    <li>Point-level serve/return statistics end at ${esc(RATINGS.data.atp.point_stats_cutoff)}; results after that update Elo and form only, so data quality is capped at MEDIUM.</li>
    <li>Fatigue, injuries, weather, altitude, ball type and court speed are <b>not</b> inferred. Enter only verified facts as manual context adjustments.</li>
    <li>ESPN does not publish the surface; it is inferred from tournament history, Grand Slam identity or venue. "assumed" means it fell back to Hard.</li>
    <li>Players with no history in the ratings file cannot be simulated and are listed as skipped.</li>
    <li>Fair odds are 1/probability from this model. They are not sportsbook prices and are not evidence of value.</li></ul>`;
  }
  init();
})();
