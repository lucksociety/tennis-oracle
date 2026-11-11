import pandas as pd
from tennis_oracle import build
from tennis_oracle.config import load_params


def test_historical_stats_cutoff_cannot_include_future_counts(monkeypatch):
    fields=['w_svpt','w_1stWon','w_2ndWon','l_svpt','l_1stWon','l_2ndWon']
    frame=pd.DataFrame({'date':pd.to_datetime(['2026-10-29','2026-11-01']),
                        'winner_id':['a','c'],'loser_id':['b','d'],'stats_source':['wta-official']*2,
                        **{f:[30.,40.]for f in fields}})
    monkeypatch.setattr(build,'combined_frame',lambda *a:(frame,{'point_stats_cutoff':'2026-11-01','results_cutoff':'2026-11-01'}))
    observed=[];monkeypatch.setattr(build,'run_history',lambda eng,df,cb:observed.extend(df.date.tolist()))
    eng=build.build_engine('atp',load_params(),asof='2026-10-30')
    assert observed==[pd.Timestamp('2026-10-29')]
    assert eng.point_cutoff==pd.Timestamp('2026-10-29')
