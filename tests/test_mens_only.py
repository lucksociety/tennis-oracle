import pytest
from tennis_oracle.build import build_engine
from tennis_oracle.predict import forecast
from tennis_oracle import schedule,jupiter
from datetime import date

def test_wta_cannot_build_or_predict():
    with pytest.raises(ValueError,match='Men-only'):build_engine('wta')
    with pytest.raises(ValueError,match='Men-only'):forecast(None,'a','b','Hard',tour='wta')
def test_cached_wta_slate_cannot_generate_a_forecast(monkeypatch):
    monkeypatch.setattr(schedule,'fetch_day_matches',lambda _: [{'tour':'wta'}])
    from types import SimpleNamespace
    monkeypatch.setattr(schedule,'surface_lookup',lambda _: {})
    monkeypatch.setattr(schedule,'load_sackmann',lambda _: None)
    engine=SimpleNamespace(players={},p={'version':'test'})
    slate=schedule.build_slate(date(2026,11,11),{'atp':engine},write=False)
    assert slate['matches']==[]
def test_wta_contract_cannot_be_recommended():
    r=jupiter.evaluate({'tour':'wta','espn_id':'x'},None)
    assert r['status']=='excluded' and r['best'] is None
    assert all(t=='atp' for t,_ in jupiter.LISTS)
