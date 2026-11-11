import copy,json,subprocess
from pathlib import Path
import pandas as pd
from tennis_oracle.quality import recommendation_quality
from tennis_oracle import jupiter as J
ROOT=Path(__file__).resolve().parents[1]

def profile(stats='2026-10-20'):
    return {'name':'Player','last_stats_match':stats}

def test_performance_evidence_age_boundaries():
    def check(stats='2026-10-20',cutoff='2026-10-20',q='HIGH',inj=False):
        return recommendation_quality(profile(stats),profile(),pd.Timestamp('2026-11-02'),cutoff,q,inj)['eligible']
    assert check() and check('2026-09-03','2026-09-18')
    assert not check('2026-09-02')
    for value in [None,'2026-01-01','2026-11-03','bad']:
        assert not check(value)
        assert not check(cutoff=value)
    assert not check(q='LOW') and not check(inj=True)

def test_quality_and_sampling_checks_python_js_parity():
    F=json.loads((ROOT/'tests/fixtures/markets_fixture.json').read_text())
    m=next(m for m in F['slate']['matches'] if m.get('forecast') and J.match_event(m,F['events']))
    m=copy.deepcopy(m);m['tour']='atp';m['start_utc']='2099-01-01T00:00:00Z';m['status']='STATUS_SCHEDULED'
    ev=J.match_event(m,F['events']) or F['events'][0]
    m['forecast']['n_sims']=100000
    for eligible in [None,False,True]:
        m['forecast']['recommendation_quality']={'eligible':eligible}
        py=J.evaluate(m,ev,F['snapshot_at_utc'])
        script="const M=require('./docs/markets');const x=JSON.parse(process.argv[1]);console.log(JSON.stringify(M.evaluate(x.m,x.ev,{snapshot_at_utc:x.at})));"
        js=json.loads(subprocess.check_output(['node','-e',script,json.dumps({'m':m,'ev':ev,'at':F['snapshot_at_utc']})],cwd=ROOT,text=True))
        assert js==py
        if eligible is not True:assert py['status']=='input_quality' and py['best'] is None
        else:
            for o in py['outcomes']:
                if o.get('eligible'):assert o['model_p']>.5 and o['ev_per_1']>0 and o['mc_lower_95']>.5
