import copy
from datetime import date
import json
from pathlib import Path

import pandas as pd
import pytest
from tennis_oracle import live_stats as S


def counts():
    return {p+k:v for p in ('w_','l_') for k,v in zip(S.COUNTS,[4,2,60,40,30,10,10,3,5])}


def result():
    return dict(winner_name='Ann Alpha',loser_name='Bea Beta',winner_id='wta:archive1',loser_id='wta:archive2',
                score='6-3 6-4',best_of=3,tourney_name='Sample Event',venue='Somewhere, Country',date=pd.Timestamp('2026-10-30'),surface='Hard')


def source():
    return dict(source='wta-official',key='one',winner_name='Ann Alpha',loser_name='Bea Beta',
                winner_source_id='123',loser_source_id='456',score='6-3 6-4',best_of=3,
                date='2026-10-30',precision='day',surface='Clay',stats=counts(),event='Sample Event',city='Somewhere')


def put(monkeypatch,tmp_path,rows):
    monkeypatch.setattr(S,'CACHE',tmp_path)
    S._save(tmp_path/'wta.json',{'schema':1,'rows':rows})


def test_enrich_adds_counts_without_adding_or_changing_results(monkeypatch,tmp_path):
    put(monkeypatch,tmp_path,{'one':source()})
    base=pd.DataFrame([result()]);new,info=S.enrich(base,'wta')
    assert len(new)==len(base)==1 and info['applied']==1
    assert new.w_svpt.iloc[0]==60 and new.surface.iloc[0]=='Clay'
    for key in ['winner_id','loser_id','winner_name','loser_name','score','best_of','date']:
        assert new[key].equals(base[key])
    # Source IDs differ from archive IDs; counts join to the established model identities.
    assert new.winner_id.iloc[0]=='wta:archive1'
    again,_=S.enrich(new,'wta');assert again.equals(new)


@pytest.mark.parametrize('change',[{'score':'6-2 6-4'},{'winner_name':'Other Player'},{'date':'2026-10-20'},{'best_of':5}])
def test_no_fuzzy_or_wrong_score_date_format_join(monkeypatch,tmp_path,change):
    row=source();row.update(change);put(monkeypatch,tmp_path,{'one':row})
    _,info=S.enrich(pd.DataFrame([result()]),'wta');assert info['applied']==0


def test_ambiguous_match_and_identity_are_rejected(monkeypatch,tmp_path):
    put(monkeypatch,tmp_path,{'one':source()})
    _,info=S.enrich(pd.DataFrame([result(),result()]),'wta');assert info['applied']==0
    extra=result();extra.update(winner_id='wta:other-id',score='6-1 6-1')
    _,info=S.enrich(pd.DataFrame([result(),extra]),'wta');assert info['applied']==0


def test_impossible_counts_and_unfinished_scores_rejected():
    assert S.valid_counts(counts())
    for key,value in [('w_svpt',0),('w_1stWon',50),('w_2ndWon',21),('w_df',21),('w_bpSaved',6),('w_ace',float('nan')),('l_svpt',True)]:
        c=counts();c[key]=value;assert not S.valid_counts(c)
    for score in ['6-3 2-1','6-3 7-7','6-3 6-5','6-3 RET','W/O']:
        assert S.score_key(score) is None


def test_cache_not_replaced_on_bad_download(monkeypatch,tmp_path):
    monkeypatch.setattr(S,'CACHE',tmp_path)
    old={'schema':1,'rows':{'known':source()}}
    S._save(tmp_path/'atp.json',old)
    monkeypatch.setattr(S,'_get',lambda *a,**k:b'<html>not statistics</html>')
    with pytest.raises(ValueError):S.refresh_atp(date(2026,6,1),date(2026,11,2))
    assert json.loads((tmp_path/'atp.json').read_text())==old


def test_wta_counts_winner_orientation_and_identity_validation():
    event={'tournamentGroup':{'id':1145},'year':2026,'surface':'Clay'}
    match={'DrawMatchType':'S','MatchState':'F','Winner':'3','EventID':'1145','EventYear':2026,
           'MatchID':'LS001','MatchTimeStamp':'2026-11-01T21:48Z','PlayerNameFirstA':'Tatjana','PlayerNameLastA':'Maria',
           'PlayerNameFirstB':'Bianca','PlayerNameLastB':'Andreescu','PlayerIDA':'310440','PlayerIDB':'325088',
           'ScoreSet1A':'1','ScoreSet1B':'6','ScoreSet2A':'6','ScoreSet2B':'2','ScoreSet3A':'3','ScoreSet3B':'6'}
    stat={'eventid':'1145','eventyear':2026,'matchid':'LS001','setnum':0}
    for side,sv in [('a',110),('b',86)]:
        stat.update({'aces'+side:4,'dblflt'+side:6,'totservplayed'+side:sv,'ptsplayed1stserv'+side:53,
                     'ptswon1stserv'+side:33,'ptstotwonserv'+side:48,'servgamesplayed'+side:12,
                     'breakptsplayed'+side:9,'breakptsconv'+side:4})
    r=S.wta_row(event,match,[stat],date(2026,11,2))
    assert r['winner_name']=='Bianca Andreescu' and r['score']=='6-1 2-6 6-3'
    assert r['stats']['w_svpt']==86 and r['stats']['l_svpt']==110 and r['stats']['w_2ndWon']==15
    assert S.wta_row(event,match,[{**stat,'matchid':'wrong'}],date(2026,11,2)) is None
    assert S.wta_row(event,{**match,'Winner':'5'},[stat],date(2026,11,2)) is None
    assert S.wta_row(event,match,[stat],date(2026,10,31)) is None


def test_revision_changes_for_new_stats_with_same_latest_date(monkeypatch,tmp_path):
    row=source();put(monkeypatch,tmp_path,{'one':row})
    _,a=S.enrich(pd.DataFrame([result()]),'wta')
    row['stats']['w_1stWon']-=1;put(monkeypatch,tmp_path,{'one':row})
    _,b=S.enrich(pd.DataFrame([result()]),'wta')
    assert a['latest_stats_match']==b['latest_stats_match'] and a['revision']!=b['revision']


def test_official_current_edition_surface_is_unambiguous(monkeypatch,tmp_path):
    monkeypatch.setattr(S,'CACHE',tmp_path)
    e={'name':'CALI','title':'Cali Open','city':'CALI','start':'2026-11-02','end':'2026-11-08','surface':'Clay'}
    S._save(tmp_path/'wta.json',{'schema':1,'rows':{},'events':{'one':e}})
    assert S.fixture_surface('Cali Open','Cali, Colombia',date(2026,11,2),'wta')==('Clay','official-wta-current-edition')
    assert S.fixture_surface('Cali Open','Cali, Colombia',date(2026,10,20),'wta') is None
    assert S.fixture_surface('Other','Elsewhere',date(2026,11,2),'wta') is None
    S._save(tmp_path/'wta.json',{'schema':1,'rows':{},'events':{'one':e,'two':{**e,'surface':'Hard'}}})
    assert S.fixture_surface('Cali Open','Cali, Colombia',date(2026,11,2),'wta') is None


def test_calendar_does_not_accept_empty_or_repeating_pages(monkeypatch):
    monkeypatch.setattr(S,'_json',lambda *a,**k:{'content':[]})
    with pytest.raises(ValueError):S._calendar(date(2026,6,1),date(2026,11,2))
    e={'tournamentGroup':{'id':1},'year':2026,'level':'WTA 250'}
    monkeypatch.setattr(S,'_json',lambda *a,**k:{'content':[e]*100})
    with pytest.raises(ValueError,match='repeated'):S._calendar(date(2026,6,1),date(2026,11,2))


def test_pipeline_keeps_raw_counts_out_of_publication_and_saves_updating_cache():
    root=Path(__file__).resolve().parents[1]
    assert 'data/live_stats/' in (root/'.gitignore').read_text()
    for name in ['daily.yml','discord-tennis.yml']:
        text=(root/'.github/workflows'/name).read_text()
        assert 'data/live_stats' in text and 'key: oracle-performance-v1-${{ github.run_id }}' in text
    text=(root/'.github/workflows/daily.yml').read_text()
    commit=text.split('git add',1)[1].splitlines()[0]
    assert 'live_stats' not in commit


def test_wrong_event_or_multiple_source_records_cannot_attach(monkeypatch,tmp_path):
    row=source();row.update(event='Wrong Event',city='Elsewhere')
    put(monkeypatch,tmp_path,{'one':row})
    _,info=S.enrich(pd.DataFrame([result()]),'wta');assert info['applied']==0
    row=source();other=copy.deepcopy(row);other['key']='two'
    put(monkeypatch,tmp_path,{'one':row,'two':other})
    _,info=S.enrich(pd.DataFrame([result()]),'wta');assert info['applied']==0 and info['ambiguous']==1
