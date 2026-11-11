import assert from 'node:assert/strict';
import {sendPart,DiscordDeliveryError} from './discord-delivery.mjs';
const base='https://discord.test/webhooks/123/fake-token';
let calls=[],renders=0;
const fake=async(url,opts)=>{calls.push({url,method:opts.method,body:JSON.parse(opts.body)});return calls.length===1?Response.json({code:10008},{status:404}):new Response(null,{status:204});};
let sent=await sendPart({base,index:0,body:()=>({content:'Forecast '+(++renders),allowed_mentions:{parse:['everyone']}}),fetcher:fake});
assert.equal(calls.length,2);assert.equal(calls[0].method,'PATCH');assert.equal(calls[1].method,'POST');assert.equal(sent.body.content,'Forecast 2');assert.deepEqual(sent.body.allowed_mentions.parse,[]);
calls=[];renders=0;
sent=await sendPart({base,index:0,body:()=>({content:++renders===1?'BET':'NO BET — quote expired'}),sleep:async()=>{},fetcher:async(url,opts)=>{calls.push(JSON.parse(opts.body));return calls.length===1?Response.json({retry_after:0},{status:429}):new Response(null,{status:204});}});
assert.equal(sent.body.content,'NO BET — quote expired');assert.equal(calls.length,2);
for(const [status,code]of [[404,10015],[403,50013],[400,50035]]){
 let n=0;await assert.rejects(sendPart({base,index:0,body:{content:'x'},fetcher:async()=>{n++;return Response.json({code},{status});}}),e=>e instanceof DiscordDeliveryError&&e.discordCode===code&&!e.message.includes('fake-token'));assert.equal(n,1);
}
let n=0;await assert.rejects(sendPart({base,index:0,startedAt:Date.now()-15*60000,body:{content:'x'},fetcher:async()=>{n++;}}));assert.equal(n,0);
console.log('Discord deleted-original recovery, bounded retries, fresh re-render, expiry and secret-safe errors: passed');
