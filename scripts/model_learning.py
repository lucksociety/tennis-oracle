#!/usr/bin/env python3
"""Permanent, market-free prospective evaluation. Calibration proposals never alter live scores."""
import argparse, csv, hashlib, io, json, math, os, statistics, urllib.request, urllib.error, zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def timestamp(value):
    if not value: return None
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except (ValueError, TypeError): return None


def record(event, start, frozen, model, probabilities, labels, source, cutoff=None, expected_scores=None):
    values = [float(p) for p in probabilities]
    if len(values) != len(labels) or any(not math.isfinite(p) or not 0 <= p <= 1 for p in values):
        raise ValueError('Invalid probability vector')
    if abs(sum(values)-1) > .002: raise ValueError('Probabilities do not sum to one')
    # Rounding in published outputs can leave a tiny mass discrepancy.
    mass = sum(values)
    values = [p/mass for p in values] if abs(mass-1)>1e-10 else values
    begin, freeze = timestamp(start), timestamp(frozen)
    if not begin or not freeze or freeze >= begin: return None
    if cutoff:
        cut = timestamp(cutoff) or timestamp(str(cutoff)[:10]+'T00:00:00+00:00')
        if cut and cut >= begin: return None
    if expected_scores is not None:
        expected_scores=[float(x) for x in expected_scores]
        if len(expected_scores)!=2 or any(not math.isfinite(x) or x<0 for x in expected_scores):raise ValueError('Invalid score prediction')
    row = dict(schema_version=1, expected_scores=expected_scores, event_id=str(event), start=begin.astimezone(timezone.utc).isoformat(), frozen_at=freeze.astimezone(timezone.utc).isoformat(),
               model=str(model), probabilities=values, labels=labels, source=source, cutoff=cutoff)
    row['key'] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return row


def documents(root, pattern):
    for path in sorted(root.glob(pattern)):
        try: yield str(path.relative_to(root)), json.loads(path.read_text())
        except (ValueError, OSError): continue


def collect_local(root, project):
    rows, outcomes = [], {}
    def add(*args, **kwargs):
        row = record(*args, **kwargs)
        if row: rows.append(row)
    if project == 'oracle-fc':
        for path, f in documents(root, 'data/forecasts/**/*.json'):
            if f.get('status') != 'ok': continue
            x, p = f['fixture'], f['simulation']
            add(x['event_id'], x['kickoff_utc'], f.get('frozen_at'), f['model_version'],
                [p['p_home'], p['p_draw'], p['p_away']], ['H','D','A'], path, f.get('data_cutoff'), [p['xg_home'],p['xg_away']] if p.get('xg_home') is not None else None)
        for path, f in documents(root, 'data/results/20*.json'):
            for g in f.get('games', []):
                if g.get('status') == 'final' and g.get('metrics', {}).get('outcome') in ('H','D','A'):
                    outcomes[str(g['event_id'])] = dict(label=g['metrics']['outcome'], source=path, scores=[int(x) for x in g['result']['score_90'].split('-')])
    elif project == 'nba-oracle':
        for path, f in documents(root, 'out/predictions.json'):
            for g in f.get('games', []):
                add(g['id'], g['tip'], f.get('generatedAt'), f['modelVersion'], [g['homeWin'],g['awayWin']], ['H','A'], path, f.get('cutoff'), [g['expHome'],g['expAway']] if g.get('expHome') is not None else None)
        for path, f in documents(root, 'data/games-*.json'):
            for g in f.get('games', []):
                if g[4] is not None and g[5] is not None and g[4] != g[5]:
                    outcomes[str(g[0])] = dict(label='H' if g[4]>g[5] else 'A', source=path, scores=[g[4],g[5]])
    elif project == 'nhl-score-predictor':
        for path, f in documents(root, 'data/predictions/*.json'):
            for g in f.get('games', []):
                if g.get('pHome') is not None:
                    add(g['id'], g['start'], f.get('generatedAt'), f['model'], [g['pHome'],g['pAway']], ['H','A'], path, expected_scores=list(reversed(g['xg'])) if g.get('xg') else None)
        for path, f in documents(root, 'data/results_*.json'):
            if not isinstance(f, list): continue
            for g in f:
                if g.get('a') is not None and g.get('h') is not None and g['a'] != g['h']:
                    outcomes[str(g['id'])] = dict(label='H' if g['h']>g['a'] else 'A', source=path, scores=[g['h'],g['a']])
    elif project == 'lucksociety-nfl-dashboard':
        for path, f in documents(root, 'out/model*.json'):
            for x in f.get('predictions', []):
                g,p = x['game'],x['res']
                start = datetime.fromisoformat(g['gameday']+'T'+g['gametime']).replace(tzinfo=ZoneInfo('America/New_York')).isoformat()
                add(g['game_id'], start, f.get('frozen_at'), 'nfl-v6', [p['p_home'],p['p_tie'],p['p_away']], ['H','D','A'], path, f.get('dataThrough', {}).get('last_game_date'), [p['sim_mean_home'],p['sim_mean_away']] if p.get('sim_mean_home') is not None else None)
        for path,f in documents(root, 'data/weeks/*.json'):
            for g in f.get('games', []):
                final = g.get('final')
                if final and final.get('home') is not None and final.get('away') is not None:
                    h,a=final['home'],final['away']
                    outcomes[str(g['game_id'])]=dict(label='H' if h>a else 'A' if a>h else 'D',source=path,scores=[h,a])
    elif project == 'tennis-oracle':
        for path,f in documents(root, 'forecasts/*/*/v*.json'):
            z=f.get('freeze',{}); p=f.get('win_prob_exact',f.get('win_prob',{}))
            if not z.get('pre_match') or not z.get('espn_id'): continue
            # Preserve player identity in class labels; result ordering can change independently.
            labels=[f['players'][k]['name'] for k in ('A','B')]
            add(z['espn_id'],z.get('scheduled_start_utc'),z.get('frozen_at_utc'),f['model']['version'],[p['A'],p['B']],labels,path)
        for path,f in documents(root, 'data/espn/results.json'):
            for x in f.values():
                w=x.get('winner')
                if x.get('status')=='STATUS_FINAL' and w in (0,1) and x.get('sets'):
                    outcomes[str(x['espn_id'])]=dict(label=x['players'][w]['name'],source=path)
    elif project == 'lol-scanner':
        for path,f in documents(root,'forecast_log/*.json'):
            for c in f.get('cards',[]):
                p=c.get('sim',{}).get('p_series_a')
                if p is None:continue
                add(c['match_id'],c.get('start_utc'),f.get('frozen_at_utc'),f['model_version'],[p,1-p],['A','B'],path)
        p=root/'calibration/calibration_log.csv'
        if p.exists():
            with p.open() as handle:
                for x in csv.DictReader(handle): outcomes[str(x['match_id'])]=dict(label='A' if int(x['a_won']) else 'B',source=str(p.relative_to(root)))
    else: raise ValueError('Unknown model project')
    return rows,outcomes


def merge_records(existing, incoming):
    result={}
    for row in [*existing,*incoming]:
        checked=record(row['event_id'],row['start'],row['frozen_at'],row['model'],row['probabilities'],row['labels'],row['source'],row.get('cutoff'),row.get('expected_scores'))
        if not checked or checked['key'] != row.get('key'): raise ValueError('Invalid or modified forecast record')
        result[checked['key']]=checked
    return sorted(result.values(),key=lambda r:(r['start'],r['event_id'],r['frozen_at'],r['key']))


def graded(rows,outcomes):
    # One event per model version; choose the final genuinely pre-game freeze, never a post-game reconstruction.
    chosen={}
    for r in rows:
        key=(r['event_id'],r['model'])
        if key not in chosen or r['frozen_at']>chosen[key]['frozen_at']:chosen[key]=r
    out=[]
    for r in chosen.values():
        outcome=outcomes.get(r['event_id'])
        if outcome and outcome['label'] in r['labels']:
            out.append({**r,'observed':r['labels'].index(outcome['label']),'actual_scores':outcome.get('scores')})
    return sorted(out,key=lambda r:(r['start'],r['event_id']))


def temperature(p,t):
    logs=[math.log(max(1e-12,x))/t for x in p]; hi=max(logs)
    ex=[math.exp(x-hi) for x in logs];s=sum(ex)
    return [x/s for x in ex]


def losses(rows,t=1):
    ll=[];bs=[]
    for r in rows:
        p=temperature(r['probabilities'],t);y=r['observed']
        ll.append(-math.log(max(1e-12,p[y])))
        bs.append(sum((v-int(i==y))**2 for i,v in enumerate(p)))
    return ll,bs


def metrics(rows):
    if not rows:return {'n':0}
    ll,bs=losses(rows)
    scored=[r for r in rows if r.get('expected_scores') is not None and r.get('actual_scores') is not None]
    score_metrics={}
    if scored:
        score_metrics={'score_n':len(scored),'score_mae_per_team':statistics.mean(abs(p-y) for r in scored for p,y in zip(r['expected_scores'],r['actual_scores'])),
                       'total_mae':statistics.mean(abs(sum(r['expected_scores'])-sum(r['actual_scores'])) for r in scored),
                       'margin_mae':statistics.mean(abs((r['expected_scores'][0]-r['expected_scores'][1])-(r['actual_scores'][0]-r['actual_scores'][1])) for r in scored)}
    return {**score_metrics,'n':len(rows),'log_loss':statistics.mean(ll),'brier_multiclass_sum':statistics.mean(bs),
            'winner_accuracy':statistics.mean(int(max(range(len(r['probabilities'])),key=r['probabilities'].__getitem__)==r['observed']) for r in rows)}


def calibration_proposal(rows):
    # Split by calendar day to avoid same-day results contaminating evaluation. Test is never used to select T.
    dates=sorted({r['start'][:10] for r in rows})
    if len(rows)<500 or len(dates)<30:return {'status':'insufficient_forward_sample','minimum_games':500,'minimum_days':30}
    a,b=dates[int(len(dates)*.6)],dates[int(len(dates)*.8)]
    train=[r for r in rows if r['start'][:10]<a];val=[r for r in rows if a<=r['start'][:10]<b];test=[r for r in rows if r['start'][:10]>=b]
    if min(len(val),len(test))<100:return {'status':'insufficient_holdout_sample','validation_n':len(val),'test_n':len(test)}
    t=min((.75,1.,1.25,1.5,2.),key=lambda t:statistics.mean(losses(train,t)[0]))
    def gate(part):
        base,bb=losses(part);cand,cb=losses(part,t);delta=[c-b for c,b in zip(cand,base)]
        # Cluster by date: fixtures on one day are correlated. Normal approximation, explicitly a screening gate.
        days={}
        for r,d in zip(part,delta):days.setdefault(r['start'][:10],[]).append(d)
        daily=[statistics.mean(x) for x in days.values()]
        upper=statistics.mean(daily)+1.96*statistics.stdev(daily)/math.sqrt(len(daily)) if len(daily)>1 else float('inf')
        change=statistics.mean(delta);bchange=statistics.mean(cb)-statistics.mean(bb)
        return dict(n=len(part),days=len(daily),log_loss_change=change,brier_change=bchange,daily_95pct_upper=upper,
                    passes=change<-.001 and bchange<=0 and upper<0)
    v,h=gate(val),gate(test)
    return {'status':'review_candidate' if t!=1 and v['passes'] and h['passes'] else 'retain_current_model',
            'temperature':t,'train_n':len(train),'validation':v,'heldout_test':h,'automatic_live_changes':False,
            'note':'Prospective screening only. Preserve score/probability consistency and validate a new version before promotion.'}


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def api_get(url,token,limit=20_000_000):
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'sports-model-learning'})
    try:r=urllib.request.build_opener(NoRedirect).open(req,timeout=45)
    except urllib.error.HTTPError as e:
        if e.code not in (301,302,303,307,308):raise
        # Signed artifact download receives no GitHub credential on the cross-host redirect.
        target=e.headers['Location']
        from urllib.parse import urlsplit
        u=urlsplit(target)
        if u.scheme!='https' or not u.hostname or not u.hostname.endswith(('.blob.core.windows.net','.githubusercontent.com','.actions.githubusercontent.com','.github.com')):raise ValueError('Unexpected artifact host')
        r=urllib.request.urlopen(target,timeout=45)
    with r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('Artifact exceeds size limit')
        return raw


def collect_artifacts(state):
    repo=os.environ.get('GITHUB_REPOSITORY');token=os.environ.get('GH_TOKEN')
    if not repo or not token:raise ValueError('Artifact collection needs repository and token')
    incoming=[];seen=set(state.get('artifact_ids',[]));new=set()
    for page in range(1,21):
        data=json.loads(api_get(f'https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}',token))
        artifacts=data.get('artifacts',[])
        for a in artifacts:
            legacy_nba=repo.endswith('/nba-oracle') and a['name'].startswith('nba-oracle-')
            if (not a['name'].startswith('learning-') and not legacy_nba) or a['expired'] or a['id'] in seen:continue
            raw=api_get(f'https://api.github.com/repos/{repo}/actions/artifacts/{int(a["id"])}/zip',token)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for f in z.infolist():
                    filename=f.filename.split('/')[-1]
                    if filename not in (('inbox.jsonl','predictions.json') if legacy_nba else ('inbox.jsonl',)):continue
                    if f.file_size>10_000_000:raise ValueError('Expanded ledger too large')
                    if filename=='inbox.jsonl':incoming += [json.loads(x) for x in z.read(f).decode().splitlines() if x.strip()]
                    else:
                        doc=json.loads(z.read(f))
                        if doc.get('stage')!='prediction':continue
                        for g in doc.get('games',[]):
                            row=record(g['id'],g['tip'],doc.get('generatedAt'),doc['modelVersion'],[g['homeWin'],g['awayWin']],['H','A'],f'artifact:{a["id"]}/predictions.json',doc.get('cutoff'),[g['expHome'],g['expAway']] if g.get('expHome') is not None else None)
                            if row:incoming.append(row)
            new.add(a['id'])
        if len(artifacts)<100:break
    return incoming,{'artifact_ids':sorted(seen|new)}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--capture-only',action='store_true');ap.add_argument('--collect',action='store_true');args=ap.parse_args()
    project=json.loads((ROOT/'learning/config.json').read_text())['project']
    rows,outcomes=collect_local(ROOT,project); folder=ROOT/'learning';folder.mkdir(exist_ok=True)
    if args.capture_only:
        recent=datetime.now(timezone.utc)-timedelta(days=2)
        rows=[r for r in rows if timestamp(r['frozen_at'])>=recent]
        (folder/'inbox.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
        print(f'Archived {len(rows)} provably pre-game forecasts');return
    path=folder/'forecasts.jsonl';statepath=folder/'state.json'
    state=json.loads(statepath.read_text()) if statepath.exists() else {}
    if args.collect:
        incoming,state=collect_artifacts(state);rows+=incoming
    captures=list((folder/'captures').glob('*.jsonl'))
    for capture in captures:rows+=read_jsonl(capture)
    merged=merge_records(read_jsonl(path),rows)
    path.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in merged))
    prior=folder/'results.json'; results=json.loads(prior.read_text()) if prior.exists() else {};results.update(outcomes)
    prior.write_text(json.dumps(results,sort_keys=True,indent=1)+'\n')
    settled=graded(merged,results);models=sorted({r['model'] for r in merged});now=datetime.now(timezone.utc)
    report={'generated_at':now.isoformat(),'project':project,'forecasts':len(merged),'settled_by_version':{},'calibration_proposals':{},
            'forecast_policy':'Last pre-game freeze per event per model version. No retrospectively generated forecast is graded.',
            'warnings':[]}
    if args.collect:
        cfg=json.loads((folder/'config.json').read_text())
        workflow=cfg.get('refresh_workflow')
        if workflow:
            repo=os.environ['GITHUB_REPOSITORY'];token=os.environ['GH_TOKEN']
            health=json.loads(api_get(f'https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?status=success&per_page=1',token)).get('workflow_runs',[])
            last=timestamp(health[0]['updated_at']) if health else None
            report['last_successful_refresh']=last.isoformat() if last else None
            if not last or (now-last).total_seconds()>48*3600:report['warnings'].append('No successful data refresh in the last 48 hours; inspect the refresh workflow before trusting new predictions.')
    for model in models:
        sample=[r for r in settled if r['model']==model]
        report['settled_by_version'][model]=metrics(sample);report['calibration_proposals'][model]=calibration_proposal(sample)
    pending=[r for r in graded_pending(merged,results) if (now-timestamp(r['start'])).total_seconds()>7*86400]
    if pending:report['warnings'].append(f'{len(pending)} events more than seven days old still lack final results; investigate result-feed coverage.')
    if not merged:report['warnings'].append('No eligible pre-game forecasts captured yet; outcome learning has not started.')
    for warning in report['warnings']:print('::warning::'+warning)
    (folder/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n')
    statepath.write_text(json.dumps(state,sort_keys=True)+'\n')
    for capture in captures:capture.unlink()
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:f.write('## Daily model evaluation\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n')
    print(json.dumps(report,indent=2))


def graded_pending(rows,outcomes):
    return {r['event_id']:r for r in rows if r['event_id'] not in outcomes}.values()


if __name__=='__main__':main()
