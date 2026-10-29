import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import worker from '../worker/src/index.js';
const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
const pub = Buffer.from(await crypto.subtle.exportKey('raw', pair.publicKey)).toString('hex');
const env = { DISCORD_PUBLIC_KEY: pub, DISCORD_APPLICATION_ID: '123', GITHUB_TOKEN: 'fake-test-key', RELAY_KEY: 'test-relay' };
const date = '2026-10-30';
let calls = [], pending;
globalThis.fetch = async (url, opts) => { calls.push({ url: String(url), opts }); return new Response(null, { status: 204 }); };
async function invoke(data, settings=env) {
  const body = JSON.stringify(data), ts = String(Math.floor(Date.now()/1000));
  const signature = Buffer.from(await crypto.subtle.sign('Ed25519', pair.privateKey, new TextEncoder().encode(ts+body))).toString('hex');
  return worker.fetch(new Request('https://worker.test/interactions', { method: 'POST', body,
    headers: { 'X-Signature-Ed25519': signature, 'X-Signature-Timestamp': ts } }), settings, { waitUntil: p => { pending=p; } });
}
const interaction = { type: 2, token: 'fake.interaction.token', data: { name: 'tennis', options: [{ name: 'date', value: date }] } };
assert.equal((await (await invoke({ type: 1 })).json()).type, 1);
assert.equal((await (await invoke(interaction)).json()).type, 5);
await pending;
const dispatch = calls.find(x => x.url.includes('/dispatches'));
assert.ok(dispatch.url.endsWith('/discord-tennis.yml/dispatches'));
const payload = JSON.parse(dispatch.opts.body);
assert.equal(payload.inputs.date, date);
assert.ok(!dispatch.opts.body.includes(interaction.token));
const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'oracle-relay-'));
try {
  const githubEnv = path.join(tmp, 'env');
  const childEnv = { ...process.env, DATE: date, RELAY_KEY: env.RELAY_KEY, RELAY_PAYLOAD: payload.inputs.relay, GITHUB_ENV: githubEnv };
  const run = (extra={}) => spawnSync(process.execPath, ['scripts/discord-relay.mjs', 'decrypt'], { env: { ...childEnv, ...extra }, encoding: 'utf8' });
  assert.equal(run().status, 0);
  assert.ok((await fs.readFile(githubEnv, 'utf8')).includes(`INTERACTION_TOKEN=${interaction.token}`));
  assert.notEqual(run({ RELAY_KEY: 'wrong-key' }).status, 0);
  assert.notEqual(run({ DATE: '2026-10-31' }).status, 0);
} finally { await fs.rm(tmp, { recursive: true, force: true }); }
calls=[];
assert.equal((await (await invoke({ ...interaction, data: { ...interaction.data, options: [{ name: 'date', value: 'bad' }] } })).json()).type, 4);
assert.equal(calls.length, 0);
await invoke(interaction, { ...env, GITHUB_TOKEN: '' }); await pending;
assert.ok(calls.some(x => x.url.includes('discord.com') && JSON.parse(x.opts.body).content.includes('Could not start')));
console.log('signed command → matching workflow → encrypted relay: passed (all HTTP mocked)');
