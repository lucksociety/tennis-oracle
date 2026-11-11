"""Current performance counts: TennisMyLife ATP CSVs and official WTA match stats.

Only enrich an already-recorded completed result when names, winner-oriented set
scores, format and dates match uniquely. Never add a result or change its winner.
Raw provider data stays in the runner cache, not the published site.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

import pandas as pd

from .data import ROOT, norm_name, parse_score, assert_no_market_columns

CACHE = ROOT / 'data' / 'live_stats'
WTA_BASE = 'https://api.wtatennis.com/tennis'
TML_BASE = 'https://stats.tennismylife.org/data'
COUNTS = ('ace', 'df', 'svpt', '1stIn', '1stWon', '2ndWon', 'SvGms', 'bpSaved', 'bpFaced')
CORE = ('svpt', '1stIn', '1stWon', '2ndWon')
SCHEMA = 1


def _get(url, *, optional=False):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'tennis-oracle/1.2 (completed performance statistics)'})
            with urllib.request.urlopen(req, timeout=25) as response:
                return response.read(20_000_001)
        except urllib.error.HTTPError as exc:
            if optional and exc.code == 404:
                return None
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(10 if exc.code == 429 else 1 + attempt)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def _json(url, *, optional=False):
    payload = _get(url, optional=optional)
    if payload is None:
        return None
    if len(payload) > 20_000_000:
        raise ValueError('Statistics response exceeds size limit')
    return json.loads(payload)


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False))
    os.replace(temporary, path)


def load_cache(tour):
    path = CACHE / f'{tour}.json'
    if not path.exists():
        return {'schema': SCHEMA, 'rows': {}}
    obj = json.loads(path.read_text())
    if obj.get('schema') != SCHEMA or not isinstance(obj.get('rows'), dict):
        raise ValueError(f'Invalid {tour} performance cache')
    return obj


def valid_counts(stats):
    """Counts, not rounded percentages; reject impossible or incomplete records."""
    for pre in ('w_', 'l_'):
        vals = [stats.get(pre + k) for k in COUNTS]
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 or v != int(v) for v in vals):
            return False
        n, first, won1, won2 = [stats[pre + k] for k in CORE]
        if n < 20 or not (won1 <= first <= n and won2 <= n - first):
            return False
        if stats[pre + 'df'] > n - first or stats[pre + 'ace'] > won1 + won2:
            return False
        if stats[pre + 'bpSaved'] > stats[pre + 'bpFaced']:
            return False
    return True


def score_key(score):
    sets, _, complete = parse_score(score)
    if not complete or len(sets) < 2 or any(not ((max(a, b) >= 6 and abs(a - b) >= 2) or {a, b} == {6, 7}) for a, b in sets):
        return None
    return tuple(sets)


def refresh_atp(since, until):
    cache = load_cache('atp')
    rows = dict(cache['rows'])
    fetched = 0
    for year in range(since.year, until.year + 1):
        # Ongoing events are separate from the completed-season file.
        names = [f'{year}.csv', f'atp_quali/{year}_atp_quali.csv'] + (['ongoing_tourneys.csv'] if year == until.year else [])
        for name in names:
            payload = _get(f'{TML_BASE}/{name}', optional=name.startswith('atp_quali/'))
            if payload is None:
                continue
            if len(payload) > 20_000_000:
                raise ValueError('ATP CSV exceeds size limit')
            frame = pd.read_csv(io.BytesIO(payload), low_memory=False)
            assert_no_market_columns(frame)
            required = {'tourney_date', 'winner_name', 'loser_name', 'score', 'best_of', 'surface'} | {p + k for p in ('w_', 'l_') for k in COUNTS}
            if not required.issubset(frame) or (frame.empty and name != 'ongoing_tourneys.csv'):
                raise ValueError('ATP statistics schema is incomplete; retaining last good cache')
            for rec in frame.to_dict('records'):
                stamp = pd.to_datetime(str(rec['tourney_date']), format='%Y%m%d', errors='coerce')
                if pd.isna(stamp) or not since - timedelta(days=14) <= stamp.date() <= until:
                    continue
                sets = score_key(rec['score'])
                if not sets or sum(a > b for a, b in sets) != (int(rec['best_of']) + 1) // 2 or rec['surface'] not in ('Hard', 'Clay', 'Grass'):
                    continue
                try:
                    stats = {p + k: float(rec[p + k]) for p in ('w_', 'l_') for k in COUNTS}
                except (TypeError, ValueError):
                    continue
                if not valid_counts(stats):
                    continue
                key = f"tml:{rec.get('tourney_id')}:{rec.get('match_num')}:{norm_name(rec['winner_name'])}:{norm_name(rec['loser_name'])}"
                rows[key] = dict(source='tennismylife', key=key, date=str(stamp.date()), precision='tournament',
                                 winner_name=rec['winner_name'], loser_name=rec['loser_name'], score=rec['score'],
                                 best_of=int(rec['best_of']), surface=rec['surface'], stats=stats, event=rec.get('tourney_name'), city=rec.get('tourney_name'))
                fetched += 1
    if not fetched:
        raise ValueError('ATP feed returned no valid completed performance records')
    cache.update(rows=rows, refreshed_at=datetime.now(timezone.utc).isoformat())
    _save(CACHE / 'atp.json', cache)
    return {'records_seen': fetched, 'cached': len(rows)}


def wta_row(event, match, stats, until):
    if match.get('DrawMatchType') != 'S' or match.get('MatchState') != 'F' or str(match.get('Winner')) not in ('2', '3'):
        return None  # retirement/walkover codes are not normal completed matches
    if str(match.get('EventID')) != str(event['tournamentGroup']['id']) or int(match.get('EventYear', 0)) != int(event['year']):
        return None
    stamp = pd.to_datetime(match.get('MatchTimeStamp'), utc=True, errors='coerce')
    if pd.isna(stamp) or stamp.date() > until:
        return None
    whole = [s for s in (stats or []) if s.get('setnum') == 0]
    if len(whole) != 1:
        return None
    s = whole[0]
    if str(s.get('eventid')) != str(match['EventID']) or int(s.get('eventyear', 0)) != int(match['EventYear']) or s.get('matchid') != match.get('MatchID'):
        return None
    winner, loser = ('A', 'B') if str(match['Winner']) == '2' else ('B', 'A')
    counts = {}
    for pre, who, other in [('w_', winner.lower(), loser.lower()), ('l_', loser.lower(), winner.lower())]:
        def value(key):
            v = s.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError('Missing WTA performance count')
            return v
        try:
            counts.update({pre + 'ace': value('aces' + who), pre + 'df': value('dblflt' + who),
                           pre + 'svpt': value('totservplayed' + who), pre + '1stIn': value('ptsplayed1stserv' + who),
                           pre + '1stWon': value('ptswon1stserv' + who),
                           pre + '2ndWon': value('ptstotwonserv' + who) - value('ptswon1stserv' + who),
                           pre + 'SvGms': value('servgamesplayed' + who),
                           pre + 'bpFaced': value('breakptsplayed' + other),
                           pre + 'bpSaved': value('breakptsplayed' + other) - value('breakptsconv' + other)})
        except ValueError:
            return None
    if not valid_counts(counts):
        return None
    score = []
    for i in range(1, 4):
        a, b = match.get(f'ScoreSet{i}{winner}'), match.get(f'ScoreSet{i}{loser}')
        if a in ('', None) or b in ('', None):
            break
        score.append(f'{a}-{b}')
    score = ' '.join(score)
    if not score_key(score) or sum(a > b for a, b in score_key(score)) != 2:
        return None
    if event.get('surface') not in ('Hard', 'Clay', 'Grass'):
        return None
    key = f"wta:{match['EventYear']}:{match['EventID']}:{match['MatchID']}"
    return dict(source='wta-official', key=key, date=str(stamp.date()), precision='day',
                winner_name=f"{match.get('PlayerNameFirst' + winner, '')} {match.get('PlayerNameLast' + winner, '')}".strip(),
                loser_name=f"{match.get('PlayerNameFirst' + loser, '')} {match.get('PlayerNameLast' + loser, '')}".strip(),
                winner_source_id=str(match.get('PlayerID' + winner)), loser_source_id=str(match.get('PlayerID' + loser)),
                score=score, best_of=3, surface=event['surface'], stats=counts, provider_updated=match.get('LastUpdated'), event=event.get('title') or event['tournamentGroup'].get('name'), city=event.get('city'))


def _calendar(since, until):
    events, seen = [], set()
    for page in range(50):
        obj = _json(f'{WTA_BASE}/tournaments/?from={since.isoformat()}&to={until.isoformat()}&pageSize=100&page={page}')
        content = obj.get('content')
        if not isinstance(content, list):
            raise ValueError('WTA calendar schema changed')
        added = 0
        for event in content:
            key = (str(event.get('tournamentGroup', {}).get('id')), event.get('year'))
            if key not in seen:
                seen.add(key); events.append(event); added += 1
        if not content or len(content) < 100:
            break
        if not added:
            raise ValueError('WTA pagination repeated a full page')
    else:
        raise ValueError('WTA calendar pagination limit reached')
    if not events:
        raise ValueError('WTA calendar empty; retaining last good statistics')
    return [e for e in events if str(e.get('level', '')).startswith('WTA') or e.get('level') in ('Grand Slam', 'WTA Finals')]


def refresh_wta(since, until):
    cache = load_cache('wta')
    rows = dict(cache['rows'])
    # Daily runs revisit the last week, retaining all previously validated counts.
    start = max(since - timedelta(days=14), date.fromisoformat(cache['through']) - timedelta(days=7)) if cache.get('through') else since - timedelta(days=14)
    events = _calendar(start, until)
    catalog = dict(cache.get('events', {}))
    for e in events:
        catalog[f"{e['year']}-{e['tournamentGroup']['id']}"] = {
            'name': e['tournamentGroup'].get('name'), 'title': e.get('title'), 'city': e.get('city'),
            'surface': e.get('surface'), 'start': e.get('startDate'), 'end': e.get('endDate')}
    cache['events'] = catalog
    failures, missing, new = [], 0, 0
    def event_rows(event):
        eid, year = event['tournamentGroup']['id'], event['year']
        obj = _json(f'{WTA_BASE}/tournaments/{eid}/{year}/matches', optional=True)
        if obj is None:
            return [], 1
        matches = obj.get('matches')
        if not isinstance(matches, list):
            raise ValueError(f'WTA match schema changed: {eid}/{year}')
        result, unavailable = [], 0
        for match in matches:
            if match.get('DrawMatchType') != 'S' or match.get('MatchState') != 'F' or str(match.get('Winner')) not in ('2', '3'):
                continue
            key = f"wta:{year}:{eid}:{match.get('MatchID')}"
            # Re-fetch a completed record when the provider publishes a correction.
            if key in rows and rows[key].get('provider_updated') == match.get('LastUpdated'):
                continue
            st = _json(f"{WTA_BASE}/tournaments/{eid}/{year}/matches/{match['MatchID']}/stats", optional=True)
            row = wta_row(event, match, st, until)
            if row:
                result.append(row)
            else:
                unavailable += 1
            time.sleep(.05)
        return result, unavailable
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(event_rows, e): e for e in events}
        for future in as_completed(futures):
            event = futures[future]
            try:
                fresh, skipped = future.result()
                rows.update({r['key']: r for r in fresh}); new += len(fresh); missing += skipped
                cache['rows'] = rows
                _save(CACHE / 'wta.json', cache)  # checkpoint long initial backfills
                print(f"[stats] WTA {event['tournamentGroup']['id']}: {len(fresh)} new, {skipped} unavailable", flush=True)
            except Exception as exc:
                failures.append(f"{event['tournamentGroup']['id']}: {exc}")
    if not rows:
        raise ValueError('WTA returned no valid completed performance records')
    cache.update(rows=rows, refreshed_at=datetime.now(timezone.utc).isoformat(), errors=failures, unavailable=missing)
    if not failures:
        cache['through'] = until.isoformat()
    _save(CACHE / 'wta.json', cache)
    if failures:
        raise RuntimeError('WTA partial refresh; retained validated records: ' + '; '.join(failures[:3]))
    return {'new': new, 'cached': len(rows), 'unavailable': missing}


def refresh(since, until=None):
    until = until or date.today()
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'sources': {}}
    for tour, fn in [('atp', refresh_atp)]:
        try:
            report['sources'][tour] = {'ok': True, **fn(since, until)}
        except Exception as exc:
            report['sources'][tour] = {'ok': False, 'error': str(exc)}
            print(f'[stats] {tour}: {exc}; last good counts retained', flush=True)
    _save(CACHE / 'refresh.json', report)
    return report


def enrich(frame, tour):
    """Attach uniquely matched counts; leave the result population unchanged."""
    frame = frame.copy()
    for k in [p + c for p in ('w_', 'l_') for c in COUNTS]:
        if k not in frame:
            frame[k] = float('nan')
    if 'stats_source' not in frame:
        frame['stats_source'] = pd.Series(index=frame.index, dtype=object)
    index, identities = {}, {}
    for i, r in frame.iterrows():
        for side in ("winner", "loser"):
            identities.setdefault(norm_name(r[side + "_name"]), set()).add(str(r[side + "_id"]))
        key = (norm_name(r['winner_name']), norm_name(r['loser_name']), score_key(r.get('score')), int(r['best_of']))
        index.setdefault(key, []).append(i)
    applied, ambiguous = [], 0
    proposals = {}
    cache = load_cache(tour)
    for src in cache['rows'].values():
        if not valid_counts(src.get('stats', {})):
            continue
        key = (norm_name(src['winner_name']), norm_name(src['loser_name']), score_key(src['score']), int(src['best_of']))
        # Provider IDs are in different namespaces; never compare them as if equal.
        # Exact names must each resolve to one existing model identity.
        if any(len(identities.get(norm_name(src[side + '_name']), set())) != 1 for side in ('winner', 'loser')):
            ambiguous += 1
            continue
        candidates = []
        for i in index.get(key, []):
            r = frame.loc[i]
            metadata = src
            if src['source'] == 'wta-official' and not src.get('event'):
                parts = src['key'].split(':')
                metadata = cache.get('events', {}).get('-'.join(parts[1:3]), {})
            names = {norm_name(metadata.get(k) or '') for k in ('event', 'name', 'title')} - {''}
            cities = {norm_name(metadata.get('city') or '')} - {''}
            event_ok = norm_name(r.get('tourney_name', '')) in names
            city_ok = norm_name(str(r.get('venue', '')).split(',')[0]) in cities
            if not event_ok and not city_ok:
                continue
            gap = (pd.Timestamp(r['date']).normalize() - pd.Timestamp(src['date'])).days
            if (abs(gap) > 1 if src['precision'] == 'day' else not 0 <= gap <= 14):
                continue
            candidates.append(i)
        if len(candidates) != 1:
            ambiguous += int(len(candidates) > 1)
            continue
        i = candidates[0]
        proposals.setdefault(i, []).append(src)
    for i, choices in proposals.items():
        if len(choices) != 1:
            ambiguous += 1
            continue
        src = choices[0]
        # The historical archive already has trusted counts; this is an additive repair.
        if valid_counts({p + k: frame.at[i, p + k] for p in ('w_', 'l_') for k in COUNTS}):
            continue
        for k, value in src['stats'].items():
            frame.at[i, k] = value
        frame.at[i, 'surface'] = src['surface']
        frame.at[i, 'stats_source'] = src['source']
        applied.append((src['key'], str(frame.at[i, 'date']), src['stats']))
    revision = hashlib.sha256(json.dumps(sorted(applied), sort_keys=True).encode()).hexdigest()[:16]
    latest = max((r[1] for r in applied), default=None)
    return frame, {'applied': len(applied), 'ambiguous': ambiguous, 'latest_stats_match': latest, 'revision': revision}


def fixture_surface(event, venue, day, tour):
    """Current official edition metadata wins only on an unambiguous exact match."""
    if tour != 'wta':
        return None
    target, city = norm_name(event), norm_name((venue or '').split(',')[0])
    hits = []
    for e in load_cache(tour).get('events', {}).values():
        if not e.get('start') or not e.get('end') or not e['start'] <= str(day) <= e['end']:
            continue
        if target in {norm_name(e.get('name')), norm_name(e.get('title'))} or (city and city == norm_name(e.get('city'))):
            if e.get('surface') in ('Hard', 'Clay', 'Grass'):
                hits.append(e['surface'])
    return (hits[0], 'official-wta-current-edition') if hits and len(set(hits)) == 1 else None
