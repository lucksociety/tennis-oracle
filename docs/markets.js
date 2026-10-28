/* Tennis Oracle - Jupiter prediction-market comparison (shared by the dashboard, the Discord Worker and
 * mirrored exactly by tennis_oracle/jupiter.py; parity tested in tests/test_markets.py).
 * Market prices NEVER feed the model: this only reads an already-frozen forecast distribution and
 * compares it with prices fetched afterwards.
 *   cost (implied prob incl. fee) = p + 0.07 * p * (1 - p)   (Jupiter taker fee schedule, per contract)
 *   edge = model_p - cost ;  EV per $1 staked = model_p / cost - 1
 *   best bet = highest-EV outcome with model_p > 0.5 AND edge > 0, otherwise NO BET.            */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.TOMarkets = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const API = 'https://api.jup.ag/prediction/v1';
  const FEE_RATE = 0.07;
  const LISTS = [['atp', 'upcoming'], ['atp', 'live'], ['wta', 'upcoming'], ['wta', 'live']];

  function norm(s) {
    return String(s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/[^a-z\s-]/g, ' ').split(/[\s-]+/).filter(Boolean);
  }
  // same player if >=2 shared name tokens, or 1 shared token (len>=3) plus same first initial / same first token
  function samePlayer(a, b) {
    const ta = norm(a), tb = norm(b); if (!ta.length || !tb.length) return false;
    const sb = new Set(tb); const shared = ta.filter(t => sb.has(t));
    if (shared.length >= 2) return true;
    if (shared.length === 1 && shared[0].length >= 3) {
      if (ta.length === 1 || tb.length === 1) return true;
      return ta[0][0] === tb[0][0];
    }
    return false;
  }
  function eventPlayers(ev) {
    const t = (ev.metadata && ev.metadata.title) || '';
    const body = t.includes(':') ? t.slice(t.indexOf(':') + 1) : t;
    const parts = body.split(/\s+vs\.?\s+/i);
    return parts.length === 2 ? [parts[0].trim(), parts[1].trim()] : null;
  }
  // which side of the slate match ("A"/"B") a name refers to, or null
  function side(name, A, B) {
    const a = samePlayer(name, A), b = samePlayer(name, B);
    return a && !b ? 'A' : b && !a ? 'B' : null;
  }
  function matchEvent(m, events) {
    const [A, B] = [m.players[0].name, m.players[1].name];
    const t0 = m.start_utc ? Date.parse(m.start_utc) / 1000 : null;
    for (const ev of events) {
      if (ev.subcategory && m.tour && ev.subcategory !== m.tour) continue;
      const ps = eventPlayers(ev); if (!ps) continue;
      const s0 = side(ps[0], A, B), s1 = side(ps[1], A, B);
      if (!s0 || !s1 || s0 === s1) continue;
      if (t0 && ev.beginAt && Math.abs(Number(ev.beginAt) - t0) > 36 * 3600) continue;
      return ev;
    }
    return null;
  }
  function parseLabel(lbl) {            // "Jan-Lennard Struff -1.5" -> {name, line:-1.5}; "Over 21.5" -> {ou:'over', line}
    const s = String(lbl || '').trim();
    let r = s.match(/^(over|under)\s+([0-9.]+)$/i);
    if (r) return { ou: r[1].toLowerCase(), line: Number(r[2]) };
    r = s.match(/^(.*\S)\s+([+-][0-9.]+)$/);
    if (r) return { name: r[1], line: Number(r[2]) };
    return { name: s, line: null };
  }
  function cost(p) { return p + FEE_RATE * p * (1 - p); }
  const r4 = x => Math.round(x * 1e4) / 1e4;

  // model probability of one market outcome from the frozen distribution (A/B = slate order), or {skip}
  function modelProb(mk, lab, dist, A, B, bo) {
    const t = mk.sportsMarketType, need = Math.floor(bo / 2) + 1;
    const ss = dist.set_score_probs, gp = dist.games_pmf, dp = dist.games_diff_pmf;
    const sumIf = (pmf, f) => Object.entries(pmf).reduce((s, [k, v]) => s + (f(k) ? v : 0), 0);
    if (t === 'moneyline') {
      const sd = side(lab.name, A, B); if (!sd) return { skip: 'unmatched player' };
      return { p: sd === 'A' ? dist.win_prob.A : dist.win_prob.B, desc: `${lab.name} to win` };
    }
    if (t === 'tennis_set_totals' || t === 'tennis_match_totals') {
      if (!lab.ou) return { skip: 'unparsed label' };
      const L = lab.line; if (Number.isInteger(L)) return { skip: 'integer line (push) not modelled' };
      const sets = t === 'tennis_set_totals';
      const over = sets ? sumIf(ss, k => { const [a, b] = k.split('-').map(Number); return a + b > L; })
                        : sumIf(gp, k => Number(k) > L);
      return { p: lab.ou === 'over' ? over : 1 - over, desc: `${lab.ou === 'over' ? 'Over' : 'Under'} ${L} ${sets ? 'sets' : 'games'}` };
    }
    if (t === 'handicap' || t === 'tennis_game_handicap') {
      const sd = side(lab.name, A, B); if (!sd || lab.line == null) return { skip: 'unparsed label' };
      const L = lab.line; if (Number.isInteger(L)) return { skip: 'integer line (push) not modelled' };
      const sgn = sd === 'A' ? 1 : -1, sets = t === 'handicap';
      const p = sets ? sumIf(ss, k => { const [a, b] = k.split('-').map(Number); return sgn * (a - b) + L > 0; })
                     : sumIf(dp, k => sgn * Number(k) + L > 0);
      return { p, desc: `${lab.name} ${L > 0 ? '+' : ''}${L} ${sets ? 'sets' : 'games'}` };
    }
    return { skip: `market type ${t} not modelled` };
  }

  // evaluate every outcome of a matched event against a slate match's frozen forecast
  function evaluate(m, ev, opts) {
    opts = opts || {};
    const f = m.forecast, base = { espn_id: m.espn_id, event_id: ev ? ev.eventId : null, event_title: ev && ev.metadata ? ev.metadata.title : null };
    if (!ev) return Object.assign(base, { status: 'no_market', best: null, verdict: 'NO BET (no market)', outcomes: [] });
    if (!f || !f.dist) return Object.assign(base, { status: 'no_forecast', best: null, verdict: 'NO BET (no frozen forecast)', outcomes: [] });
    const snapAt = opts.snapshot_at_utc ? Date.parse(opts.snapshot_at_utc) : Date.now();
    if (f.frozen_at_utc && Date.parse(f.frozen_at_utc) > snapAt)
      return Object.assign(base, { status: 'invalid_order', best: null, verdict: 'NO BET (prices older than forecast)', outcomes: [] });
    const A = m.players[0].name, B = m.players[1].name, bo = m.best_of || 3;
    const outs = [];
    for (const mk of ev.markets || []) {
      const lab = parseLabel(((mk.marketOptions || [])[0] || {}).label || mk.title);
      const pr = mk.pricing || {}; const price = Number(pr.buyYesPriceUsd || 0) / 1e6;
      const mp = modelProb(mk, lab, f.dist, A, B, bo);
      const o = { market_id: mk.marketId, type: mk.sportsMarketType, label: ((mk.marketOptions || [])[0] || {}).label || mk.title, status: mk.status };
      if (mp.skip) { o.skip = mp.skip; outs.push(o); continue; }
      o.selection = mp.desc; o.model_p = r4(mp.p);
      if (mk.status && mk.status !== 'open') { o.skip = `market ${mk.status}`; outs.push(o); continue; }
      if (!(price > 0 && price < 1)) { o.skip = 'no ask price (no liquidity)'; outs.push(o); continue; }
      if (price >= 0.99) { o.skip = 'ask at 0.99 placeholder (no real liquidity)'; outs.push(o); continue; }
      const c = cost(price);
      o.price = r4(price); o.implied = r4(c); o.edge = r4(mp.p - c); o.ev_per_1 = r4(mp.p / c - 1);
      o.eligible = mp.p > 0.5 && mp.p - c > 0;
      outs.push(o);
    }
    const priced = outs.filter(o => o.implied != null);
    const elig = priced.filter(o => o.eligible).sort((x, y) => y.ev_per_1 - x.ev_per_1 || y.model_p - x.model_p);
    const live = m.status && m.status !== 'STATUS_SCHEDULED';
    let best = elig[0] || null, verdict, status;
    if (live) { best = null; status = 'not_prematch'; verdict = m.completed ? 'NO BET (match finished)' : 'NO BET (match in progress - pre-match model not valid in-play)'; }
    else if (!priced.length) { status = 'no_prices'; verdict = 'NO BET (no priced outcomes)'; }
    else if (!best) {
      status = 'no_bet';
      const anyEdge = priced.some(o => o.edge > 0), anyFav = priced.some(o => o.model_p > 0.5);
      verdict = 'NO BET (' + (!anyEdge ? 'no positive edge' : !anyFav ? 'no outcome over 50%' : 'no outcome with both >50% and positive edge') + ')';
    } else { status = 'bet'; verdict = `${best.selection} @ ${best.price.toFixed(2)} (model ${(best.model_p * 100).toFixed(1)}%, edge ${(best.edge * 100).toFixed(1)} pts, EV ${(best.ev_per_1 * 100 >= 0 ? '+' : '')}${(best.ev_per_1 * 100).toFixed(1)}%)`; }
    return Object.assign(base, { status, best, verdict, outcomes: outs, n_priced: priced.length, n_eligible: elig.length });
  }

  function evaluateSlate(slate, events, opts) {
    return slate.matches.map(m => evaluate(m, matchEvent(m, events), opts));
  }

  async function fetchEvents(fetchFn, opts) {
    opts = opts || {}; const gap = opts.gap_ms == null ? 2200 : opts.gap_ms;
    const out = [], errors = [];
    for (let i = 0; i < LISTS.length; i++) {
      const [sub, filt] = LISTS[i];
      const url = `${API}/events?category=sports&subcategory=${sub}&filter=${filt}&includeMarkets=true&includeAllMarkets=true&start=0&end=100`;
      try {
        const r = await fetchFn(url, { headers: Object.assign({ accept: 'application/json' }, opts.apiKey ? { 'x-api-key': opts.apiKey } : {}) });
        if (!r.ok) { errors.push(`${sub}/${filt}: HTTP ${r.status}`); }
        else { const j = await r.json(); for (const e of (j.data || [])) out.push(e); }
      } catch (e) { errors.push(`${sub}/${filt}: ${e.message || e}`); }
      if (i < LISTS.length - 1 && gap) await new Promise(res => setTimeout(res, gap));
    }
    const seen = new Set(); const evs = out.filter(e => !seen.has(e.eventId) && seen.add(e.eventId));
    return { events: evs, errors, fetched_at_utc: new Date().toISOString() };
  }

  // slate-wide leaderboards over every priced pre-match outcome (rows = evaluate() results)
  function leaderboards(rows, n) {
    n = n || 10; const all = [];
    for (const r of rows || []) {
      if (!r || r.status === 'not_prematch' || r.status === 'invalid_order' || r.status === 'no_forecast') continue;
      for (const o of r.outcomes || []) if (o.implied != null) all.push(Object.assign({ espn_id: r.espn_id, event_title: r.event_title, is_best: !!(r.best && r.best.market_id === o.market_id) }, o));
    }
    const prob = [...all].sort((a, b) => b.model_p - a.model_p || b.ev_per_1 - a.ev_per_1).slice(0, n);
    const ev = all.filter(o => o.ev_per_1 > 0).sort((a, b) => b.ev_per_1 - a.ev_per_1 || b.model_p - a.model_p).slice(0, n);
    return { top_probability: prob, top_ev: ev, n_outcomes: all.length };
  }

  return { leaderboards, API, FEE_RATE, LISTS, norm, samePlayer, eventPlayers, side, matchEvent, parseLabel, cost, modelProb, evaluate, evaluateSlate, fetchEvents };
}));
