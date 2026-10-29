// Shared CLI relay contract: decrypt first; post/fail never print interaction credentials.
import fs from 'node:fs/promises';
const mode = process.argv[2];
if (mode === 'decrypt') {
  const raw = Buffer.from(process.env.RELAY_PAYLOAD || '', 'base64url');
  if (!process.env.RELAY_KEY || raw.length < 29) throw new Error('Missing relay configuration');
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(process.env.RELAY_KEY));
  const key = await crypto.subtle.importKey('raw', digest, 'AES-GCM', false, ['decrypt']);
  const msg = JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: raw.subarray(0, 12) }, key, raw.subarray(12))));
  if (!Number.isFinite(msg.ts) || Date.now() - msg.ts > 14 * 60000 || msg.ts > Date.now() + 60000) throw new Error('Interaction relay expired');
  if (!/^[A-Za-z0-9_.-]+$/.test(msg.token) || !/^\d+$/.test(msg.app) || msg.date !== process.env.DATE) throw new Error('Invalid relay context');
  console.log(`::add-mask::${msg.token}`);
  await fs.appendFile(process.env.GITHUB_ENV, `INTERACTION_TOKEN=${msg.token}\nDISCORD_APPLICATION_ID=${msg.app}\nONLY_BETS=${msg.only_bets ? '1' : ''}\n`);
} else {
  const app = process.env.DISCORD_APPLICATION_ID, token = process.env.INTERACTION_TOKEN;
  if (!app || !token) throw new Error('No decrypted interaction delivery route');
  const messages = mode === 'fail' ? [{ content: `⚠️ Model run failed. See ${process.env.RUN_URL}` }] : JSON.parse(await fs.readFile('out/discord.json', 'utf8'));
  for (let i = 0; i < messages.length; i++) {
    let sent = false;
    for (let attempt = 0; attempt < 4; attempt++) {
      const url = `https://discord.com/api/v10/webhooks/${app}/${token}${i ? '' : '/messages/@original'}`;
      const r = await fetch(url, { method: i ? 'POST' : 'PATCH', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ ...messages[i], allowed_mentions: { parse: [] } }) });
      if (r.ok) { sent = true; break; }
      if (r.status !== 429) throw new Error(`Discord delivery HTTP ${r.status}`);
      const j = await r.json(); await new Promise(resolve => setTimeout(resolve, Math.min(10000, 1000 * (j.retry_after || 1) + 100)));
    }
    if (!sent) throw new Error('Discord rate limit exhausted');
  }
}
