// Shared transport only: caller renders frozen forecasts and rechecks bet freshness per attempt.
export class DiscordDeliveryError extends Error {
  constructor(status,code){super(`Discord delivery HTTP ${status}${Number.isInteger(code)?` (code ${code})`:''}`);this.status=status;this.discordCode=code;this.unavailable=status===401||status===403||(status===404&&code===10015);}
}
export async function sendPart({base,index,body,fetcher=globalThis.fetch,sleep=ms=>new Promise(r=>setTimeout(r,ms)),startedAt=process.env.INTERACTION_STARTED_AT}) {
  let followup=index>0;
  for(let attempt=0;attempt<5;attempt++){
    const start=Number(startedAt);
    if(start>0&&Date.now()-start>=14.5*60000)throw new DiscordDeliveryError(404,10015);
    const payload=typeof body==='function'?await body():body;
    if(!payload)return null;
    const safe={...payload,allowed_mentions:{parse:[]}};
    const r=await fetcher(followup?base:base+'/messages/@original',{method:followup?'POST':'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify(safe)});
    if(r.ok)return {body:safe,route:followup?'followup':'original',status:r.status};
    const detail=await r.json().catch(()=>({}));
    // A deleted original reply does not invalidate the interaction webhook.
    if(r.status===404&&detail.code===10008&&!followup){followup=true;continue;}
    if(r.status===429){const retry=Number(detail.retry_after);await sleep(Math.min(30000,Math.max(100,Number.isFinite(retry)?retry*1000+100:1100)));continue;}
    throw new DiscordDeliveryError(r.status,Number.isInteger(detail.code)?detail.code:null);
  }
  throw new DiscordDeliveryError(429,null);
}
