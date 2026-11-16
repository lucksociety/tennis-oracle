/* Tennis Oracle - Polymarket price comparison (shared by the dashboard, the Discord Worker and
 * mirrored exactly by tennis_oracle/markets.py; parity tested in tests/test_markets.py).
 * Market prices NEVER feed the model: this only reads an already-frozen forecast distribution and
 * compares it with prices fetched afterwards from Polymarket's public Gamma API.
 *   cost (implied prob incl. fee) = p + rate * (p * (1 - p)) ^ exponent   (the market's own feeSchedule; sports default 0.05)
 *   edge = model_p - cost ;  EV per $1 staked = model_p / cost - 1
 * BETS ARE PAUSED (2026-11-16): on 308 settled matches the market's log loss was 0.571 against the model's 0.623.
 * Every outcome is still priced and flagged would_qualify so the comparison keeps accumulating; nothing is recommended. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.TOMarkets = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const GAMMA = 'https://gamma-api.polymarket.com', CLOB = 'https://clob.polymarket.com';
  const TENNIS_TAG = '864', TOURS = ['atp'];
  const DEFAULT_FEE = { rate: 0.05, exponent: 1 };
  const TYPE_MAP = { moneyline: 'moneyline', tennis_set_totals: 'tennis_set_totals', tennis_match_totals: 'tennis_match_totals', tennis_set_handicap: 'handicap', tennis_game_handicap: 'tennis_game_handicap' };
  const HANDICAP_Q = /^.*?:\s*(.+?)\s*\(([+-]\d+(?:\.\d+)?)\)\s+vs\.?\s+(.+?)\s*\(([+-]\d+(?:\.\d+)?)\)\s*$/;
  const BETS_PAUSED = true;
  const PAUSE_VERDICT = 'NO BET (bets paused: the model trails the market price on settled matches)';

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
    const matches=new Map();
    for (const ev of events) {
      if (ev.subcategory && m.tour && ev.subcategory !== m.tour) continue;
      const ps = eventPlayers(ev); if (!ps) continue;
      const s0 = side(ps[0], A, B), s1 = side(ps[1], A, B);
      if (!s0 || !s1 || s0 === s1) continue;
      if(!t0||!ev.beginAt)continue;
      const day=x=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York'}).format(new Date(x*1000));
      if(day(t0)!==day(Number(ev.beginAt)))continue;
      matches.set(ev.eventId,ev);
    }
    return matches.size===1?[...matches.values()][0]:null;
  }
  function parseLabel(lbl) {            // "Jan-Lennard Struff -1.5" -> {name, line:-1.5}; "Over 21.5" -> {ou:'over', line}
    const s = String(lbl || '').trim();
    let r = s.match(/^(over|under)\s+([0-9.]+)$/i);
    if (r) return { ou: r[1].toLowerCase(), line: Number(r[2]) };
    r = s.match(/^(.*\S)\s+([+-][0-9.]+)$/);
    if (r) return { name: r[1], line: Number(r[2]) };
    return { name: s, line: null };
  }
  function cost(p, fee) { const f = fee && fee.rate != null ? fee : DEFAULT_FEE; return p + Number(f.rate) * Math.pow(p * (1 - p), Number(f.exponent == null ? 1 : f.exponent)); }
  const r4 = x => Math.round(x * 1e4) / 1e4;
  const list = v => { if (typeof v === 'string') { try { v = JSON.parse(v); } catch (e) { return []; } } return Array.isArray(v) ? v : []; };
  const fnum = x => { if (x == null || x === '') return null; const n = Number(x); return Number.isFinite(n) ? n : null; };
  const fmtLine = x => String(Number(x));

  // Gamma event -> the internal event shape (one market record per buyable outcome)
  function adaptGammaEvent(e) {
    const tour = e.seriesSlug, t0 = Date.parse(e.startTime);
    if (!TOURS.includes(tour) || !e.id || !Number.isFinite(t0) || !String(e.title || '').includes(' vs')) return null;
    const markets = [];
    for (const gm of e.markets || []) {
      const t = TYPE_MAP[gm.sportsMarketType], outs = list(gm.outcomes), toks = list(gm.clobTokenIds);
      if (!t || outs.length !== 2 || toks.length !== 2) continue;
      const line = fnum(gm.line); let labels;
      if (t === 'moneyline') labels = outs.map(String);
      else if (t === 'tennis_set_totals' || t === 'tennis_match_totals') {
        if (line == null || String(outs[0]).split(/\s+/)[0].toLowerCase() !== 'over' || String(outs[1]).split(/\s+/)[0].toLowerCase() !== 'under') continue;
        labels = [`Over ${fmtLine(line)}`, `Under ${fmtLine(line)}`];
      } else {
        const q = HANDICAP_Q.exec(String(gm.question || ''));
        if (!q || q[1] !== String(outs[0]) || q[3] !== String(outs[1]) || Number(q[2]) !== -Number(q[4])) continue;
        labels = [`${outs[0]} ${q[2]}`, `${outs[1]} ${q[4]}`];
      }
      const bid = fnum(gm.bestBid), ask = fnum(gm.bestAsk);
      // Binary CLOB books mirror each other: buying outcome 1 at its ask is selling outcome 0 at its bid.
      const quotes = [[ask, bid], [bid == null ? null : 1 - bid, ask == null ? null : 1 - ask]];
      const sched = gm.feeSchedule || {};
      const fee = gm.feesEnabled === false ? { rate: 0, exponent: 1 } : sched.rate != null ? { rate: Number(sched.rate), exponent: Number(sched.exponent == null ? 1 : sched.exponent) } : null;
      const open = !gm.closed && gm.active !== false && gm.acceptingOrders !== false;
      const usd = x => (x != null && x > 0 && x < 1 ? Math.round(x * 1e6) : 0);
      for (let i = 0; i < 2; i++) markets.push({ marketId: `POLY-${gm.id}-${i}`, sportsMarketType: t, title: labels[i], sportsLine: line, status: open ? 'open' : 'closed',
        marketOptions: [{ label: labels[i] }], pricing: { buyYesPriceUsd: usd(quotes[i][0]), sellYesPriceUsd: usd(quotes[i][1]), buyNoPriceUsd: 0, sellNoPriceUsd: 0 }, fee, tokenId: String(toks[i]) });
    }
    return { eventId: `POLY-${e.id}`, subcategory: tour, slug: e.slug == null ? null : e.slug, metadata: { title: e.title }, beginAt: String(Math.floor(t0 / 1000)), isLive: !!(e.live || e.ended), markets };
  }
  // no-vig pre-match moneyline for the slate's player A: the benchmark the model is graded against
  function marketWinProb(ev, A, B) {
    const mids = {};
    for (const mk of (ev && ev.markets) || []) {
      if (mk.sportsMarketType !== 'moneyline' || mk.status !== 'open') continue;
      const pr = mk.pricing || {}, a = Number(pr.buyYesPriceUsd || 0) / 1e6, b = Number(pr.sellYesPriceUsd || 0) / 1e6;
      const sd = side(mk.title, A, B);
      if (!sd || !(b > 0 && b <= a && a < 1) || a - b > 0.06 + 1e-9) return null;
      mids[sd] = (a + b) / 2;
    }
    if (mids.A == null || mids.B == null) return null;
    return r4(mids.A / (mids.A + mids.B));
  }

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

  function scoreSupports(mk,lab,f,A,B,bo=3) {
    const fs=f?.final_score||{},a=fs.A_sets,b=fs.B_sets;
    if(![a,b].every(x=>Number.isInteger(x)&&x>=0)||Math.max(a,b)!==Math.floor(bo/2)+1||a===b)return false;
    const t=mk.sportsMarketType,sd=side(lab.name,A,B),line=lab.line;
    if(t==='moneyline')return sd==='A'?a>b:sd==='B'?b>a:false;
    if(!Number.isFinite(line))return false;
    if(t==='handicap')return sd?((sd==='A'?a-b:b-a)+line>0):false;
    if(t==='tennis_set_totals')return lab.ou==='over'?a+b>line:lab.ou==='under'?a+b<line:false;
    const sets=fs.sets||[],pairs=[];
    for(const text of sets){const m=/^(\d+)-(\d+)(?:\(\d+\))?$/.exec(String(text));if(!m)return false;pairs.push([+m[1],+m[2]]);}
    if(pairs.length!==a+b||pairs.filter(([x,y])=>x>y).length!==a||pairs.filter(([x,y])=>y>x).length!==b)return false;
    const ga=pairs.reduce((n,[x,y])=>n+x,0),gb=pairs.reduce((n,[x,y])=>n+y,0);
    if(t==='tennis_game_handicap')return sd?((sd==='A'?ga-gb:gb-ga)+line>0):false;
    if(t==='tennis_match_totals'){
      const totals=[ga+gb];if(f.games_projected!=null)totals.push(f.games_projected);
      return lab.ou==='over'?totals.every(v=>v>line):lab.ou==='under'?totals.every(v=>v<line):false;
    }
    return false;
  }
  function recommendationMatchesScore(m,o){return !!(m?.forecast&&o)&&scoreSupports({sportsMarketType:o.type},parseLabel(o.label),m.forecast,m.players[0].name,m.players[1].name,m.best_of||3);}
  // evaluate every outcome of a matched event against a slate match's frozen forecast
  function evaluate(m, ev, opts) {
    opts = opts || {};
    const paused = opts.bets_paused == null ? BETS_PAUSED : !!opts.bets_paused;
    const f = m.forecast, base = { espn_id: m.espn_id, event_id: ev ? ev.eventId : null, event_title: ev && ev.metadata ? ev.metadata.title : null };
    if (m.tour !== 'atp') return Object.assign(base,{status:'excluded',best:null,verdict:'Excluded by men-only policy',outcomes:[]});
    if (!ev) return Object.assign(base, { status: 'no_market', best: null, verdict: 'NO BET (no market)', outcomes: [] });
    if (!f || !f.dist) return Object.assign(base, { status: 'no_forecast', best: null, verdict: 'NO BET (no frozen forecast)', outcomes: [] });
    const snapAt = opts.snapshot_at_utc ? Date.parse(opts.snapshot_at_utc) : Date.now();
    if (f.frozen_at_utc && Date.parse(f.frozen_at_utc) > snapAt)
      return Object.assign(base, { status: 'invalid_order', best: null, verdict: 'NO BET (prices older than forecast)', outcomes: [] });
    base.model_p_a = f.dist.win_prob && typeof f.dist.win_prob.A === 'number' ? r4(f.dist.win_prob.A) : null;
    const t0 = Date.parse(m.start_utc);
    const prematch = !!m.start_utc && Number.isFinite(t0) && snapAt < t0 && !ev.isLive && (m.status == null || m.status === 'STATUS_SCHEDULED');
    base.market_p_a = prematch ? marketWinProb(ev, m.players[0].name, m.players[1].name) : null;
    if (!f.recommendation_quality || f.recommendation_quality.eligible !== true)
      return Object.assign(base, { status: 'input_quality', best: null, verdict: 'NO BET (current performance inputs not verified)', outcomes: [] });
    const A = m.players[0].name, B = m.players[1].name, bo = m.best_of || 3;
    const outs = [];
    for (const mk of ev.markets || []) {
      const lab = parseLabel(((mk.marketOptions || [])[0] || {}).label || mk.title);
      const pr = mk.pricing || {}; const price = Number(pr.buyYesPriceUsd || 0) / 1e6;
      const mp = modelProb(mk, lab, f.dist, A, B, bo);
      const o = { market_id: mk.marketId, type: mk.sportsMarketType, label: ((mk.marketOptions || [])[0] || {}).label || mk.title, status: mk.status };
      if (mp.skip) { o.skip = mp.skip; outs.push(o); continue; }
      if (!Number.isFinite(mp.p) || mp.p < 0 || mp.p > 1) { o.skip = 'invalid model probability'; outs.push(o); continue; }
      o.selection = mp.desc; o.model_p = r4(mp.p);
      const lower = Number.isInteger(f.n_sims) && f.n_sims >= 1000 ? mp.p - 1.96 * Math.sqrt(mp.p * (1 - mp.p) / f.n_sims) : -1;
      o.mc_lower_95 = r4(lower);
      if (mk.status && mk.status !== 'open') { o.skip = `market ${mk.status}`; outs.push(o); continue; }
      if (!(price > 0 && price < 1)) { o.skip = 'no ask price (no liquidity)'; outs.push(o); continue; }
      if (price >= 0.99) { o.skip = 'ask at 0.99 placeholder (no real liquidity)'; outs.push(o); continue; }
      const c = cost(price, mk.fee);
      o.price = r4(price); o.implied = r4(c); o.edge = r4(mp.p - c); o.ev_per_1 = r4(mp.p / c - 1);
      o.score_consistent = scoreSupports(mk,lab,f,A,B,bo);
      o.would_qualify = o.score_consistent && lower > 0.5 && lower > cost(Math.min(.999,price+.01), mk.fee) && o.model_p > 0.5 && o.ev_per_1 > 0;
      o.eligible = o.would_qualify && !paused;
      outs.push(o);
    }
    const priced = outs.filter(o => o.implied != null);
    const elig = priced.filter(o => o.eligible).sort((x, y) => y.ev_per_1 - x.ev_per_1 || y.model_p - x.model_p);
    const start = Date.parse(m.start_utc);
    const live = (m.status && m.status !== 'STATUS_SCHEDULED') || ev.isLive || !Number.isFinite(start) || snapAt >= start;
    let best = elig[0] || null, verdict, status;
    if (live) { best = null; status = 'not_prematch'; verdict = m.completed ? 'NO BET (match finished)' : 'NO BET (match in progress - pre-match model not valid in-play)'; }
    else if (!priced.length) { status = 'no_prices'; verdict = 'NO BET (no priced outcomes)'; }
    else if (paused) { status = 'paused'; verdict = PAUSE_VERDICT; }
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

  // open ATP singles events starting from 12 h ago (in play) to 3 days ahead, with their markets
  async function fetchEvents(fetchFn, opts) {
    opts = opts || {}; const now = opts.now ? new Date(opts.now) : new Date();
    const lo = new Date(now.getTime() - 12 * 36e5).toISOString(), hi = new Date(now.getTime() + 3 * 864e5).toISOString();
    const out = new Map(), errors = [];
    try {
      let page = 0;
      for (; page < 30; page++) {
        const url = `${GAMMA}/events?tag_id=${TENNIS_TAG}&closed=false&limit=100&offset=${page * 100}&order=id&ascending=true&start_time_min=${encodeURIComponent(lo)}&start_time_max=${encodeURIComponent(hi)}`;
        const r = await fetchFn(url, { headers: { accept: 'application/json' } });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json(); if (!Array.isArray(data)) throw new Error('Gamma events page is not a list');
        for (const e of data) { const ev = adaptGammaEvent(e); if (ev) out.set(ev.eventId, ev); }
        if (data.length < 100) break;
      }
      if (page === 30) errors.push('pagination safety limit reached; event list may be partial');
    } catch (e) { errors.push(`gamma: ${e.message || e}`); }
    return { events: [...out.values()], errors, fetched_at_utc: now.toISOString() };
  }

  // slate-wide leaderboards over every priced pre-match outcome (rows = evaluate() results)
  function leaderboards(rows, n) {
    n = n || 10; const all = [];
    for (const r of rows || []) {
      if (!r || r.status === 'not_prematch' || r.status === 'invalid_order' || r.status === 'no_forecast' || r.status === 'input_quality') continue;
      for (const o of r.outcomes || []) if (o.implied != null && o.eligible && o.model_p > .5 && o.ev_per_1 > 0) all.push(Object.assign({ espn_id: r.espn_id, event_title: r.event_title, is_best: !!(r.best && r.best.market_id === o.market_id) }, o));
    }
    const prob = [...all].sort((a, b) => b.model_p - a.model_p || b.ev_per_1 - a.ev_per_1).slice(0, n);
    const ev = all.filter(o => o.ev_per_1 > 0).sort((a, b) => b.ev_per_1 - a.ev_per_1 || b.model_p - a.model_p).slice(0, n);
    return { top_probability: prob, top_ev: ev, n_outcomes: all.length };
  }

  return { scoreSupports, recommendationMatchesScore, leaderboards, GAMMA, CLOB, DEFAULT_FEE, BETS_PAUSED, PAUSE_VERDICT, adaptGammaEvent, marketWinProb, norm, samePlayer, eventPlayers, side, matchEvent, parseLabel, cost, modelProb, evaluate, evaluateSlate, fetchEvents };
}));
