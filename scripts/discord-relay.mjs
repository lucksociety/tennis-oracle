// Shared CLI relay contract: decrypt first; post/fail never print interaction credentials.
import fs from 'node:fs/promises';
import {sendPart} from './discord-delivery.mjs';
import {spawnSync} from 'node:child_process';
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
  await fs.appendFile(process.env.GITHUB_ENV, `INTERACTION_TOKEN=${msg.token}\nDISCORD_APPLICATION_ID=${msg.app}\nINTERACTION_STARTED_AT=${msg.ts}\nONLY_BETS=${msg.only_bets ? '1' : ''}\n`);
} else {
  const app = process.env.DISCORD_APPLICATION_ID, token = process.env.INTERACTION_TOKEN;
  if (!app || !token) throw new Error('No decrypted interaction delivery route');
  if(mode==='fail'){try{if(JSON.parse(await fs.readFile('out/delivery-error.json','utf8')).interactionUnavailable){console.log('Discord interaction unavailable; model artifacts retained. Run the command again.');process.exit(0);}}catch(e){if(e.code!=='ENOENT')throw e;}}
  const messages = mode === 'fail' ? [{ content: `⚠️ Model run failed. See ${process.env.RUN_URL}` }] : JSON.parse(await fs.readFile('out/discord.json', 'utf8'));
  for (let i = 0; i < messages.length; i++) {
    const render=()=>{
      if(mode==='post'&&process.env.DELIVERY_RENDER_SCRIPT){
        const r=spawnSync(process.execPath,[process.env.DELIVERY_RENDER_SCRIPT],{stdio:'pipe'});
        if(r.status!==0)throw new Error('Fresh publication rendering failed');
        return fs.readFile('out/discord.json','utf8').then(JSON.parse).then(rows=>rows[i]);
      }
      return messages[i];
    };
    try{
      const sent=await sendPart({base:`https://discord.com/api/v10/webhooks/${app}/${token}`,index:i,body:render});
      if(!sent)break;
    }catch(e){
      await fs.mkdir('out',{recursive:true});
      await fs.writeFile('out/delivery-error.json',JSON.stringify({failedPart:i,httpStatus:e.status||null,discordCode:e.discordCode||null,interactionUnavailable:!!e.unavailable,at:new Date().toISOString()}));
      throw e;
    }
  }
}
