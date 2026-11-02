import pathlib,sys,json,copy,time
import numpy as np,pandas as pd
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tennis_oracle.backtest import collect
from tennis_oracle.config import load_params
from tennis_oracle.grid import mixture_field,get_grid
P=load_params()
def distributions(df,p):
 w=p['weights'];D=p['composite_scale']*sum(w[k]*df['c_'+k].values for k in ['surface_elo','serve_return','overall_elo','form']);target=1/(1+10**(-D/400))
 sig,rho=p['variation']['sigma'],p['variation']['rho'];la=np.log(df.pa_sr.values/(1-df.pa_sr.values));lb=np.log(df.pb_sr.values/(1-df.pb_sr.values));lo=np.full(len(df),-3.);hi=np.full(len(df),3.)
 for _ in range(30):
  mid=(lo+hi)/2;pa=1/(1+np.exp(-(la+mid)));pb=1/(1+np.exp(-(lb-mid)));q=mixture_field('p_win',pa,pb,3,'tb7',sig,rho,3);lo=np.where(q<target,mid,lo);hi=np.where(q<target,hi,mid)
 mid=(lo+hi)/2;pa=1/(1+np.exp(-(la+mid)));pb=1/(1+np.exp(-(lb-mid)))
 return {k:mixture_field(k,pa,pb,3,'tb7',sig,rho,3)for k in ['p_win','games','diff']}
def evaluate(df,p):
 z=distributions(df,p);ys=df.y.values;ps=np.clip(z['p_win'],1e-7,1-1e-7);out=pd.DataFrame({'date':df.date.values,'tour':df.tour.values,'winner_logloss':-ys*np.log(ps)-(1-ys)*np.log(1-ps)})
 for field,target in [('games',df.games.values),('diff',df['diff'].values)]:
  pmf=z[field];pmf=pmf/pmf.sum(1)[:,None];cdf=np.cumsum(pmf,axis=1);axis=np.arange(pmf.shape[1])-(40 if field=='diff' else 0)
  out[field+'_crps']=((cdf-(axis[None,:]>=target[:,None]))**2).sum(1)
 return out
if __name__=='__main__':
 print('Preparing exact scoring grid',flush=True);get_grid(3,'tb7')
 frames=[]
 for tour in ['atp','wta']:
  f=ROOT/'research'/f'features-{tour}.parquet'
  if f.exists():df=pd.read_parquet(f)
  else:print('Collecting chronological',tour,flush=True);df,_=collect(tour,P,start='2024-01-01');df.to_parquet(f)
  df=df[(df.bo==3)&(df.fr=='tb7')&(df.date<'2026-11-01')].reset_index(drop=True);print(tour,len(df),'matches',flush=True)
  for name in ['pooled','tour_specific']:
   p=copy.deepcopy(P)
   if name=='tour_specific':p['variation']['sigma'],p['variation']['rho']=P['variation']['by_tour'][tour]
   o=evaluate(df,p);o['variant']=name;frames.append(o);print(tour,name,'done',flush=True)
 out=pd.concat(frames,ignore_index=True);out.to_parquet(ROOT/'research/distribution-scores.parquet')
 summary={}
 for split,mask in [('validation_2024',out.date.dt.year==2024),('heldout_2025_26',out.date.dt.year>=2025)]:
  part=out[mask];summary[split]=part.groupby(['variant','tour'])[['games_crps','diff_crps','winner_logloss']].mean().reset_index().to_dict('records')
 (ROOT/'research/distribution-results.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
