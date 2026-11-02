"""Bounded, aggregate contest evidence for Hermes selection.

Regular CF standings use the anonymous, single-parameter public contract.
Gym standings require the user's existing API key. Signed URLs, ranklist rows,
handles and team names are never cached. Transport errors expose fixed reasons.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import secrets
import time
from urllib.parse import urlsplit

import requests
from ojcrawler.http import _HOST_LOCKS, _LAST_REQUEST

from .difficulty import MIN_POPULATION

TTL_SECONDS = 6 * 60 * 60
FAILURE_TTL_SECONDS = 10 * 60
MAX_BODY_BYTES = 24 * 1024 * 1024
_ALLOWED_HOSTS = {'codeforces.com', 'board.xcpcio.com', 'rank.iccpc.cn'}


class EvidenceUnavailable(Exception):
    """Only a fixed, public reason may leave this module."""


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _cancel(stop):
    if stop is not None and stop.is_set():
        raise asyncio.CancelledError


def _public_get(url, params, stop):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in _ALLOWED_HOSTS or parsed.username or parsed.password:
        raise EvidenceUnavailable('invalid_source')
    _cancel(stop)
    if parsed.hostname == 'codeforces.com':
        with _HOST_LOCKS['codeforces.com']:
            wait = max(0.0, 2.1 - (time.monotonic() - _LAST_REQUEST['codeforces.com']))
            if stop is not None:
                if stop.wait(wait):
                    raise asyncio.CancelledError
            else:
                time.sleep(wait)
            _cancel(stop)
            _LAST_REQUEST['codeforces.com'] = time.monotonic()
    try:
        with requests.Session() as client:
            client.trust_env = False
            with client.get(url, params=params, headers={'Accept': 'application/json'},
                            stream=True, timeout=12, allow_redirects=False) as response:
                if response.status_code != 200:
                    reason = 'rate_limited' if response.status_code == 429 else 'access_unavailable'
                    raise EvidenceUnavailable(reason)
                data = bytearray()
                started = time.monotonic()
                for chunk in response.iter_content(chunk_size=65536):
                    _cancel(stop)
                    if len(data) + len(chunk) > MAX_BODY_BYTES or time.monotonic() - started > 25:
                        raise EvidenceUnavailable('response_limit')
                    data.extend(chunk)
                result = json.loads(data)
                _cancel(stop)
                return result
    except EvidenceUnavailable:
        raise
    except Exception:
        raise EvidenceUnavailable('network_or_format') from None


def _problem_rows(result, contest_id, gym, observed_at):
    if not isinstance(result, dict):
        raise EvidenceUnavailable('invalid_standings')
    contest, problems, rows = result.get('contest'), result.get('problems'), result.get('rows')
    if (not isinstance(contest, dict) or contest.get('id') != contest_id
            or contest.get('phase') != 'FINISHED' or contest.get('type') not in {'CF', 'ICPC'} or not _integer(contest.get('startTimeSeconds'), 946684800)
            or contest['startTimeSeconds'] > observed_at
            or not isinstance(problems, list) or not problems or len(problems) > 100
            or not isinstance(rows, list) or len(rows) > 100000
            or result.get('truncated') or result.get('hasMore')):
        raise EvidenceUnavailable('invalid_standings')
    accepted = [0] * len(problems)
    attempted = [0] * len(problems)
    submissions = [0] * len(problems)
    participants, seen, team_parties = 0, set(), False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('party'), dict):
            raise EvidenceUnavailable('invalid_standings')
        party = row['party']
        if party.get('participantType') != 'CONTESTANT' or party.get('ghost'):
            continue
        members = party.get('members')
        if not isinstance(members, list) or not members or any(not isinstance(m, dict) or not isinstance(m.get('handle'), str) for m in members):
            raise EvidenceUnavailable('invalid_standings')
        identity = tuple(sorted(m['handle'].casefold() for m in members))
        if identity in seen:
            continue
        seen.add(identity)
        values = row.get('problemResults')
        if not isinstance(values, list) or len(values) != len(problems):
            raise EvidenceUnavailable('invalid_standings')
        participants += 1
        team_parties = team_parties or bool(party.get('teamId')) or len(members) > 1
        for i, value in enumerate(values):
            if not isinstance(value, dict) or value.get('type') not in {'FINAL', 'PRELIMINARY'}:
                raise EvidenceUnavailable('invalid_standings')
            # A FINISHED contest with preliminary judging is not final evidence.
            if value.get('type') != 'FINAL':
                raise EvidenceUnavailable('judging_incomplete')
            points, failed = value.get('points'), value.get('rejectedAttemptCount')
            if isinstance(points, bool) or not isinstance(points, (int, float)) or not math.isfinite(points) or points < 0 or not _integer(failed):
                raise EvidenceUnavailable('invalid_standings')
            solved = points > 0
            accepted[i] += int(solved)
            attempted[i] += int(solved or failed > 0)
            submissions[i] += failed + int(solved)
    output, statistics = [], {}
    for i, problem in enumerate(problems):
        if (not isinstance(problem, dict) or problem.get('contestId') != contest_id
                or not isinstance(problem.get('index'), str) or not re.fullmatch(r'[A-Za-z][0-9]?', problem['index'])
                or not isinstance(problem.get('name'), str)):
            raise EvidenceUnavailable('invalid_standings')
        index = problem['index']
        stats = {'participants': participants, 'accepted_participants': accepted[i],
                 'attempted_participants': attempted[i], 'submissions': submissions[i],
                 'accepted_submissions': accepted[i], 'source': 'codeforces.contest.standings',
                 'observed_at': observed_at, 'scope': 'contest',
                 'participant_unit': 'team' if team_parties else 'person', 'complete': True,
                 'contest_id': contest_id, 'problem_index': index,
                 'submission_count_kind': 'scoring_attempts_until_first_solve'}
        if participants:
            statistics[index] = stats
        row = {'id': f'codeforces:{contest_id}-{index}', 'platform': 'codeforces',
               'problem_id': f'{contest_id}-{index}', 'title': problem['name'][:500],
               'url': f'https://codeforces.com/{"gym" if gym else "contest"}/{contest_id}/problem/{index}',
               'tags': [v[:100] for v in problem.get('tags', []) if isinstance(v, str)][:30],
               'published_at': contest['startTimeSeconds'],
               'published_at_source': 'codeforces.contest.startTimeSeconds',
               'contest_id': contest_id, 'contest_name': str(contest.get('name', ''))[:500],
               'is_gym': gym}
        if participants:
            row['contest_statistics'] = stats
        if _integer(problem.get('rating'), 1):
            row['difficulty'] = problem['rating']
            row['official_rating'] = problem['rating']
        output.append(row)
    if len({p['problem_id'] for p in output}) != len(problems):
        raise EvidenceUnavailable('invalid_standings')
    return {'status': 'available' if participants else 'metadata_only',
            'reason': None if participants else 'no_official_participants', 'contest': {'id': contest_id, 'name': str(contest.get('name', ''))[:500],
            'published_at': contest['startTimeSeconds'], 'gym': gym}, 'problems': output, 'statistics': statistics}


class ContestEvidenceProvider:
    def __init__(self, database=None, *, api_key=None, api_secret=None, stop=None, request=None):
        self.database, self.api_key, self.api_secret = database, api_key, api_secret
        self.stop = stop
        self.request = request

    def _get(self, url, params=None):
        _cancel(self.stop)
        result = self.request(url, params) if self.request else _public_get(url, params, self.stop)
        _cancel(self.stop)
        return result

    def _cached(self, key):
        if self.database is None:
            return None
        value = self.database.get(key)
        now = int(time.time())
        if not isinstance(value, dict) or not _integer(value.get('at')):
            return None
        result = value.get('result')
        ttl = TTL_SECONDS if isinstance(result, dict) and result.get('status') in {'available', 'metadata_only'} else FAILURE_TTL_SECONDS
        return result if 0 <= now - value['at'] < ttl else None

    def _save(self, key, result):
        _cancel(self.stop)
        if self.database is not None:
            self.database.set(key, {'at': int(time.time()), 'result': result})
        return result

    def standings(self, contest_id, *, gym=False):
        _cancel(self.stop)
        if not _integer(contest_id, 1):
            return {'status': 'unavailable', 'reason': 'invalid_contest'}
        if gym and (not self.api_key or not self.api_secret):
            return {'status': 'unavailable', 'reason': 'gym_api_required'}
        key = f'hermes_contest_evidence:v1:{"gym" if gym else "contest"}:{contest_id}'
        cached = self._cached(key)
        if cached:
            return cached
        params = {'contestId': str(contest_id)}
        if gym:
            params.update(apiKey=self.api_key, time=str(int(time.time())), showUnofficial='false')
            nonce = ''.join(secrets.choice('0123456789abcdefghijklmnopqrstuvwxyz') for _ in range(6))
            canonical = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
            params['apiSig'] = nonce + hashlib.sha512(f'{nonce}/contest.standings?{canonical}#{self.api_secret}'.encode()).hexdigest()
        try:
            payload = self._get('https://codeforces.com/api/contest.standings', params)
            if not isinstance(payload, dict) or payload.get('status') != 'OK' or payload.get('truncated'):
                raise EvidenceUnavailable('official_api_unavailable')
            result = _problem_rows(payload.get('result'), contest_id, gym, int(time.time()))
        except EvidenceUnavailable as error:
            result = {'status': 'unavailable', 'reason': str(error)}
        except Exception:
            result = {'status': 'unavailable', 'reason': 'network_or_format'}
        return self._save(key, result)

    def gym_catalog(self, limit=2):
        if not self.api_key or not self.api_secret:
            return {'status': 'unavailable', 'reason': 'gym_api_required', 'rows': [], 'contests_checked': 0}
        limit = max(0, min(2, int(limit)))
        if not limit:
            return {'status': 'available', 'rows': [], 'contests_checked': 0}
        try:
            listing_key = 'hermes_contest_evidence:v1:gym_listing'
            cached = self._cached(listing_key)
            payload = cached.get('payload') if cached else None
            if payload is None:
                payload = self._get('https://codeforces.com/api/contest.list', {'gym': 'true'})
            if not isinstance(payload, dict) or payload.get('status') != 'OK' or not isinstance(payload.get('result'), list):
                raise EvidenceUnavailable('official_api_unavailable')
            if not cached:
                self._save(listing_key, {'status': 'available', 'payload': {'status': 'OK', 'result': [
                {k: c[k] for k in ('id', 'phase', 'startTimeSeconds') if k in c}
                for c in payload['result'] if isinstance(c, dict)]}})
            now = int(time.time())
            choices = [c for c in payload['result'] if isinstance(c, dict) and c.get('phase') == 'FINISHED'
                       and _integer(c.get('id'), 100000) and _integer(c.get('startTimeSeconds'), 946684800)
                       and c['startTimeSeconds'] <= now]
            choices = random.SystemRandom().sample(choices, min(limit, len(choices)))
            rows, unavailable, onsite_checked = [], 0, False
            onsite_status = 'not_needed'
            for contest in choices:
                result = self.standings(contest['id'], gym=True)
                candidates = result.get('problems', [])
                insufficient = result['status'] == 'metadata_only' or (result['status'] == 'available' and
                    any(s.get('participants', 0) < MIN_POPULATION for s in result.get('statistics', {}).values()))
                if candidates and insufficient and not onsite_checked:
                    onsite_checked = True
                    meta = result['contest']
                    source = 'iccpc' if 'ccpc' in meta['name'].casefold() or '中国大学生' in meta['name'] else 'xcpcio'
                    onsite = self.onsite_for_contest(meta['name'], meta['published_at'],
                        [c['problem_id'].split('-')[-1] for c in candidates], source=source)
                    onsite_status = onsite.get('reason') or onsite['status']
                    if onsite['status'] == 'available':
                        for candidate in candidates:
                            label = candidate['problem_id'].split('-')[-1]
                            candidate['contest_statistics'] = dict(onsite['statistics'][label],
                                contest_id=contest['id'], problem_index=label)
                        result = dict(result, status='available')
                if result['status'] == 'available':
                    rows.extend(candidates)
                else:
                    unavailable += 1
            return {'status': 'available' if rows else 'unavailable', 'rows': rows,
                    'contests_checked': len(choices), 'unavailable_contests': unavailable,
                    'onsite_status': onsite_status, 'onsite_events_checked': int(onsite_checked),
                    'request_budget': '至多1次Gym索引+2场签名榜；额外至多1个现场索引和1场现场榜（XCPC为3个静态文件）',
                    'coverage': '每次随机采样至多两场已结束 Gym；仅纳入有完整官方参赛榜或精确匹配现场榜的可见比赛'}
        except EvidenceUnavailable as error:
            return {'status': 'unavailable', 'reason': str(error), 'rows': [], 'contests_checked': 0}
        except Exception:
            return {'status': 'unavailable', 'reason': 'network_or_format', 'rows': [], 'contests_checked': 0}

    def onsite_index(self, source):
        """Discover only the public static indexes actually used by both sites."""
        if source not in {'xcpcio', 'iccpc'}:
            return {'status': 'unavailable', 'reason': 'invalid_source', 'contests': []}
        key = f'hermes_contest_evidence:v1:onsite_index:{source}'
        cached = self._cached(key)
        if cached:
            return cached
        try:
            contests = []
            if source == 'xcpcio':
                payload = self._get('https://board.xcpcio.com/data/index/contest_list.json')
                def walk(value, depth=0):
                    if depth > 12 or len(contests) > 10000:
                        raise EvidenceUnavailable('response_limit')
                    if not isinstance(value, dict):
                        return
                    if isinstance(value.get('config'), dict) and isinstance(value.get('board_link'), str):
                        config, ref = value['config'], value['board_link']
                        if _xcpc_ref(ref) and _integer(config.get('start_time'), 946684800):
                            contests.append({'name': _name(config.get('contest_name')), 'published_at': config['start_time'], 'ref': ref})
                    else:
                        for child in value.values():
                            walk(child, depth + 1)
                walk(payload)
            else:
                payload = self._get('https://rank.iccpc.cn/pub/list.json')
                if not isinstance(payload, list) or len(payload) > 10000:
                    raise EvidenceUnavailable('invalid_onsite_index')
                for row in payload:
                    if not isinstance(row, dict) or not row.get('has_rank') or row.get('is_warmup') or str(row.get('defunct', '0')) != '0':
                        continue
                    ref = row.get('outrank_uuid')
                    if isinstance(ref, str) and re.fullmatch(r'[a-f0-9-]{36}', ref):
                        timestamp = _iso_timestamp(row.get('start_time'))
                        if timestamp:
                            contests.append({'name': _name(row.get('title')), 'published_at': timestamp, 'ref': ref})
            return self._save(key, {'status': 'available', 'contests': contests})
        except EvidenceUnavailable as error:
            return self._save(key, {'status': 'unavailable', 'reason': str(error), 'contests': []})
        except Exception:
            return self._save(key, {'status': 'unavailable', 'reason': 'network_or_format', 'contests': []})

    def onsite_snapshot(self, source, ref):
        """Read one explicitly matched event; return aggregate evidence, never teams."""
        if not ((source == 'xcpcio' and _xcpc_ref(ref)) or
                (source == 'iccpc' and isinstance(ref, str) and re.fullmatch(r'[a-f0-9-]{36}', ref))):
            return {'status': 'unavailable', 'reason': 'invalid_source'}
        key = f'hermes_contest_evidence:v1:onsite:{source}:{ref}'
        cached = self._cached(key)
        if cached:
            return cached
        try:
            if source == 'xcpcio':
                base = 'https://board.xcpcio.com/data' + ref
                config = self._get(base + '/config.json')
                teams = self._get(base + '/team.json')
                runs = self._get(base + '/run.json')
                result = _xcpc_statistics(config, teams, runs, ref)
            else:
                payload = self._get(f'https://rank.iccpc.cn/upload/outrank_attach/{ref}/rank.json')
                result = _iccpc_statistics(payload, ref)
            return self._save(key, result)
        except EvidenceUnavailable as error:
            return self._save(key, {'status': 'unavailable', 'reason': str(error)})
        except Exception:
            return self._save(key, {'status': 'unavailable', 'reason': 'network_or_format'})

    def onsite_for_contest(self, name, published_at, problem_indices, *, source='xcpcio'):
        """Only exact event name/date/labels mapping; never fuzzy map different events."""
        index = self.onsite_index(source)
        if index['status'] != 'available':
            return {'status': 'unavailable', 'reason': 'onsite_index_unavailable'}
        names = _normalized_name(name)
        matches = [c for c in index['contests'] if names and _normalized_name(c['name']) == names
                   and _integer(published_at) and abs(c['published_at'] - published_at) <= 24 * 3600]
        if len(matches) != 1:
            return {'status': 'unavailable', 'reason': 'onsite_mapping_unverified'}
        result = self.onsite_snapshot(source, matches[0]['ref'])
        if result.get('status') != 'available' or set(result['statistics']) != set(problem_indices):
            return {'status': 'unavailable', 'reason': 'onsite_mapping_unverified'}
        return result


def _xcpc_ref(value):
    return isinstance(value, str) and bool(re.fullmatch(r'/(?:icpc|ccpc|provincial-contest|camp)/[A-Za-z0-9_/-]{1,180}', value)) and '..' not in value and '//' not in value


def _name(value):
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, dict):
        return next((v[:500] for v in value.values() if isinstance(v, str)), '')
    return ''


def _normalized_name(value):
    return re.sub(r'\W+', '', _name(value).casefold())


def _iso_timestamp(value):
    from datetime import datetime
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return int(parsed.timestamp()) if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _aggregate_onsite(problems, teams, runs, *, source, ref, published_at, contest_name):
    if not problems or not teams or len(problems) > 100 or len(teams) > 100000 or len(runs) > 300000:
        raise EvidenceUnavailable('invalid_onsite_data')
    observed_at = int(time.time())
    if not _integer(published_at, 946684800) or published_at > observed_at:
        raise EvidenceUnavailable('invalid_onsite_data')
    if len(set(problems.values())) != len(problems):
        raise EvidenceUnavailable('invalid_onsite_data')
    accepted, attempted, submissions, accepted_submissions = {}, {}, {}, {}
    for label in problems.values():
        if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z][0-9]?', label):
            raise EvidenceUnavailable('invalid_onsite_data')
        accepted[label], attempted[label], submissions[label], accepted_submissions[label] = set(), set(), 0, 0
    seen = set()
    for run_id, team, problem, is_ac in runs:
        if run_id in seen or team not in teams or problem not in problems:
            continue
        seen.add(run_id)
        label = problems[problem]
        attempted[label].add(team)
        submissions[label] += 1
        if is_ac:
            accepted[label].add(team)
            accepted_submissions[label] += 1
    stats = {label: {'participants': len(teams), 'accepted_participants': len(accepted[label]),
             'attempted_participants': len(attempted[label]), 'submissions': submissions[label],
             'accepted_submissions': accepted_submissions[label], 'source': source,
             'observed_at': observed_at, 'scope': 'contest', 'participant_unit': 'team',
             'complete': True, 'event_ref': ref, 'problem_index': label}
             for label in accepted}
    return {'status': 'available', 'statistics': stats, 'contest': {'name': _name(contest_name),
            'published_at': published_at}, 'source': source, 'ref': ref}


def _xcpc_statistics(config, teams, runs, ref):
    if not isinstance(config, dict) or not isinstance(teams, list) or not isinstance(runs, list):
        raise EvidenceUnavailable('invalid_onsite_data')
    if not _integer(config.get('end_time')) or config['end_time'] > time.time():
        raise EvidenceUnavailable('onsite_not_final')
    if not isinstance(config.get('problems'), list):
        raise EvidenceUnavailable('invalid_onsite_data')
    problems = {str(p['id']): p['label'] for p in config['problems'] if isinstance(p, dict) and 'id' in p and 'label' in p}
    if len(problems) != len(config['problems']) or (
            'problem_quantity' in config and config['problem_quantity'] != len(problems)):
        raise EvidenceUnavailable('invalid_onsite_data')
    # The official group is explicit. Unmarked/invitational teams are excluded.
    included = {str(t['team_id']) for t in teams if isinstance(t, dict) and 'team_id' in t and
                isinstance(t.get('group'), list) and 'official' in t['group']}
    parsed = []
    pending = {'PENDING', 'SUBMITTED', 'RUNNING', 'FROZEN'}
    for i, run in enumerate(runs):
        if not isinstance(run, dict) or run.get('status') in pending:
            raise EvidenceUnavailable('onsite_not_final')
        if run.get('status') not in {'CORRECT', 'REJECTED', 'WRONG_ANSWER', 'TIME_LIMIT_EXCEEDED', 'RUNTIME_ERROR', 'COMPILATION_ERROR', 'MEMORY_LIMIT_EXCEEDED', 'PRESENTATION_ERROR', 'OUTPUT_LIMIT_EXCEEDED'}:
            raise EvidenceUnavailable('unknown_onsite_verdict')
        parsed.append((str(run.get('id', i)), str(run.get('team_id')), str(run.get('problem_id')), run.get('status') == 'CORRECT'))
    return _aggregate_onsite(problems, included, parsed, source='https://board.xcpcio.com' + ref, ref=ref,
                             published_at=config.get('start_time'), contest_name=config.get('contest_name'))


def _iccpc_statistics(payload, ref):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not isinstance(payload, dict) or any(not isinstance(payload.get(k), list) for k in ('team', 'problem', 'solution')):
        raise EvidenceUnavailable('invalid_onsite_data')
    contest = payload.get('contest', {})
    context = payload.get('time_context', {})
    if context.get('wall_clock_timezone') != 'Asia/Shanghai' or context.get('encoding') != 'naive_app_wall':
        raise EvidenceUnavailable('unknown_onsite_timezone')
    try:
        start = int(datetime.strptime(contest['start_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        end = int(datetime.strptime(contest['end_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
    except (ValueError, KeyError, TypeError):
        raise EvidenceUnavailable('invalid_onsite_data') from None
    if end > time.time() or payload.get('frozen') or payload.get('is_frozen'):
        raise EvidenceUnavailable('onsite_not_final')
    included = {str(t['team_id']) for t in payload['team'] if isinstance(t, dict) and 'team_id' in t and t.get('tkind') == 0}
    problems = {str(p['problem_id']): chr(ord('A') + p['num']) for p in payload['problem'] if isinstance(p, dict)
                and 'problem_id' in p and _integer(p.get('num')) and p['num'] < 26}
    if len(problems) != len(payload['problem']):
        raise EvidenceUnavailable('invalid_onsite_data')
    runs = []
    for run in payload['solution']:
        if not isinstance(run, dict) or not _integer(run.get('solution_id')) or run.get('result') not in range(4, 12):
            raise EvidenceUnavailable('onsite_not_final')
        runs.append((str(run['solution_id']), str(run.get('team_id')), str(run.get('problem_id')), run['result'] == 4))
    return _aggregate_onsite(problems, included, runs, source='https://rank.iccpc.cn/r/' + ref, ref=ref,
                             published_at=start, contest_name=contest.get('title'))
