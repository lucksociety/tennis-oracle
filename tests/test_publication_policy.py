import copy,json,subprocess
from pathlib import Path
from tennis_oracle import jupiter as J
ROOT=Path(__file__).resolve().parents[1]
F=json.loads((ROOT/'tests/fixtures/markets_fixture.json').read_text())

def test_scheduled_status_cannot_override_clock_or_live_market():
    m=copy.deepcopy(next(m for m in F['slate']['matches']if m.get('forecast') and J.match_event(m,F['events'])))
    ev=J.match_event(m,F['events'])
    assert ev
    m['status']='STATUS_SCHEDULED';m['start_utc']='2020-01-01T00:00:00Z'
    assert J.evaluate(m,ev,F['snapshot_at_utc'])['best']is None
    m['start_utc']='2099-01-01T00:00:00Z'
    assert J.evaluate(m,{**ev,'isLive':True},F['snapshot_at_utc'])['best']is None

def test_ranked_bets_also_require_probability_and_ev():
    js="""const assert=require('node:assert/strict');const M=require('./docs/markets');
    const rows=[{status:'bet',outcomes:[
    {model_p:.49,ev_per_1:2,implied:.2,eligible:false},
    {model_p:.5,ev_per_1:1,implied:.3,eligible:false},
    {model_p:.8,ev_per_1:-.1,implied:.9,eligible:false},
    {model_p:.6,ev_per_1:.1,implied:.5,eligible:true}]}];
    const b=M.leaderboards(rows,10);assert.equal(b.top_ev.length,1);assert.equal(b.top_probability.length,1);
    assert.equal(b.top_ev[0].model_p,.6);"""
    subprocess.run(['node','-e',js],cwd=ROOT,check=True)
