/* TENNIS ORACLE V1.0 - in-browser engine.
 * Faithful port of tennis_oracle/{scoring,simulate,predict}.py. Reads the CURRENT model
 * version + parameters from data/ratings.json (exported by the Python pipeline), so the
 * dashboard always runs the newest promoted model. No market data of any kind. */
(function (G) {
  "use strict";
  const A = 0, B = 1, MAXG = 140, DOFF = 70, ND = 2 * DOFF + 1;
  const SURF = ["Hard", "Clay", "Grass"];
  const RULES = { tb7: "7-point tiebreak at 6-6", tb10: "10-point tiebreak at 6-6", adv: "advantage (no tiebreak)" };
  const GH = {
    3: [[-1.7320508075688774, 0.0, 1.7320508075688774], [1 / 6, 2 / 3, 1 / 6]],
    5: [[-2.8569700138728056, -1.355626179974266, 0, 1.355626179974266, 2.8569700138728056],
        [0.011257411327720677, 0.22207592200561257, 0.5333333333333335, 0.22207592200561257, 0.011257411327720677]],
  };
  const logit = p => { p = Math.min(Math.max(p, 1e-4), 1 - 1e-4); return Math.log(p / (1 - p)); };
  const sigmoid = x => 1 / (1 + Math.exp(-x));
  const eloProb = d => 1 / (1 + Math.pow(10, -d / 400));
  const r6 = x => Math.round(x * 1e6) / 1e6;

  function holdProb(p) {
    const q = 1 - p; if (p <= 0) return 0; if (p >= 1) return 1;
    return Math.pow(p, 4) * (1 + 4 * q + 10 * q * q) + 20 * Math.pow(p, 3) * Math.pow(q, 3) * p * p / (1 - 2 * p * q);
  }
  function expBP(p) {
    const q = 1 - p, C = [1, 4, 10];
    let e = 0; for (let i = 0; i < 3; i++) e += C[i] * Math.pow(p, i) * q * q * q;
    return e + 20 * Math.pow(p, 3) * Math.pow(q, 3) * q / (1 - 2 * p * q);
  }
  function tbServer(k, first) { if (k === 0) return first; return (Math.floor((k - 1) / 2) % 2 === 1) ? first : 1 - first; }
  function tiebreakProb(pa, pb, first, target) {
    const n = target, dp = []; for (let i = 0; i <= n; i++) dp.push(new Float64Array(n + 1));
    dp[0][0] = 1; let win = 0;
    for (let tot = 0; tot < 2 * n - 2; tot++) {
      for (let i = Math.max(0, tot - (n - 1)); i <= Math.min(tot, n - 1); i++) {
        const j = tot - i; if (j > n - 1 || dp[i][j] === 0) continue;
        const s = tbServer(tot, first), pw = s === A ? pa : 1 - pb, pr = dp[i][j];
        if (i + 1 === n) win += pr * pw; else dp[i + 1][j] += pr * pw;
        if (j + 1 < n) dp[i][j + 1] += pr * (1 - pw);
      }
    }
    const x = pa * (1 - pb), y = (1 - pa) * pb, tie = dp[n - 1][n - 1];
    return win + tie * ((x + y) > 0 ? x / (x + y) : 0.5);
  }
  const setCache = new Map();
  function setDist(pa, pb, first, rule) {
    const key = pa + "|" + pb + "|" + first + "|" + rule;
    let c = setCache.get(key); if (c) return c;
    if (setCache.size > 200000) setCache.clear();
    const ha = holdProb(pa), hb = holdProb(pb), bpa = expBP(pa), bpb = expBP(pb);
    const cap = rule === "adv" ? 20 : 6;
    const dp = new Map([["0,0", 1]]), scores = new Map();
    let eb = 0, ebp = 0, ptb = 0;
    const add = (m, k, v) => m.set(k, (m.get(k) || 0) + v);
    for (let tot = 0; tot < 2 * cap + 2; tot++) {
      for (const [k, pr] of Array.from(dp.entries())) {
        const [i, j] = k.split(",").map(Number); if (i + j !== tot) continue;
        if ((i >= 6 || j >= 6) && Math.abs(i - j) >= 2) { add(scores, i + "-" + j, pr); continue; }
        if (i === 6 && j === 6 && rule !== "adv") {
          ptb += pr; const tw = tiebreakProb(pa, pb, first, rule === "tb7" ? 7 : 10);
          add(scores, "7-6", pr * tw); add(scores, "6-7", pr * (1 - tw)); continue;
        }
        if (rule === "adv" && i === cap && j === cap) {
          const x = ha * (1 - hb), y = (1 - ha) * hb, w = x + y > 0 ? x / (x + y) : 0.5;
          add(scores, (cap + 2) + "-" + cap, pr * w); add(scores, cap + "-" + (cap + 2), pr * (1 - w)); continue;
        }
        const srv = (i + j) % 2 === 0 ? first : 1 - first, h = srv === A ? ha : hb;
        eb += pr * (1 - h); ebp += pr * (srv === A ? bpa : bpb);
        if (srv === A) { add(dp, (i + 1) + "," + j, pr * h); add(dp, i + "," + (j + 1), pr * (1 - h)); }
        else { add(dp, i + "," + (j + 1), pr * h); add(dp, (i + 1) + "," + j, pr * (1 - h)); }
      }
    }
    c = { scores: Array.from(scores.entries()).map(([k, v]) => { const [a, b] = k.split("-").map(Number); return [a, b, v]; }), eb, ebp, ptb };
    setCache.set(key, c); return c;
  }
  function matchWinProb(pa, pb, bo, fr) {
    const need = (bo >> 1) + 1; let out = 0;
    for (const first of [A, B]) {
      let st = new Map([[`0,0,${first}`, 1]]), win = 0;
      for (let s = 0; s < 2 * need - 1; s++) {
        const ns = new Map();
        for (const [k, pr] of st) {
          const [sa, sb, srv] = k.split(",").map(Number);
          const r = (sa === need - 1 && sb === need - 1) ? fr : "tb7";
          for (const [ga, gb, q] of setDist(pa, pb, srv, r).scores) {
            const nsa = sa + (ga > gb), nsb = sb + (gb > ga), nsrv = (ga + gb) % 2 === 0 ? srv : 1 - srv;
            if (nsa === need) win += pr * q; else if (nsb !== need) { const kk = `${nsa},${nsb},${nsrv}`; ns.set(kk, (ns.get(kk) || 0) + pr * q); }
          }
        }
        st = ns;
      }
      out += 0.5 * win;
    }
    return out;
  }
  function ghNodes(sigma, rho, n) {
    const [x, w] = GH[n], out = [];
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++)
      out.push([sigma * x[i], sigma * (rho * x[i] + Math.sqrt(Math.max(0, 1 - rho * rho)) * x[j]), w[i] * w[j]]);
    return out;
  }
  function mixtureWinProb(pa, pb, bo, fr, sigma, rho, n) {
    if (sigma <= 0) return matchWinProb(r6(pa), r6(pb), bo, fr);
    const la = logit(pa), lb = logit(pb); let s = 0;
    for (const [e1, e2, w] of ghNodes(sigma, rho, n)) s += w * matchWinProb(r6(sigmoid(la + e1)), r6(sigmoid(lb + e2)), bo, fr);
    return s;
  }
  /* exact analytic match distribution (no variation) */
  function matchDist(pa, pb, bo, fr) {
    const need = (bo >> 1) + 1, res = { pWin: 0, sets: {}, games: new Float64Array(MAXG), diff: new Float64Array(ND), pTb: 0, pDec: 0, eb: 0 };
    for (const first of [A, B]) {
      let st = new Map([[`0,0,${first},0`, (() => { const a = new Float64Array(MAXG * ND); a[DOFF] = 1; return a; })()]]);
      for (let s = 0; s < 2 * need - 1; s++) {
        const ns = new Map();
        for (const [k, arr] of st) {
          const [sa, sb, srv, tbf] = k.split(",").map(Number);
          let mass = 0; for (let t = 0; t < arr.length; t++) mass += arr[t];
          if (!mass) continue;
          const dec = sa === need - 1 && sb === need - 1, r = dec ? fr : "tb7";
          if (dec) res.pDec += 0.5 * mass;
          const sd = setDist(pa, pb, srv, r); res.eb += 0.5 * mass * sd.eb;
          for (const [ga, gb, pr] of sd.scores) {
            if (pr < 1e-15) continue;
            const nsa = sa + (ga > gb), nsb = sb + (gb > ga), nsrv = (ga + gb) % 2 === 0 ? srv : 1 - srv;
            const ntb = (tbf || (ga + gb === 13 && r !== "adv")) ? 1 : 0, kk = `${nsa},${nsb},${nsrv},${ntb}`;
            let tgt = ns.get(kk); if (!tgt) { tgt = new Float64Array(MAXG * ND); ns.set(kk, tgt); }
            const dg = ga + gb, dd = ga - gb;
            const gl = Math.min(MAXG - dg, fr === "adv" ? MAXG : 13 * s + 1), dlo = Math.max(0, -dd, DOFF - 7 * s - 1), dhi = Math.min(ND, ND - dd, DOFF + 7 * s + 2);
            for (let g = 0; g < gl; g++) for (let d = dlo; d < dhi; d++) {
              const v = arr[g * ND + d]; if (v) tgt[(g + dg) * ND + d + dd] += v * pr;
            }
          }
        }
        st = new Map();
        for (const [k, arr] of ns) {
          const [sa, sb] = k.split(",").map(Number), tbf = +k.split(",")[3];
          if (sa === need || sb === need) {
            let m = 0;
            for (let g = 0; g < MAXG; g++) for (let d = 0; d < ND; d++) { const v = arr[g * ND + d]; if (v) { res.games[g] += 0.5 * v; res.diff[d] += 0.5 * v; m += v; } }
            res.sets[`${sa}-${sb}`] = (res.sets[`${sa}-${sb}`] || 0) + 0.5 * m; if (tbf) res.pTb += 0.5 * m;
            if (sa > sb) res.pWin += 0.5 * m;
          } else st.set(k, arr);
        }
      }
    }
    return res;
  }
  function mixtureDist(pa, pb, bo, fr, sigma, rho, n) {
    const nodes = sigma > 0 ? ghNodes(sigma, rho, n) : [[0, 0, 1]], la = logit(pa), lb = logit(pb);
    const out = { pWin: 0, sets: {}, games: new Float64Array(MAXG), pTb: 0, pDec: 0, eb: 0 };
    for (const [e1, e2, w] of nodes) {
      const d = matchDist(r6(sigmoid(la + e1)), r6(sigmoid(lb + e2)), bo, fr);
      out.pWin += w * d.pWin; out.pTb += w * d.pTb; out.pDec += w * d.pDec; out.eb += w * d.eb;
      for (const k in d.sets) out.sets[k] = (out.sets[k] || 0) + w * d.sets[k];
      for (let g = 0; g < MAXG; g++) out.games[g] += w * d.games[g];
    }
    let m = 0; for (let g = 0; g < MAXG; g++) m += g * out.games[g]; out.gamesMean = m;
    return out;
  }
  /* seeded PRNG (mulberry32) */
  function rng(seed) {
    let a = seed >>> 0;
    const f = () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
    f.normal = () => { let u = 0, v = 0; while (u === 0) u = f(); v = f(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
    return f;
  }
  /* point-by-point Monte Carlo */
  function simulate(pa, pb, bo, fr, n, seed, sigma, rho) {
    const R = rng(seed), need = (bo >> 1) + 1, la = logit(pa), lb = logit(pb);
    const tot = new Int16Array(n), dif = new Int16Array(n), aw = new Uint8Array(n), sA = new Uint8Array(n), sB = new Uint8Array(n);
    const tbn = new Uint8Array(n); let brA = 0, brB = 0, bpA = 0, bpB = 0;
    const seqCount = new Map();
    const tgt = []; for (let s = 0; s < bo; s++) tgt.push(s === bo - 1 ? fr : "tb7");
    for (let m = 0; m < n; m++) {
      let PA = pa, PB = pb;
      if (sigma > 0) { const z1 = R.normal(), z2 = rho * z1 + Math.sqrt(Math.max(0, 1 - rho * rho)) * R.normal(); PA = sigmoid(la + sigma * z1); PB = sigmoid(lb + sigma * z2); }
      let server = R() < 0.5 ? A : B, sa = 0, sb = 0, ga = 0, gb = 0, setI = 0, TA = 0, TB = 0, ntb = 0, seq = "";
      while (sa < need && sb < need) {
        const rule = tgt[setI];
        if (ga === 6 && gb === 6 && rule !== "adv") {
          const target = rule === "tb7" ? 7 : 10; let x = 0, y = 0, k = 0;
          while (!((x >= target || y >= target) && Math.abs(x - y) >= 2)) {
            const s = tbServer(k, server), p = s === A ? PA : 1 - PB; if (R() < p) x++; else y++; k++;
          }
          if (x > y) ga++; else gb++; ntb++; server = 1 - server;
        } else {
          let x = 0, y = 0; const p = server === A ? PA : PB;
          while (!((x >= 4 || y >= 4) && Math.abs(x - y) >= 2)) {
            const sp = x, rp = y; // server points x, receiver points y
            if (rp >= 3 && rp > sp) { if (server === A) bpB++; else bpA++; }
            if (R() < p) x++; else y++;
          }
          const srvWon = x > y;
          if (server === A) { if (srvWon) ga++; else { gb++; brB++; } } else { if (srvWon) gb++; else { ga++; brA++; } }
          server = 1 - server;
        }
        const tb = rule !== "adv";
        if (((ga >= 6 || gb >= 6) && Math.abs(ga - gb) >= 2) || (tb && (ga === 7 && gb === 6 || gb === 7 && ga === 6))) {
          if (ga > gb) sa++; else sb++; TA += ga; TB += gb; seq += (seq ? "," : "") + ga + "-" + gb; ga = 0; gb = 0; setI++;
        }
      }
      tot[m] = TA + TB; dif[m] = TA - TB; aw[m] = sa > sb ? 1 : 0; sA[m] = sa; sB[m] = sb; tbn[m] = ntb;
      seqCount.set(seq, (seqCount.get(seq) || 0) + 1);
    }
    const out = { n, seed, pA: 0, sets: {}, gamesPmf: {}, diffPmf: {}, pTb: 0, pASet: 0, pBSet: 0, pDec: 0 };
    let sum = 0, sum2 = 0;
    for (let m = 0; m < n; m++) {
      out.pA += aw[m]; out.pTb += tbn[m] > 0; out.pASet += sA[m] >= 1; out.pBSet += sB[m] >= 1; out.pDec += (sA[m] + sB[m]) === bo;
      const k = sA[m] + "-" + sB[m]; out.sets[k] = (out.sets[k] || 0) + 1;
      out.gamesPmf[tot[m]] = (out.gamesPmf[tot[m]] || 0) + 1; out.diffPmf[dif[m]] = (out.diffPmf[dif[m]] || 0) + 1;
      sum += tot[m]; sum2 += tot[m] * tot[m];
    }
    for (const k of ["pA", "pTb", "pASet", "pBSet", "pDec"]) out[k] /= n;
    for (const k in out.sets) out.sets[k] /= n; for (const k in out.gamesPmf) out.gamesPmf[k] /= n; for (const k in out.diffPmf) out.diffPmf[k] /= n;
    out.gamesMean = sum / n; out.gamesSd = Math.sqrt(sum2 / n - out.gamesMean * out.gamesMean);
    const sorted = Array.from(tot).sort((a, b) => a - b), q = f => sorted[Math.min(n - 1, Math.floor(f * (n - 1)))];
    out.gamesMedian = (n % 2) ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    out.gamesP10 = percentile(sorted, 10); out.gamesP90 = percentile(sorted, 90);
    out.expBreaks = (brA + brB) / n; out.expBreaksA = brA / n; out.expBreaksB = brB / n; out.expBpA = bpA / n; out.expBpB = bpB / n;
    out.representative = {};
    for (const [s, c] of Array.from(seqCount.entries()).sort((a, b) => b[1] - a[1])) {
      const parts = s.split(","), a = parts.filter(x => { const [p, q] = x.split("-").map(Number); return p > q; }).length, k = a + "-" + (parts.length - a);
      if (!out.representative[k]) out.representative[k] = { sets: parts, p: c / n };
    }
    return out;
  }
  function percentile(sorted, p) { const idx = (sorted.length - 1) * p / 100, lo = Math.floor(idx), hi = Math.ceil(idx); return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo); }

  function fairOdds(p) {
    p = Math.min(Math.max(p, 1e-6), 1 - 1e-6); const am = p > 0.5 ? -100 * p / (1 - p) : 100 * (1 - p) / p;
    return { decimal: Math.round(100 / p) / 100, american: (am > 0 ? "+" : "") + Math.round(am) };
  }

  /* ---------- model (reads params/ratings exported by Python) ---------- */
  class Oracle {
    constructor(R) {
      this.R = R; this.P = R.params; this.version = R.model_version;
      this.byId = new Map(R.players.map(p => [p.id, p]));
      this.cutoff = {}; for (const t in R.data) this.cutoff[t] = R.data[t];
    }
    norm(s) { return s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/-/g, " ").replace(/[^a-z ]/g, " ").split(/\s+/).filter(Boolean).join(" "); }
    find(name, tour) {
      const q = this.norm(name); let best = null;
      for (const p of this.R.players) {
        if (tour && p.t !== tour) continue;
        const k = this.norm(p.n), parts = k.split(" "), rev = parts.length === 2 ? parts[1] + " " + parts[0] : null;
        let score = 0;
        if (k === q || rev === q) score = 3; else if (q.split(" ").every(t => parts.includes(t))) score = 2; else if (k.includes(q)) score = 1;
        if (score && (!best || score > best[0] || (score === best[0] && (p.lm || "") > (best[1].lm || "")))) best = [score, p];
      }
      return best ? best[1] : null;
    }
    components(a, b, tour, surf) {
      const si = SURF.indexOf(surf), base = this.R.baseline_logit[tour][surf];
      const la = base + a.S[si] - b.R[si], lb = base + b.S[si] - a.R[si];
      const pa = sigmoid(la), pb = sigmoid(lb);
      let psr = matchWinProb(Math.round(pa * 1e4) / 1e4, Math.round(pb * 1e4) / 1e4, 3, "tb7");
      psr = Math.min(Math.max(psr, 1e-4), 1 - 1e-4); const cap = this.P.sr_elo_cap;
      return { comps: { surface_elo: a.se[si] - b.se[si], overall_elo: a.e - b.e, serve_return: Math.max(-cap, Math.min(cap, 400 * Math.log10(psr / (1 - psr)))), form: a.f - b.f }, pa, pb };
    }
    dataQuality(a, b, surf, asof, tour) {
      const notes = []; let s = 2; const si = SURF.indexOf(surf), day = 86400000;
      for (const p of [a, b]) {
        const st = (p.st && p.st.matches_with_stats) || 0, n52 = p.l52[0] + p.l52[1], ns = p.sn[si];
        if (p.m < 10 || st < 3) { s = 0; notes.push(`${p.n}: sparse record (${p.m} tour matches, ${st} with serve stats in 52w)`); }
        else if (st < 15 || ns < 15 || n52 < 15) { s = Math.min(s, 1); notes.push(`${p.n}: limited recent/surface sample (${n52} matches 52w, ${ns} career on ${surf}, ${st} with stats)`); }
        if (p.ls) { const gap = Math.round((asof - new Date(p.ls)) / day); if (gap > 60) { s = Math.min(s, 1); notes.push(`${p.n}: latest point-level stats are ${gap} days old (${p.ls})`); } }
        if (p.lm && (asof - new Date(p.lm)) / day > 120) { s = Math.min(s, 1); notes.push(`${p.n}: no recorded match since ${p.lm}`); }
      }
      const cut = new Date(this.cutoff[tour].point_stats_cutoff), g = Math.round((asof - cut) / day);
      if (g > 45) { s = Math.min(s, 1); notes.push(`Point-level dataset cutoff ${this.cutoff[tour].point_stats_cutoff} is ${g} days before forecast date`); }
      return [["LOW", "MEDIUM", "HIGH"][s], notes];
    }
    solveSplit(pa, pb, target, sig, rho) {
      const la = logit(pa), lb = logit(pb); let lo = -3, hi = 3;
      for (let i = 0; i < 40; i++) { const mid = (lo + hi) / 2, p = mixtureWinProb(sigmoid(la + mid), sigmoid(lb - mid), 3, "tb7", sig, rho, 3); if (p < target) lo = mid; else hi = mid; if (hi - lo < 1e-5) break; }
      return (lo + hi) / 2;
    }
    forecast(o) {
      const P = this.P, a = this.byId.get(o.a), b = this.byId.get(o.b), tour = o.tour || a.t, surf = o.surface;
      const bo = o.bestOf || 3, fr = o.finalRule || "tb7", asof = o.date ? new Date(o.date) : new Date();
      const { comps, pa: pas, pb: pbs } = this.components(a, b, tour, surf);
      let paS = pas, pbS = pbs;
      if (o.indoor && P.indoor_logit_adj) { paS = sigmoid(logit(paS) + P.indoor_logit_adj); pbS = sigmoid(logit(pbS) + P.indoor_logit_adj); }
      const cap = P.context_max_elo, ctx = Math.max(-cap, Math.min(cap, (o.aAdj || 0) - (o.bAdj || 0)));
      const w = P.weights; let D = 0; for (const k of ["surface_elo", "serve_return", "overall_elo", "form"]) D += w[k] * comps[k];
      D = P.composite_scale * D + ctx;
      const target = eloProb(D), sig = P.variation.sigma, rho = P.variation.rho;
      const delta = this.solveSplit(paS, pbS, target, sig, rho), pa = sigmoid(logit(paS) + delta), pb = sigmoid(logit(pbS) - delta);
      const nS = o.sims || P.mc.n_sims, seed = o.seed != null ? o.seed : P.mc.seed;
      const sim = simulate(pa, pb, bo, fr, nS, seed, sig, rho);
      const ana = o.skipAnalytic ? null : mixtureDist(pa, pb, bo, fr, sig, rho, P.gh_nodes || 5);
      let [dq, notes] = this.dataQuality(a, b, surf, asof, tour);
      if (o.injuryUncertain) { dq = dq === "HIGH" ? "MEDIUM" : "LOW"; notes.push("Manual flag: injury/fitness uncertain"); }
      const rep = buildReport(a.n, b.n, bo, sim);
      const contrib = {}; for (const k in comps) contrib[k] = P.composite_scale * w[k] * comps[k];
      Object.assign(rep, {
        match: `${a.n} vs ${b.n}`, A: a, B: b, tour, surface: surf, indoor: !!o.indoor, best_of: bo, final_rule: fr,
        format: `Best of ${bo}, deciding set: ${RULES[fr]}`, tournament: o.tournament || "", round: o.round || "",
        data_cutoff: this.cutoff[tour].point_stats_cutoff, results_cutoff: this.cutoff[tour].results_cutoff, forecast_date: asof.toISOString().slice(0, 10),
        data_quality: dq, data_quality_notes: notes,
        model: { version: this.version, composite_elo_diff: D, components_elo: comps, weighted: contrib, target_bo3: target, sr_spw: [pas, pbs],
          point_probs: [pa, pb], hold: [holdProb(pa), holdProb(pb)], sigma: sig, rho, n_sims: nS, seed },
        analytic: ana ? { pWin: ana.pWin, pTb: ana.pTb, pDec: ana.pDec, gamesMean: ana.gamesMean, sets: ana.sets } : null,
        context: { aAdj: o.aAdj || 0, bAdj: o.bAdj || 0, injuryUncertain: !!o.injuryUncertain },
      });
      rep.drivers = Object.entries(contrib).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1])).slice(0, 3)
        .map(([k, v]) => ({ k, label: { surface_elo: "surface-specific Elo", overall_elo: "all-surface Elo", serve_return: "opponent-adjusted serve/return quality", form: "recent opponent-adjusted form" }[k], favours: v > 0 ? a.n : b.n, pts: v, raw: comps[k] }));
      rep.strongest = strongest(rep, dq);
      return rep;
    }
  }
  function thresholds(bo) {
    const t = [], h = bo === 3 ? [1.5, 2.5, 3.5, 4.5, 5.5] : [2.5, 3.5, 4.5, 5.5, 6.5, 7.5];
    if (bo === 3) for (let x = 17; x <= 25; x++) t.push(x + 0.5); else for (let x = 29; x <= 43; x++) t.push(x + 0.5);
    return [t, h];
  }
  function buildReport(na, nb, bo, sim) {
    const ss = Object.entries(sim.sets).sort((x, y) => y[1] - x[1]), top = ss[0][0], rep = sim.representative[top] || { sets: [], p: 0 };
    const [tl, hl] = thresholds(bo), gp = sim.gamesPmf, dp = sim.diffPmf;
    const sumIf = (pmf, f) => Object.entries(pmf).reduce((s, [k, v]) => s + (f(+k) ? v : 0), 0);
    const ou = tl.map(t => ({ line: t, over: sumIf(gp, g => g > t), under: sumIf(gp, g => g < t) }));
    const hc = hl.map(h => { const am = sumIf(dp, d => d > h), bm = sumIf(dp, d => d < -h); return { line: h, Aminus: am, Bplus: 1 - am, Bminus: bm, Aplus: 1 - bm }; });
    const aFav = sim.pA >= 0.5, fk = ss.find(([k]) => { const [x, y] = k.split("-").map(Number); return (x > y) === aFav; })[0];
    const [sa, sb] = fk.split("-").map(Number), frep = sim.representative[fk] || { sets: [] };
    return {
      predicted_winner: sim.pA >= 0.5 ? na : nb, win_prob: [sim.pA, 1 - sim.pA], fair_odds: [fairOdds(sim.pA), fairOdds(1 - sim.pA)],
      set_score_probs: ss, most_likely_set_score: top, representative_sets: rep.sets, representative_p: rep.p,
      games: { mean: sim.gamesMean, median: sim.gamesMedian, p10: sim.gamesP10, p90: sim.gamesP90, sd: sim.gamesSd, pmf: gp }, diff_pmf: dp,
      over_under: ou, handicaps: hc, p_tiebreak: sim.pTb, p_a_set: sim.pASet, p_b_set: sim.pBSet, p_decider: sim.pDec,
      breaks: { total: sim.expBreaks, A: sim.expBreaksA, B: sim.expBreaksB }, bp: { A: sim.expBpA, B: sim.expBpB },
      final_score: { A: sa, B: sb, sets: frep.sets, prob: sim.sets[fk] },
    };
  }
  function strongest(r, dq) {
    const na = r.A.n, nb = r.B.n, c = [];
    c.push([`${na} to win`, r.win_prob[0], "winner"], [`${nb} to win`, r.win_prob[1], "winner"],
      [`${na} to win at least one set`, r.p_a_set, "set"], [`${nb} to win at least one set`, r.p_b_set, "set"]);
    for (const [k, v] of r.set_score_probs) c.push([`Exact sets ${na} ${k} ${nb}`, v, "exact"]);
    const core = r.best_of === 3 ? [18.5, 20.5, 22.5, 24.5] : [33.5, 36.5, 38.5, 40.5];
    for (const o of r.over_under) if (core.includes(o.line)) c.push([`Over ${o.line} games`, o.over, "total"], [`Under ${o.line} games`, o.under, "total"]);
    for (const h of r.handicaps) if (h.line === 2.5 || h.line === 4.5)
      if (r.win_prob[0] >= 0.5) c.push([`${na} -${h.line} games`, h.Aminus, "handicap"], [`${nb} +${h.line} games`, h.Bplus, "handicap"]);
      else c.push([`${nb} -${h.line} games`, h.Bminus, "handicap"], [`${na} +${h.line} games`, h.Aplus, "handicap"]);
    c.push(["At least one tiebreak", r.p_tiebreak, "tb"], ["No tiebreak", 1 - r.p_tiebreak, "tb"], ["Deciding set played", r.p_decider, "decider"], ["No deciding set", 1 - r.p_decider, "decider"]);
    const el = c.filter(x => x[1] <= 0.92), sel = (el.length ? el : c).reduce((m, x) => x[1] > m[1] ? x : m);
    const p = sel[1], conf = (p >= 0.75 && dq === "HIGH") ? "High" : ((p >= 0.62 && dq !== "LOW") ? "Medium" : "Low");
    const risks = {
      winner: `Upset through serve-dominant variance: P(at least one tiebreak) = ${(100 * r.p_tiebreak).toFixed(0)}%; underdog wins ${(100 * Math.min(...r.win_prob)).toFixed(0)}% of simulations.`,
      set: "Straight-sets result for the opponent; driven by tiebreak/hold variance.",
      total: `Total-games distribution is wide (sd ${r.games.sd.toFixed(1)}; p10-p90 ${r.games.p10.toFixed(0)}-${r.games.p90.toFixed(0)}); one tiebreak or a deciding set swings it.`,
      handicap: "Game margin is highly sensitive to a deciding set and to break-point conversion luck.",
      tb: "Tiebreak occurrence hinges on hold rates; small serve-model misspecification moves it materially.",
      decider: "Deciding-set probability depends on the closeness estimate; rating error widens it.", exact: "Exact set scores are low-probability outcomes by nature.",
    };
    let risk = risks[sel[2]]; if (dq !== "HIGH") risk += ` Data quality ${dq}: ${r.data_quality_notes.slice(0, 2).join("; ")}`;
    return { selection: sel[0], probability: p, fair_odds: fairOdds(p), confidence: conf, main_risk: risk, candidates: c.sort((x, y) => y[1] - x[1]).slice(0, 12) };
  }
  G.TennisOracle = { Oracle, holdProb, tiebreakProb, setDist, matchWinProb, matchDist, mixtureDist, simulate, fairOdds, SURF, RULES };
  if (typeof module !== "undefined") module.exports = G.TennisOracle;
})(typeof window !== "undefined" ? window : globalThis);
