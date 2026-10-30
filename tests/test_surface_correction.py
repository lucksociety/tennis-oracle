import copy,json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from tennis_oracle import data,schedule

def test_official_queretaro_surface_overrides_historical_city():
    for name in ['Queretaro Open','Querétaro Open']:
        assert data.infer_surface(name,'Santiago de Querétaro, Mexico',{'santiago de queretaro':'Hard'})==('Clay','official-wta-1145')
    assert data.infer_surface('Other Open','Mexico City, Mexico',{'mexico city':'Hard'})==('Hard','venue-history')

def test_changed_surface_refreezes_only_before_play(monkeypatch):
    root=Path(__file__).resolve().parents[1]
    f=json.loads(next((root/'forecasts/2026-10-29').glob('*/v1.json')).read_text())
    f['surface']='Hard';f['results_cutoff']='2026-10-29'
    names=[f['players'][k]['name'] for k in ('A','B')];ids=[f['players'][k]['id'] for k in ('A','B')]
    eng=SimpleNamespace(p={'version':f['model']['version']},info={'results_cutoff':'2026-10-29'},point_cutoff='2026-06-01')
    row={'tour':'wta','event':'Queretaro Open','venue':'Santiago de Queretaro, Mexico','major':False,'round':'Quarterfinal','players':[{'name':x} for x in names],'espn_id':'fixture','court':'','start':'2099-01-01T20:00Z','status':'STATUS_SCHEDULED','completed':False,'winner':None,'sets':[]}
    monkeypatch.setattr(schedule,'load_sackmann',lambda t:None);monkeypatch.setattr(schedule,'surface_lookup',lambda x:{})
    monkeypatch.setattr(schedule,'name_map',lambda e:dict(zip(map(data.norm_name,names),ids)))
    monkeypatch.setattr(schedule,'fetch_day_matches',lambda d:[row]);monkeypatch.setattr(schedule,'latest',lambda *a:f)
    calls=[]
    def forecast(*a,**k):
        calls.append(a[3]);updated=copy.deepcopy(f);updated['surface']=a[3];return updated
    monkeypatch.setattr(schedule,'forecast',forecast)
    freezes=[];monkeypatch.setattr(schedule,'freeze',lambda *a,**k:freezes.append(k))
    schedule.build_slate(date(2099,1,1),{'wta':eng},write=False)
    assert calls==['Clay'] and 'surface corrected' in freezes[0]['reason']
    calls.clear();freezes.clear();row['start']='2000-01-01T20:00Z'
    schedule.build_slate(date(2000,1,1),{'wta':eng},write=False)
    assert calls==[] and freezes==[]
