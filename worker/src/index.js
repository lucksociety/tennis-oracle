// Encrypt short-lived interaction credentials before putting them in public workflow inputs.
export async function dispatchModel(env, interaction, date, workflow, repo) {
  const app = env.DISCORD_APPLICATION_ID || interaction.application_id;
  const edit = async content => {
    const r = await fetch(`https://discord.com/api/v10/webhooks/${app}/${interaction.token}/messages/@original`, {
      method: 'PATCH', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content, allowed_mentions: { parse: [] } }),
    });
    if (!r.ok) throw new Error(`Discord reply HTTP ${r.status}`);
  };
  try {
    if (!env.GITHUB_TOKEN || !env.RELAY_KEY) throw new Error('configure GITHUB_TOKEN and RELAY_KEY on the Worker, plus the same RELAY_KEY in Actions');
    const keyBytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(env.RELAY_KEY));
    const key = await crypto.subtle.importKey('raw', keyBytes, 'AES-GCM', false, ['encrypt']);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const msg = { token: interaction.token, app, ts: Date.now(), date,
      only_bets: (interaction.data?.options || []).some(o => o.name === 'only_bets' && o.value === true) };
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(JSON.stringify(msg))));
    const bytes = new Uint8Array(iv.length + ct.length); bytes.set(iv); bytes.set(ct, iv.length);
    const relay = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO || repo}/actions/workflows/${workflow}/dispatches`, {
      method: 'POST', headers: { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: 'application/vnd.github+json',
        'content-type': 'application/json', 'user-agent': 'oracle-discord', 'x-github-api-version': '2022-11-28' },
      body: JSON.stringify({ ref: env.GITHUB_REF || 'main', inputs: { date, relay } }),
    });
    if (r.status !== 204) throw new Error(`GitHub dispatch HTTP ${r.status}`);
    // The workflow owns the deferred reply from here. A progress PATCH here could race its result.
  } catch (e) {
    await edit(`⚠️ Could not start the model for ${date}: ${e.message}.`);
  }
}

/* Luck Society Tennis Oracle - Discord /tennis interactions endpoint + live best-bet API (Cloudflare Worker).
 * Built by worker/build.mjs, which prepends docs/engine.js (TennisOracle) and docs/markets.js (TOMarkets).
 * Forecasts come from the FROZEN pre-match slate on GitHub Pages; Jupiter prices are fetched live at
 * command time and only compared against those frozen numbers (never fed back into the model).
 * Env: DISCORD_PUBLIC_KEY, DISCORD_APPLICATION_ID (vars), DASHBOARD_URL, optional JUPITER_API_KEY (secret),
 *      ENGINE_FALLBACK = "on" to simulate Jupiter-listed matches missing from the frozen slate (needs Workers Paid CPU). */
const M = globalThis.TOMarkets, T = globalThis.TennisOracle;
const DISCORD = 'https://discord.com/api/v10';
const json = (o, s = 200, h = {}) => new Response(JSON.stringify(o), { status: s, headers: Object.assign({ 'content-type': 'application/json' }, h) });
const CORS = { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'GET, OPTIONS' };
const nyDate = (d = new Date()) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const nyTime = iso => iso ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(new Date(iso)) + ' ET' : '';
const addDays = (ymd, k) => { const d = new Date(ymd + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + k); return d.toISOString().slice(0, 10); };
const dayLabel = ymd => new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(ymd + 'T12:00:00Z'));
// accepts today / tomorrow / yesterday / YYYY-MM-DD / MM-DD / M/D (NY time); returns YYYY-MM-DD or null
function parseDay(v) {
  const t = nyDate(), s = String(v || '').trim().toLowerCase();
  if (!s || s === 'today') return t;
  if (s === 'tomorrow') return addDays(t, 1);
  if (s === 'yesterday') return addDays(t, -1);
  let r = s.match(/^\+(\d)$/); if (r) return addDays(t, +r[1]);
  let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  const ok = (y, mo, d) => { const x = `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`; return isNaN(Date.parse(x + 'T12:00:00Z')) || new Date(x + 'T12:00:00Z').toISOString().slice(0, 10) !== x ? null : x; };
  if (m) return ok(m[1], m[2], m[3]);
  m = s.match(/^(\d{1,2})[-/](\d{1,2})$/);
  if (m) return ok(t.slice(0, 4), m[1], m[2]);
  return null;
}
async function dateChoices(env, typed) {
  const t = nyDate(); let idx = { dates: [], counts: {} };
  try { idx = await getJSON(`${env.DASHBOARD_URL}data/slates.json`); } catch (e) { }
  const cnt = d => (idx.counts && idx.counts[d] != null) ? ` (${idx.counts[d]} matches)` : (idx.dates || []).includes(d) ? '' : ' (no slate yet)';
  const rel = [['Today', t], ['Tomorrow', addDays(t, 1)], ['Yesterday', addDays(t, -1)]];
  let ch = rel.map(([n, d]) => ({ name: `${n} · ${dayLabel(d)}${cnt(d)}`, value: d }));
  const rs = new Set(rel.map(r => r[1]));
  ch = ch.concat([...(idx.dates || [])].sort().reverse().filter(d => !rs.has(d)).map(d => ({ name: `${dayLabel(d)}${cnt(d)}`, value: d })));
  const q = String(typed || '').trim().toLowerCase();
  if (q) {
    const p = parseDay(q);
    const hit = ch.filter(c => c.name.toLowerCase().includes(q) || c.value.includes(q) || c.value === p);
    if (p && !hit.some(c => c.value === p)) hit.unshift({ name: `${dayLabel(p)}${cnt(p)}`, value: p });
    ch = hit;
  }
  return ch.slice(0, 25);
}
const pct = (x, d = 1) => (100 * x).toFixed(d) + '%';

function hex2buf(h) { const b = new Uint8Array(h.length / 2); for (let i = 0; i < b.length; i++) b[i] = parseInt(h.substr(2 * i, 2), 16); return b; }
async function verifyDiscord(req, body, pubHex) {
  const sig = req.headers.get('x-signature-ed25519'), ts = req.headers.get('x-signature-timestamp');
  if (!sig || !ts || !/^[0-9a-f]+$/i.test(sig) || sig.length !== 128) return false;
  try {
    const key = await crypto.subtle.importKey('raw', hex2buf(pubHex), { name: 'Ed25519' }, false, ['verify']);
    return await crypto.subtle.verify({ name: 'Ed25519' }, key, hex2buf(sig), new TextEncoder().encode(ts + body));
  } catch (e) { return false; }
}

async function getJSON(url) { const r = await fetch(url, { cf: { cacheTtl: 60 } }); if (!r.ok) throw new Error(`${url} HTTP ${r.status}`); return r.json(); }

// live prices -> evaluations; falls back to the newest committed snapshot if Jupiter is unreachable/geo-blocked
async function bestBets(env, day, slate) {
  const dash = env.DASHBOARD_URL;
  const live = await M.fetchEvents(fetch, { gap_ms: 2100, apiKey: env.JUPITER_API_KEY });
  if (live.events.length) {
    const rows = M.evaluateSlate(slate, live.events, { snapshot_at_utc: live.fetched_at_utc });
    return { source: 'live', at: live.fetched_at_utc, errors: live.errors, rows, events: live.events };
  }
  try {
    const snap = await getJSON(`${dash}data/jupiter-${day}.json?t=${Date.now()}`);
    return { source: 'snapshot', at: snap.snapshot_at_utc, errors: live.errors, rows: snap.matches, events: [] };
  } catch (e) { return { source: 'none', at: null, errors: live.errors.concat(String(e.message)), rows: [], events: [] }; }
}

function distFromEngine(f) {
  const ss = Array.isArray(f.set_score_probs) ? Object.fromEntries(f.set_score_probs) : f.set_score_probs;
  return { win_prob: { A: f.win_prob[0], B: f.win_prob[1] }, set_score_probs: ss, games_pmf: f.games.pmf, games_diff_pmf: f.diff_pmf };
}

// optional: run the engine for Jupiter-listed matches on this NY date that are not in the frozen slate
async function engineExtras(env, day, slate, events) {
  if (env.ENGINE_FALLBACK !== 'on' || !events.length) return [];
  const known = new Set(slate.matches.map(m => M.matchEvent(m, events)).filter(Boolean).map(e => e.eventId));
  const todo = events.filter(e => !known.has(e.eventId) && !e.isLive && nyDate(new Date(Number(e.beginAt) * 1000)) === day).slice(0, 8);
  if (!todo.length) return [];
  const R = await getJSON(`${env.DASHBOARD_URL}data/ratings.json`); const O = new T.Oracle(R); const out = [];
  for (const ev of todo) {
    const ps = M.eventPlayers(ev); if (!ps) continue;
    const tour = ev.subcategory, A = O.find(ps[0], tour), B = O.find(ps[1], tour);
    const title = (ev.metadata && ev.metadata.title) || '';
    const m = { espn_id: null, tour, tournament: title.split(':')[0], players: [{ name: ps[0] }, { name: ps[1] }], best_of: 3, final_rule: 'tb7',
      status: 'STATUS_SCHEDULED', start_utc: new Date(Number(ev.beginAt) * 1000).toISOString(), surface: 'Hard', surface_source: 'assumed', engine: true };
    if (!A || !B) { out.push({ m, note: `not in frozen slate; no rating history for ${!A ? ps[0] : ps[1]}` }); continue; }
    const f = O.forecast({ a: A.id, b: B.id, tour, surface: 'Hard', bestOf: 3, finalRule: 'tb7', indoor: false, date: day + 'T12:00:00', sims: 100000, skipAnalytic: true });
    m.forecast = { frozen_at_utc: new Date().toISOString(), predicted_winner: f.predicted_winner, win_prob: { A: f.win_prob[0], B: f.win_prob[1] },
      final_score: { A_sets: f.final_score.A, B_sets: f.final_score.B, sets: f.final_score.sets }, games_projected: f.games.median, dist: distFromEngine(f) };
    out.push({ m, row: M.evaluate(m, ev) });
  }
  return out;
}

function matchLines(m, row) {
  const [A, B] = [m.players[0].name, m.players[1].name], f = m.forecast;
  const head = `**${m.tour ? m.tour.toUpperCase() + ' ' : ''}${m.tournament || ''}${m.round ? ' · ' + m.round : ''}**${m.start_utc ? ' · ' + nyTime(m.start_utc) : ''}${m.engine ? ' · _live engine run, surface assumed Hard_' : ''}`;
  const lines = [head, `🎾 ${A} vs ${B}`];
  if (!f) { lines.push(`_No forecast: ${m.note || 'player(s) not in rating database'}_`); return lines.join('\n'); }
  const aW = f.win_prob.A >= 0.5, w = aW ? A : B, wp = aW ? f.win_prob.A : f.win_prob.B, fs = f.final_score;
  const sets = (fs.sets || []).map(s => { const [x, y] = s.split('-').map(Number); return aW ? `${x}-${y}` : `${y}-${x}`; }).join(' ');
  const sc = aW ? `${fs.A_sets}-${fs.B_sets}` : `${fs.B_sets}-${fs.A_sets}`;
  const g = f.games_projected != null ? ` · ~${f.games_projected} games` : '';
  lines.push(`Winner: **${w} ${pct(wp)}** · Score: **${w} ${sc}** (${sets})${g}`);
  if (m.result && m.result.sets) {
    const rw = m.players[m.result.winner].name, rs = m.result.sets.map(s => m.result.winner === 0 ? `${s[0]}-${s[1]}` : `${s[1]}-${s[0]}`).join(' ');
    lines.push(`Result: ${rw === w ? '✅' : '❌'} ${rw} ${rs}`);
  }
  const v = row ? row.verdict : 'NO BET (no market)';
  if (row && row.status === 'bet') { const b = row.best; lines.push(`💰 Best bet: **${b.selection} @ ${b.price.toFixed(2)}** · implied ${pct(b.implied)} · model ${pct(b.model_p)} · edge +${(100 * b.edge).toFixed(1)} pts · EV +${(100 * b.ev_per_1).toFixed(1)}%`); }
  else lines.push(`🚫 ${v}`);
  return lines.join('\n');
}

function chunk(blocks, max = 1900) {
  const out = []; let cur = '';
  for (const b of blocks) {
    const piece = b.length > max ? b.slice(0, max - 1) + '…' : b;
    if (cur && cur.length + 2 + piece.length > max) { out.push(cur); cur = ''; }
    cur = cur ? cur + '\n\n' + piece : piece;
  }
  if (cur) out.push(cur);
  return out;
}

async function discordSend(method, url, content) {
  for (let i = 0; i < 4; i++) {
    const r = await fetch(url, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify({ content, flags: 4, allowed_mentions: { parse: [] } }) });
    if (r.ok) return r;
    if (r.status !== 429) throw new Error(`Discord delivery HTTP ${r.status}`);
    const j = await r.json().catch(() => ({})); await new Promise(res => setTimeout(res, 1000 * (j.retry_after || 1) + 100));
  }
  throw new Error('Discord rate limit exhausted');
}

export async function buildReport(env, day, tour) {
  const dash = env.DASHBOARD_URL;
  let slate;
  try { slate = await getJSON(`${dash}data/slate-${day}.json?t=${Date.now()}`); }
  catch (e) { return [`🍀 **Luck Society Tennis Oracle** — no frozen slate published for ${day} yet. Dashboard: ${dash}`]; }
  if (tour && tour !== 'all') slate = Object.assign({}, slate, { matches: slate.matches.filter(m => m.tour === tour) });
  const bb = await bestBets(env, day, slate);
  const byId = new Map(bb.rows.map(r => [String(r.espn_id), r]));
  const extras = await engineExtras(env, day, slate, bb.events).catch(e => [{ err: String(e.message) }]);
  const ms = [...slate.matches].sort((a, b) => String(a.start_utc).localeCompare(String(b.start_utc)));
  const nF = ms.filter(m => m.forecast).length, nBet = ms.filter(m => (byId.get(String(m.espn_id)) || {}).status === 'bet').length;
  const n0 = (ms.find(m => m.forecast) || {}).forecast;
  const src = bb.source === 'live' ? `live Jupiter prices ${nyTime(bb.at)}` : bb.source === 'snapshot' ? `⚠️ Jupiter unreachable from Worker (${bb.errors[0] || ''}); using saved snapshot ${nyTime(bb.at)}` : `⚠️ no Jupiter prices available (${bb.errors.join('; ')})`;
  const header = `🍀 **Luck Society Tennis Oracle — ${day} (ET)**\n${ms.length} matches · ${nF} frozen pre-match forecasts (${n0 && n0.n_sims ? n0.n_sims.toLocaleString('en-US') : '100,000'} Monte Carlo sims each, seed ${n0 && n0.seed || ''}) · ${nBet} best bets · ${src}\n_Best bet = highest EV outcome with model prob > 50% AND positive edge after Jupiter fees; otherwise NO BET. Not betting advice._`;
  const blocks = [header].concat(ms.map(m => matchLines(m, byId.get(String(m.espn_id)))));
  for (const x of extras) {
    if (x.err) blocks.push(`_Engine fallback failed: ${x.err}_`);
    else if (x.row) blocks.push(matchLines(x.m, x.row) + '\n_(not in frozen slate: simulated now, not a frozen pre-match record)_');
    else blocks.push(`**${x.m.tournament}** · ${x.m.players[0].name} vs ${x.m.players[1].name}\n_${x.note}_`);
  }
  const LB = M.leaderboards(bb.rows, 5);
  const lbl = o => { const m = slate.matches.find(x => String(x.espn_id) === String(o.espn_id)); const who = m ? `${m.players[0].name.split(' ').slice(-1)[0]} v ${m.players[1].name.split(' ').slice(-1)[0]}` : ''; return `${o.selection} (${who}) @ ${o.price.toFixed(2)} · impl ${pct(o.implied)} · model ${pct(o.model_p)} · edge ${o.edge > 0 ? '+' : ''}${(100 * o.edge).toFixed(1)} · EV ${o.ev_per_1 > 0 ? '+' : ''}${(100 * o.ev_per_1).toFixed(1)}%${o.eligible ? ' ✅' : ''}`; };
  if (LB.n_outcomes) {
    blocks.push('🏆 **Top probability bets**\n' + LB.top_probability.map((o, i) => `${i + 1}. ${lbl(o)}`).join('\n'));
    blocks.push('📈 **Top EV bets** (+EV only; ✅ = also >50%)\n' + (LB.top_ev.length ? LB.top_ev.map((o, i) => `${i + 1}. ${lbl(o)}`).join('\n') : '_none_'));
  }
  blocks.push(`📊 Full reports & distributions: ${dash}`);
  return chunk(blocks);
}

async function followUp(env, interaction, day, tour) {
  const app = env.DISCORD_APPLICATION_ID, tok = interaction.token;
  let parts;
  try { parts = await buildReport(env, day, tour); } catch (e) { parts = [`⚠️ Tennis Oracle error: ${e.message}. Dashboard: ${env.DASHBOARD_URL}`]; }
  await discordSend('PATCH', `${DISCORD}/webhooks/${app}/${tok}/messages/@original`, parts[0]);
  for (const p of parts.slice(1)) await discordSend('POST', `${DISCORD}/webhooks/${app}/${tok}`, p);
}

export default {
  async fetch(req, env, ctx) {
    env.DASHBOARD_URL = env.DASHBOARD_URL || 'https://lucksociety.github.io/tennis-oracle/';
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });
    if (url.pathname === '/health') return json({ ok: true, service: 'tennis-oracle', ny_date: nyDate(), time_utc: new Date().toISOString() }, 200, CORS);
    if (url.pathname === '/api/bets' && req.method === 'GET') {
      const day = url.searchParams.get('date') || nyDate();
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return json({ error: 'bad date' }, 400, CORS);
      try {
        const slate = await getJSON(`${env.DASHBOARD_URL}data/slate-${day}.json?t=${Date.now()}`);
        const bb = await bestBets(env, day, slate);
        return json({ date: day, source: bb.source, snapshot_at_utc: bb.at, errors: bb.errors, matches: bb.rows }, 200, Object.assign({ 'cache-control': 'max-age=60' }, CORS));
      } catch (e) { return json({ error: String(e.message) }, 502, CORS); }
    }
    if (url.pathname === '/interactions' || url.pathname === '/') {
      if (req.method !== 'POST') return new Response('Tennis Oracle Discord interactions endpoint', { status: url.pathname === '/' ? 200 : 405 });
      const body = await req.text();
      if (!(await verifyDiscord(req, body, env.DISCORD_PUBLIC_KEY))) return new Response('invalid request signature', { status: 401 });
      const it = JSON.parse(body);
      if (it.type === 1) return json({ type: 1 });
      if (it.type === 4 && it.data && it.data.name === 'tennis') {   // autocomplete for the date option
        const f = (it.data.options || []).find(o => o.focused) || {};
        return json({ type: 8, data: { choices: await dateChoices(env, f.value) } });
      }
      if (it.type === 2 && it.data && it.data.name === 'tennis') {
        const opts = Object.fromEntries((it.data.options || []).map(o => [o.name, o.value]));
        const day = parseDay(opts.date);
        if (!day) return json({ type: 4, data: { content: 'Pick a date from the list, or type today / tomorrow / yesterday / YYYY-MM-DD / MM-DD.', flags: 64 } });
        ctx.waitUntil(env.RUN_MODE === 'snapshot' ? followUp(env, it, day, 'all') : dispatchModel(env, it, day, 'discord-tennis.yml', 'lucksociety/tennis-oracle'));
        return json({ type: 5 });
      }
      return json({ type: 4, data: { content: 'Unknown command', flags: 64 } });
    }
    return new Response('not found', { status: 404 });
  },
};
