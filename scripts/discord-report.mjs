import fs from 'node:fs/promises';
await import('../docs/engine.js');
const marketModule = await import('../docs/markets.js');
globalThis.TOMarkets ||= marketModule.default;
const { buildReport } = await import('../worker/src/index.js');
const date = process.env.DATE;
if (!/^\d{4}-\d{2}-\d{2}$/.test(date || '')) throw new Error('Invalid date');
const dash = 'https://lucksociety.github.io/tennis-oracle/';
// Keep the fresh local slate; never replace it with a stale published forecast.
const net = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (String(url).startsWith(dash + 'data/')) {
    try { return new Response(await fs.readFile('docs/data/' + new URL(url).pathname.split('/data/')[1]), { status: 200 }); }
    catch (e) { if (e.code === 'ENOENT') return new Response('', { status: 404 }); throw e; }
  }
  return net(url, opts);
};
await fs.access(`docs/data/slate-${date}.json`);
const parts = await buildReport({ DASHBOARD_URL: dash, JUPITER_API_KEY: process.env.JUPITER_API_KEY, ENGINE_FALLBACK: 'off' }, date, 'all');
await fs.mkdir('out', { recursive: true });
await fs.writeFile('out/discord.json', JSON.stringify(parts.map(content => ({ content, flags: 4 }))));
