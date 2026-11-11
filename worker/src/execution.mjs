// Post-forecast execution checks only. Neither prices nor books enter player ratings.
export function depthAtAsk(book,ask){
 if(!Array.isArray(book?.yes)||!Array.isArray(book?.no)||!(ask>0&&ask<1))return 0;
 const valid=rows=>rows.filter(x=>Array.isArray(x)&&x.length>=2).map(x=>x.map(Number)).filter(([p,n])=>Number.isFinite(p)&&p>0&&p<100&&Number.isFinite(n)&&n>0);
 const yes=valid(book.yes);if(!yes.length)return 0;
 const bid=Math.max(...yes.map(x=>x[0]))/100;
 if(bid>ask+1e-7||ask-bid>.10+1e-7)return 0;
 return valid(book.no).reduce((sum,[p,n])=>100-p<=ask*100+1e-7?sum+n:sum,0);
}
export async function verifyRows(slate,rows,fetchBook,{now=()=>Date.now()}={}){
 const result=[];
 for(const original of rows){
  const row={...original,outcomes:(original.outcomes||[]).map(o=>({...o,eligible:false,executionVerified:false}))};
  const match=slate.matches.find(m=>String(m.espn_id)===String(row.espn_id));
  const fresh=()=>match&&match.status==='STATUS_SCHEDULED'&&Number.isFinite(Date.parse(match.start_utc))&&now()<Date.parse(match.start_utc);
  if(!fresh()||['no_market','input_quality','invalid_order','no_forecast','excluded','not_prematch'].includes(row.status)){result.push({...row,best:null,n_eligible:0});continue;}
  const candidates=(original.outcomes||[]).filter(o=>o.eligible).sort((a,b)=>b.ev_per_1-a.ev_per_1||b.model_p-a.model_p);
  let best=null;
  for(const candidate of candidates.slice(0,12)){
   const out=row.outcomes.find(o=>o.market_id===candidate.market_id);out.requiredDepth=Math.max(10,Math.ceil(5/candidate.price));
   try{
    const book=await fetchBook(candidate.market_id);out.executionDepth=depthAtAsk(book,candidate.price);out.executionCheckedAt=new Date(now()).toISOString();
    if(!fresh()||out.executionDepth<out.requiredDepth)throw Error('fresh depth below minimum at quoted ask');
    out.executionVerified=true;out.eligible=true;best=out;break;
   }catch(e){out.executionRejection='Live executable depth not verified';}
  }
  row.best=best;row.n_eligible=best?1:0;
  if(best){row.status='bet';row.verdict=`${best.selection} @ ${best.price.toFixed(2)} (model ${(100*best.model_p).toFixed(1)}%, verified execution depth)`;}
  else if(candidates.length){row.status='execution_blocked';row.verdict='NO BET (positive model candidate; executable depth unavailable or insufficient)';}
  result.push(row);
 }
 return result;
}
