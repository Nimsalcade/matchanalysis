"""Evidence-first tactical decoding of saved WhoScored match-centre HTML.

This module uses only event data. It does *not* claim to reconstruct the true
out-of-possession formation, press triggers, rest-defence or simultaneous player
positions; these require tracking/video. No modelled xG or xT is fabricated.

Usage: python tactic_decoder.py saved_match.html --team Como --output como.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
import json
import math
from pathlib import Path
import re
from statistics import mean, median
from typing import Any, Iterable

LENGTH = 105.0
WIDTH = 68.0
CONTROL_TYPES = {"Pass", "TakeOn", "BallTouch", "BallRecovery", "Interception",
                 "Goal", "SavedShot", "MissedShots", "ShotOnPost", "Dispossessed", "KeeperPickup"}
RECEIVER_TYPES = {"Pass", "TakeOn", "BallTouch", "Goal", "SavedShot", "MissedShots", "ShotOnPost", "Dispossessed"}
DEFENSIVE_TYPES = {"Tackle", "Interception", "BallRecovery", "BlockedPass", "Challenge", "Clearance", "Aerial"}
SHOT_TYPES = {"Goal", "SavedShot", "MissedShots", "ShotOnPost"}
STOP_TYPES = {"End", "Start", "SubstitutionOn", "SubstitutionOff"}
RESTART_TAGS = {"CornerTaken", "Corner", "ThrowIn", "GoalKick", "FreekickTaken", "IndirectFreekickTaken", "Penalty"}


def _round(value: float | None, n: int = 2) -> float | None:
    return round(value, n) if value is not None and math.isfinite(value) else None


def _median(items: Iterable[float]) -> float | None:
    values = list(items)
    return _round(median(values)) if values else None


def _pct(num: int | float, den: int | float) -> float | None:
    return _round(100.0 * num / den, 1) if den else None


def load_match(path_or_html: str | Path | bytes) -> dict:
    """Parse the genuine embedded match object, not rendered page text.

    JS top-level property names are unquoted; fields *inside* matchCentreData
    are JSON already. Only four known top-level keys are quoted.
    """
    if isinstance(path_or_html, bytes):
        html = path_or_html.decode('utf-8-sig', errors='replace')
    elif isinstance(path_or_html, Path):
        html = path_or_html.read_text(encoding='utf-8-sig', errors='replace')
    elif isinstance(path_or_html, str) and 'require.config.params["args"]' in path_or_html:
        html = path_or_html
    else:
        html = Path(path_or_html).read_text(encoding='utf-8-sig', errors='replace')
    match = re.search(r'require\.config\.params\[\s*["\']args["\']\s*\]\s*=\s*\{', html)
    if not match:
        raise ValueError('Could not find embedded WhoScored match-centre data in this HTML.')
    snippet = html[match.end()-1:]
    snippet = re.sub(r'(?<=[{,])\s*(matchId|matchCentreData|matchCentreEventTypeJson|formationIdNameMappings)\s*:',
                     lambda m: '"'+m.group(1)+'":', snippet)
    try:
        root, _end = json.JSONDecoder().raw_decode(snippet)
    except json.JSONDecodeError as exc:
        raise ValueError(f'WhoScored embedded match JSON could not be decoded: {exc}') from exc
    if 'matchCentreData' not in root or 'events' not in root['matchCentreData']:
        raise ValueError('MatchCentreData.events missing from this HTML snapshot.')
    return root


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get('displayName') or '')
    return str(value or '')


def _qualifiers(event: dict) -> frozenset[str]:
    return frozenset(_name(q.get('type')) for q in event.get('qualifiers', []) if isinstance(q, dict))


def normalize_events(raw: list[dict]) -> list[dict]:
    events = []
    for ix, e in enumerate(raw):
        def coord(k: str, scale: float) -> float | None:
            try:
                val = e.get(k)
                return float(val) * scale if val is not None else None
            except (ValueError, TypeError):
                return None
        minute = int(e.get('minute') or 0)
        second = int(e.get('second') or 0)
        events.append({
            'idx': ix, 'id': e.get('id'), 'event_id': e.get('eventId'),
            'minute': minute, 'second': second, 'time': 60 * minute + second,
            'expanded_minute': e.get('expandedMinute'),
            'period': _name(e.get('period')),
            'team_id': e.get('teamId'), 'player_id': e.get('playerId'),
            'type': _name(e.get('type')), 'outcome': _name(e.get('outcomeType')),
            'x': coord('x', LENGTH/100), 'y': coord('y', WIDTH/100),
            'end_x': coord('endX', LENGTH/100), 'end_y': coord('endY', WIDTH/100),
            'tags': _qualifiers(e), 'related_player_id': e.get('relatedPlayerId'),
        })
    # Events in this file are already in provider order. Time alone is not
    # a stable sort key for coincident events; preserve the recorded order.
    return events


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2-x1, y2-y1)


def _goal_progress(ev: dict) -> float | None:
    if None in (ev['x'], ev['y'], ev['end_x'], ev['end_y']):
        return None
    return _distance(ev['x'], ev['y'], LENGTH, WIDTH/2) - _distance(ev['end_x'], ev['end_y'], LENGTH, WIDTH/2)


def _restart(ev: dict) -> bool:
    return bool(ev['tags'] & RESTART_TAGS)


def _is_live_pass(ev: dict) -> bool:
    return ev['type'] == 'Pass' and not _restart(ev)


def link_pass_receivers(events: list[dict]) -> dict[int, dict]:
    """Link only likely controlled receptions, never a blind shift(-1).

    The inferred link is explicitly a *proxy*. Stop when the opponent has
    a recorded control or a restart/period boundary interrupts the sequence.
    """
    links: dict[int, dict] = {}
    for i, e in enumerate(events):
        if e['type'] != 'Pass' or e['outcome'] != 'Successful' or e['end_x'] is None:
            continue
        for cand in events[i+1:i+9]:
            if cand['period'] != e['period'] or cand['time'] < e['time'] or cand['time']-e['time'] > 12:
                break
            if cand['type'] in STOP_TYPES or cand['type'] == 'CornerAwarded':
                break
            if cand['type'] not in CONTROL_TYPES:
                continue
            if cand['team_id'] != e['team_id']:
                break
            if cand['type'] not in RECEIVER_TYPES or cand['player_id'] is None or cand['player_id'] == e['player_id']:
                # The next controlled action was not a teammate reception.
                break
            if cand['x'] is None or cand['y'] is None:
                break
            distance = _distance(e['end_x'], e['end_y'], cand['x'], cand['y'])
            if distance > 15:
                break
            links[e['idx']] = {'receiver_id': cand['player_id'], 'distance_m': _round(distance, 1),
                                'delay_seconds': cand['time']-e['time'],
                                'confidence': ('high' if distance <= 7 and cand['time']-e['time'] <= 6 else 'medium')}
            break
    return links


def build_possessions(events: list[dict]) -> list[dict]:
    """Approximate ball-control runs, not provider-confirmed possessions.

    Ignore opponent tackles/fouls until a recorded opponent control begins.
    Short stoppages and coincident events are not perfectly reconstructable.
    """
    runs: list[dict] = []
    active: dict | None = None
    for e in events:
        if e['type'] in STOP_TYPES:
            if e['type'] == 'End' and active:
                runs.append(active)
                active = None
            continue
        if e['type'] not in CONTROL_TYPES or e['player_id'] is None:
            continue
        split = (active is None or active['team_id'] != e['team_id'] or active['period'] != e['period']
                 or e['time'] - active['events'][-1]['time'] > 22 or
                 active['events'][-1]['type'] in SHOT_TYPES)
        if split:
            if active:
                runs.append(active)
            active = {'team_id': e['team_id'], 'period': e['period'], 'events': []}
        active['events'].append(e)
    if active:
        runs.append(active)
    return runs


def _formation_timeline(team: dict) -> list[dict]:
    """Merge adjacent identical registered shapes; ignore 0-minute records."""
    result = []
    for f in team.get('formations', []):
        a = f.get('startMinuteExpanded')
        b = f.get('endMinuteExpanded')
        if a is None or b is None or b <= a:
            continue
        formation = f.get('formationName')
        if result and result[-1]['formation'] == formation and result[-1]['end_minute'] == a:
            result[-1]['end_minute'] = b
            result[-1]['duration_min'] = _round(float(b-result[-1]['start_minute']),1)
        else:
            result.append({'formation':formation, 'start_minute':a,
                           'end_minute':b, 'duration_min':_round(float(b-a),1)})
    return result


def _game_state_at_events(events: list[dict], home_id: int, away_id: int) -> dict[int, str]:
    score = {home_id: 0, away_id: 0}
    result = {}
    for e in events:
        if e['team_id'] in score:
            other = away_id if e['team_id'] == home_id else home_id
            diff = score[e['team_id']] - score[other]
            result[e['idx']] = 'leading' if diff > 0 else ('trailing' if diff < 0 else 'level')
        if e['type'] == 'Goal' and e['team_id'] in score:
            beneficiary = (away_id if e['team_id'] == home_id else home_id) if 'OwnGoal' in e['tags'] else e['team_id']
            score[beneficiary] += 1
    return result


def _zone(y: float | None) -> str:
    if y is None:
        return 'unknown'
    if y < WIDTH/3:
        return 'lower_y_third'
    if y > 2*WIDTH/3:
        return 'upper_y_third'
    return 'central_y_third'


def _pass_features(e: dict) -> dict:
    start_x, end_x, start_y, end_y = e['x'], e['end_x'], e['y'], e['end_y']
    if None in (start_x, end_x, start_y, end_y):
        return {'progress': None, 'forward_m': None, 'length_m': None,
                'progressive': False, 'final_third_entry': False, 'penalty_area_entry': False,
                'zone14_entry': False, 'half_space_entry': False, 'switch': False, 'cutback': False}
    forward = end_x-start_x
    length = _distance(start_x,start_y,end_x,end_y)
    progress = _goal_progress(e)
    return {'progress': _round(progress), 'forward_m': _round(forward), 'length_m': _round(length),
            'progressive': bool(progress is not None and progress >= 9.144 and _is_live_pass(e)),
            'final_third_entry': start_x < 70 <= end_x and _is_live_pass(e),
            'penalty_area_entry': (not (start_x >= 88.5 and 13.84 <= start_y <= 54.16) and
                                   end_x >= 88.5 and 13.84 <= end_y <= 54.16 and _is_live_pass(e)),
            'zone14_entry': 70 <= end_x < 88.5 and 22.66 <= end_y <= 45.33 and _is_live_pass(e),
            'half_space_entry': end_x >= 70 and ((11.33 <= end_y < 22.66) or (45.33 < end_y <= 56.67)) and _is_live_pass(e),
            'switch': length >= 35 and ((start_y < 22.67 and end_y > 45.33) or (start_y > 45.33 and end_y < 22.67)) and _is_live_pass(e),
            'cutback': (start_x >= 88.5 and (start_y < 13.84 or start_y > 54.16) and
                        end_x < start_x and end_x >= 78 and 20 <= end_y <= 48 and _is_live_pass(e)),
           }


def _team_summary(match: dict, events: list[dict], all_possessions: list[dict],
                  links: dict[int, dict], game_states: dict[int, str], side: str) -> dict:
    centre = match['matchCentreData']
    team = centre[side]
    id_ = team['teamId']
    players = {p['playerId']: p for p in team['players']}
    ev = [e for e in events if e['team_id'] == id_]
    passes = [e for e in ev if e['type'] == 'Pass' and e['end_x'] is not None]
    completed = [e for e in passes if e['outcome'] == 'Successful']
    live_passes = [e for e in passes if _is_live_pass(e)]
    live_complete = [e for e in completed if _is_live_pass(e)]
    f = {e['idx']: _pass_features(e) for e in passes}
    live_fc = [f[e['idx']] for e in live_complete]
    shots = [e for e in ev if e['type'] in SHOT_TYPES and 'OwnGoal' not in e['tags']]
    actions = [e for e in ev if e['type'] in DEFENSIVE_TYPES and e['x'] is not None]
    regains = [e for e in ev if e['type'] in {'BallRecovery', 'Interception'} and e['x'] is not None]
    high_regains = [e for e in regains if e['x'] >= 70]
    poss = [p for p in all_possessions if p['team_id'] == id_]
    poss_len = [len([e for e in p['events'] if e['type'] == 'Pass']) for p in poss]
    shot_sequences = []
    two_pass_patterns = Counter()
    transition_shots = 0
    high_regain_shots = 0
    possessions_detail = []
    for p in poss:
        pe = p['events']
        initial, last = pe[0], pe[-1]
        pass_count = sum(e['type'] == 'Pass' for e in pe)
        ps_shots = [e for e in pe if e['type'] in SHOT_TYPES]
        for shot in ps_shots:
            # A sequence is a *recorded-event* passing chain, not proof that
            # the same players occupied these coordinates simultaneously.
            shot_passes = [x for x in pe if x['type'] == 'Pass' and x['outcome'] == 'Successful'
                           and x['idx'] < shot['idx']]
            recent = shot_passes[-5:]
            shot_sequences.append({
                'minute':shot['minute'], 'second':shot['second'],
                'shooter':players.get(shot['player_id'],{}).get('name',str(shot['player_id'])),
                'shot_type':shot['type'], 'start_with_recovery':initial['type'] in {'BallRecovery','Interception'},
                'sequence_passes':len(shot_passes),
                'last_five_passers':[players.get(x['player_id'],{}).get('name',str(x['player_id'])) for x in recent],
                'last_five_passes':[{'from':players.get(x['player_id'],{}).get('name',str(x['player_id'])),
                                      'to':players.get(links[x['idx']]['receiver_id'],{}).get('name',str(links[x['idx']]['receiver_id']))
                                      if x['idx'] in links else None,
                                      'minute':x['minute'], 'x_m':_round(x['x']), 'y_m':_round(x['y']),
                                      'end_x_m':_round(x['end_x']), 'end_y_m':_round(x['end_y'])} for x in recent],
            })
            for first,second in zip(recent[:-1],recent[1:]):
                if first['idx'] in links and links[first['idx']]['receiver_id'] == second['player_id']:
                    two_pass_patterns[(first['player_id'],second['player_id'])] += 1
        x0s = [e['x'] for e in pe if e['x'] is not None]
        begins_regain = initial['type'] in {'BallRecovery','Interception'}
        fast_shot = any(s['time']-initial['time'] <= 15 for s in ps_shots)
        transition_shots += int(begins_regain and fast_shot)
        high_regain_shots += int(begins_regain and initial['x'] is not None and initial['x'] >= 70 and fast_shot)
        possessions_detail.append({'period':p['period'],'start_minute':initial['minute'],
                                   'duration_seconds':max(0,last['time']-initial['time']),
                                   'pass_count':pass_count,'start_x_m':_round(initial['x']),
                                   'max_x_m':_round(max(x0s) if x0s else None),
                                   'ended_in_shot':bool(ps_shots),
                                   'starts_with_regain':begins_regain})
    reported_possession = team.get('stats',{}).get('possession',{})
    # The provider 'possession' may be a minute-indexed dict. Do not force it
    # into an unverified percentage.
    player_rows = []
    receiver_count = Counter(link['receiver_id'] for e in completed if (link := links.get(e['idx'])))
    top_links = Counter((e['player_id'], links[e['idx']]['receiver_id'])
                        for e in completed if e['idx'] in links)
    for pid,p in players.items():
        pe = [e for e in ev if e['player_id']==pid]
        pa = [e for e in passes if e['player_id']==pid]
        pc = [e for e in completed if e['player_id']==pid]
        on_ball = [e for e in pe if e['type'] in CONTROL_TYPES and e['x'] is not None]
        player_rows.append({
            'id':pid, 'name':p['name'], 'listed_position':p.get('position'),
            'starter':bool(p.get('isFirstEleven')),
            'pass_attempts':len(pa), 'completed_passes':len(pc),
            'progressive_passes':sum(f[e['idx']]['progressive'] for e in pc),
            'final_third_entries':sum(f[e['idx']]['final_third_entry'] for e in pc),
            'key_passes':sum('KeyPass' in e['tags'] for e in pa),
            'through_balls':sum('Throughball' in e['tags'] for e in pa),
            'received_linked_passes':receiver_count[pid],
            'median_on_ball_x_m':_median(e['x'] for e in on_ball),
            'median_on_ball_y_m':_median(e['y'] for e in on_ball),
            'final_third_on_ball_actions':sum(e['x'] >= 70 for e in on_ball),
            'defensive_actions':sum(e['type'] in DEFENSIVE_TYPES for e in pe),
        })
    player_rows.sort(key=lambda p:(not p['starter'], -(p['pass_attempts']+p['received_linked_passes'])))
    link_rows=[]
    for (a,b),n in top_links.most_common(12):
        link_rows.append({'from':players.get(a,{}).get('name',str(a)),
                          'to':players.get(b,{}).get('name',str(b)), 'count':n})
    times = defaultdict(lambda: {'passes':0,'completed_passes':0,'progressive_passes':0,'shots':0, 'high_regains':0})
    for e in ev:
        phase = e['period']
        if phase not in {'FirstHalf','SecondHalf'}:
            continue
        key = f"{(e['minute']//15)*15:02d}-{((e['minute']//15)+1)*15:02d}"
        if e['type']=='Pass':
            times[key]['passes']+=1
            if e['outcome']=='Successful':
                times[key]['completed_passes']+=1
                times[key]['progressive_passes']+=int(f.get(e['idx'],{}).get('progressive',False))
        times[key]['shots']+=int(e['type'] in SHOT_TYPES)
        times[key]['high_regains']+=int(e in high_regains)  # object identity same normalized dict
    states = defaultdict(lambda: {'passes':0, 'completed':0,'shots':0})
    for e in ev:
        state = game_states.get(e['idx'],'unknown')
        if e['type']=='Pass':
            states[state]['passes'] +=1
            states[state]['completed'] +=int(e['outcome']=='Successful')
        states[state]['shots']+=int(e['type'] in SHOT_TYPES)
    phases=[]
    formation_intervals=_formation_timeline(team)
    for interval in formation_intervals:
        start, end = interval['start_minute'],interval['end_minute']
        phase_events=[e for e in ev if start <= e['minute'] < end]
        phase_passes=[e for e in phase_events if e['type']=='Pass']
        phase_complete=[e for e in phase_passes if e['outcome']=='Successful']
        phase_shots=[e for e in phase_events if e['type'] in SHOT_TYPES]
        phase_prog=sum(f.get(e['idx'],{}).get('progressive',False) for e in phase_complete)
        phase_halfspace=sum(f.get(e['idx'],{}).get('half_space_entry',False) for e in phase_complete)
        phase_minutes=max(1,end-start)
        phases.append({'formation':interval['formation'],'from_minute':start,'to_minute':end,
                       'duration_min':end-start,'pass_attempts':len(phase_passes),
                       'completion_pct':_pct(len(phase_complete),len(phase_passes)),
                       'passes_per_90':_round(len(phase_passes)*90/phase_minutes,1),
                       'progressive_per_90':_round(phase_prog*90/phase_minutes,1),
                       'halfspace_passes_per_90':_round(phase_halfspace*90/phase_minutes,1),
                       'shots_per_90':_round(len(phase_shots)*90/phase_minutes,1)})
    top_progressors=sorted(player_rows,key=lambda p:p['progressive_passes'],reverse=True)[:3]
    evidence=[
        {'claim':'Registered starting formation','level':'recorded',
         'finding':str((team.get('formations') or [{}])[0].get('formationName')),
         'basis':'team formations metadata'},
        {'claim':'In-match registered formation changes','level':'recorded',
         'finding': ' → '.join(f"{x['formation']} ({x['start_minute']}–{x['end_minute']}′)" for x in formation_intervals),
         'basis':'start/end minute formation intervals; duplicate adjacent identical formations merged'},
        {'claim':'Ball circulation','level':'event-derived',
         'finding':f"{len(completed)}/{len(passes)} successful passes ({_pct(len(completed),len(passes))}%), "
                   f"mean live completed pass length {_round(mean(x['length_m'] for x in live_fc if x['length_m'] is not None)) if live_fc else None} m",
         'basis':'pass success and start/end coordinates'},
        {'claim':'Most progressive passers','level':'event-derived',
         'finding':'; '.join(f"{p['name']} {p['progressive_passes']}" for p in top_progressors),
         'basis':'completed live-ball passes gaining at least 9.144 m of distance to goal'},
        {'claim':'Final-third receiving corridors','level':'event-derived',
         'finding':f"half-spaces {sum(x['half_space_entry'] for x in live_fc)}; "
                   f"Zone 14 {sum(x['zone14_entry'] for x in live_fc)}",
         'basis':'pass endpoint areas; not all off-ball occupations or line breaks'},
        {'claim':'Off-ball attacking shape and pressing trigger','level':'not-identifiable',
         'finding':'Not identifiable reliably from event-only HTML',
         'basis':'Requires simultaneous player locations and/or video'},
    ]
    return {
        'team':team['name'], 'team_id':id_, 'side':side,
        'tactical_evidence':evidence,
        'formation_phase_comparison':phases,
        'registered_formations':_formation_timeline(team),
        'first_registered_formation':(team.get('formations') or [{}])[0].get('formationName'),
        'counts':{'events':len(ev), 'shots':len(shots), 'goals':sum(e['type']=='Goal' for e in shots),
                  'defensive_actions':len(actions), 'regains':len(regains), 'high_regains':len(high_regains)},
        'passing':{
            'attempts':len(passes), 'completed':len(completed),'completion_pct':_pct(len(completed),len(passes)),
            'live_attempts':len(live_passes),'live_completed':len(live_complete),
            'progressive_passes':sum(p['progressive'] for p in live_fc),
            'progressive_share_pct':_pct(sum(p['progressive'] for p in live_fc),len(live_complete)),
            'mean_pass_length_m':_round(mean(p['length_m'] for p in live_fc if p['length_m'] is not None)) if live_fc else None,
            'forward_pass_share_pct':_pct(sum((p['forward_m'] or 0) >= 5 for p in live_fc),len(live_fc)),
            'backward_pass_share_pct':_pct(sum((p['forward_m'] or 0) <= -5 for p in live_fc),len(live_fc)),
            'final_third_entries':sum(p['final_third_entry'] for p in live_fc),
            'penalty_area_entries':sum(p['penalty_area_entry'] for p in live_fc),
            'zone14_passes':sum(p['zone14_entry'] for p in live_fc),
            'halfspace_passes':sum(p['half_space_entry'] for p in live_fc),
            'switches_of_play':sum(p['switch'] for p in live_fc),
            'cutback_pass_proxies':sum(p['cutback'] for p in live_fc),
            'through_ball_attempts':sum('Throughball' in e['tags'] for e in passes),
            'cross_attempts':sum('Cross' in e['tags'] and _is_live_pass(e) for e in passes),
            'key_passes':sum('KeyPass' in e['tags'] for e in passes),
            'progressive_origins_by_y':dict(Counter(_zone(e['y']) for e in live_complete if f[e['idx']]['progressive'])),
        },
        'pass_link_quality':{
            'linked_completed_passes':sum(e['idx'] in links for e in completed),
            'linked_share_pct':_pct(sum(e['idx'] in links for e in completed),len(completed)),
            'high_confidence_links':sum(links[e['idx']]['confidence']=='high' for e in completed if e['idx'] in links),
            'warning':'Receiver links are proximity/time inferred; missing links are not silently assigned.'},
        'top_directed_passing_connections':link_rows,
        'shot_sequences':shot_sequences,
        'frequent_shot_buildup_links':[
            {'first_passer':players.get(a,{}).get('name',str(a)),
             'second_passer':players.get(b,{}).get('name',str(b)), 'count':n}
            for (a,b),n in two_pass_patterns.most_common(10)
        ],
        'possession_proxies':{
            'segments':len(poss), 'median_passes_per_segment':_median(poss_len),
            'segments_10_plus_passes':sum(n>=10 for n in poss_len),
            'segments_ending_in_shot':sum(p['ended_in_shot'] for p in possessions_detail),
            'shots_within_15s_of_regain':transition_shots,
            'shots_within_15s_of_high_regain':high_regain_shots,
            'sample_segments':sorted(possessions_detail,key=lambda p:p['pass_count'],reverse=True)[:8],
            'warning':'Possessions are reconstructed from recorded ball-control events, not a provider possession ID.'},
        'defence_event_proxies':{'median_defensive_action_x_m':_median(e['x'] for e in actions),
                                 'median_regain_x_m':_median(e['x'] for e in regains),
                                 'high_regain_pct':_pct(len(high_regains),len(regains)),
                                 'warning':'Defensive events do not reveal the full off-ball block or pressing triggers.'},
        'time_slices':{k:v for k,v in sorted(times.items())},
        'game_state_slices':dict(states),
        'players':player_rows,
    }


def analyze_match(source: str | Path | bytes | dict) -> dict:
    root = source if isinstance(source,dict) else load_match(source)
    centre = root['matchCentreData']
    ev = normalize_events(centre['events'])
    links = link_pass_receivers(ev)
    poss = build_possessions(ev)
    game_states = _game_state_at_events(ev,centre['home']['teamId'],centre['away']['teamId'])
    teams = {side:_team_summary(root,ev,poss,links,game_states,side) for side in ('home','away')}
    return {'match_id':root.get('matchId'), 'date':centre.get('startDate'), 'start_time':centre.get('startTime'),
            'venue':centre.get('venueName'), 'score':centre.get('score'),
            'home':centre['home']['name'], 'away':centre['away']['name'],
            'raw_event_count':len(ev), 'teams':teams,
            'data_limits':['Event coordinates describe on-ball or defensive actions, not simultaneous player locations.',
                           'Attacking/off-ball formations, cover shadows, and pressing triggers require tracking or video.',
                           'No independently verified xG/xT values are inferred from this HTML.',
                           'Pass receivers and possession segments are checked estimates, not provider-confirmed fields.']}


def select_team(result: dict, query: str) -> dict:
    if not query or not query.strip():
        raise ValueError("Team name must not be empty")
    for side,team in result['teams'].items():
        if query.lower().strip() == team['team'].lower():
            return team
    for side,team in result['teams'].items():
        if query.lower().strip() in team['team'].lower():
            return team
    raise ValueError(f"Team '{query}' not found in {result['home']} vs {result['away']}.")


def aggregate_team(matches: list[dict], team_name: str) -> dict:
    """Aggregate quantities by counts, not a mean of rounded percentages."""
    selected=[(m,select_team(m,team_name)) for m in matches]
    if not selected:
        raise ValueError('No matches provided')
    totals={}
    for k in ['attempts','completed','live_attempts','live_completed','progressive_passes',
              'final_third_entries','penalty_area_entries','zone14_passes','halfspace_passes',
              'switches_of_play','through_ball_attempts','cross_attempts','key_passes']:
        totals[k]=sum(t['passing'][k] for _,t in selected)
    totals['completion_pct']=_pct(totals['completed'],totals['attempts'])
    totals['progressive_share_pct']=_pct(totals['progressive_passes'],totals['live_completed'])
    return {'team':team_name, 'match_count':len(selected), 'passes_total':totals,
            'per_match':[{'match_id':m['match_id'],'opponent':m['away'] if t['side']=='home' else m['home'],
                          'score':m['score'], 'formation':t['first_registered_formation'],
                          'passing':t['passing'], 'defence_event_proxies':t['defence_event_proxies'],
                          'pass_link_quality':t['pass_link_quality']} for m,t in selected],
            'warning':'Do not infer a stable team tactical identity from a single match; these are event patterns.'}


def as_markdown(match: dict, team_query: str) -> str:
    t=select_team(match,team_query); p=t['passing']; c=t['counts']; d=t['defence_event_proxies']; l=t['pass_link_quality']
    passages=[f"# Tactical event decoder: {t['team']} vs {match['away'] if t['side']=='home' else match['home']}",
              f"**Date:** {match['date']}  |  **Score:** {match['score']}  |  **Match:** {match['match_id']}",
              '', '## Evidence from the HTML',
              f"Registered starting formation: **{t['first_registered_formation']}**. This is not proof of the in-possession shape.",
              f"Completed passes: **{p['completed']}/{p['attempts']} ({p['completion_pct']}%)**. "
              f"Progressive live-ball passes (≥9.144 m closer to goal): **{p['progressive_passes']}**.",
              f"Final-third entries: **{p['final_third_entries']}**; penalty-area pass entries: **{p['penalty_area_entries']}**. "
              f"Zone-14 pass receipts: **{p['zone14_passes']}**; half-space pass receipts: **{p['halfspace_passes']}**.",
              f"Wide switches (strict proxy): **{p['switches_of_play']}**; through-ball attempts: **{p['through_ball_attempts']}**; open-play crosses: **{p['cross_attempts']}**.",
              f"Regains high up the field: **{c['high_regains']}/{c['regains']}** ({d['high_regain_pct']}%); "
              f"median defensive-action location: **{d['median_defensive_action_x_m']} m**. "
              "This is defensive-action height, not defensive-line height.",
              f"Estimated pass-receiver link rate: **{l['linked_completed_passes']}/{p['completed']} ({l['linked_share_pct']}%)**. "
              "Unlinked receivers are omitted.", '', '## Main directed passing connections']
    for item in t['top_directed_passing_connections'][:8]:
        passages.append(f"- {item['from']} → {item['to']}: {item['count']} linked successful passes")
    passages += ['', '## Registered formation periods (score/substitutions can confound comparisons)']
    for phase in t['formation_phase_comparison']:
        passages.append(f"- {phase['formation']} ({phase['from_minute']}–{phase['to_minute']}′): "
                        f"{phase['passes_per_90']} pass attempts/90; {phase['progressive_per_90']} progressive passes/90; "
                        f"{phase['shots_per_90']} shots/90")
    passages += ['', '## Recorded passing routes before shots']
    for sh in t['shot_sequences'][:8]:
        chain=' → '.join(sh['last_five_passers'][-3:] + [sh['shooter']])
        passages.append(f"- {sh['minute']}′ {sh['shot_type']}: {chain}")
    passages += ['', '## On-ball roles (top passing participants)']
    for player in t['players'][:8]:
        passages.append(f"- {player['name']} ({player['listed_position']}): {player['pass_attempts']} passes, "
                        f"{player['progressive_passes']} progressive, {player['received_linked_passes']} linked receptions; "
                        f"median event position ({player['median_on_ball_x_m']}, {player['median_on_ball_y_m']}) m")
    passages += ['', '## Data interpretation limits']
    passages += [f'- {lim}' for lim in match['data_limits']]
    return '\n'.join(passages)+'\n'


def main() -> None:
    parser=argparse.ArgumentParser(description='Event-based WhoScored tactical decoder')
    parser.add_argument('html',nargs='+',help='Saved WhoScored Live HTML files')
    parser.add_argument('--team',required=True,help='Team to focus on (e.g. Como)')
    parser.add_argument('--output',default='tactics.json',help='Output JSON file')
    parser.add_argument('--markdown',default=None,help='Optional Markdown overview for first match')
    args=parser.parse_args()
    matches=[analyze_match(Path(p)) for p in args.html]
    summary={'team':args.team,'matches':matches,'aggregate':aggregate_team(matches,args.team)}
    Path(args.output).write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    if args.markdown:
        Path(args.markdown).write_text(as_markdown(matches[0],args.team),encoding='utf-8')
    print(f"Decoded {len(matches)} match(es); report: {args.output}")
    for m in matches:
        t=select_team(m,args.team)
        print(f"  {m['home']} {m['score']} {m['away']}: formation {t['first_registered_formation']}, "
              f"passes {t['passing']['completed']}/{t['passing']['attempts']}, "
              f"progressive {t['passing']['progressive_passes']}, linked {t['pass_link_quality']['linked_share_pct']}%")


if __name__=='__main__':
    main()
