"""Opt-in community profiles and official-evidence Codeforces duels.

Only metadata lives in the identity database. Cookies, drafts and AI records
remain in isolated workspaces. Clients never supply a verdict or finish time.
"""
import asyncio
import json
import re
import secrets
import time
import uuid
from datetime import date, datetime, timedelta
from contextlib import ExitStack
from zoneinfo import ZoneInfo

import requests
from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .catalog import publication_time
from .codex_bridge import AIError
from .problem_sources import atcoder_contest

TZ = ZoneInfo('Asia/Shanghai')
PLATFORMS = ('codeforces', 'atcoder', 'luogu', 'nowcoder')
def pace_official_api():
    # Share pacing with account collectors and workbench judge synchronization.
    from ojcrawler.http import _HOST_LOCKS, _LAST_REQUEST
    host = 'codeforces.com'
    with _HOST_LOCKS[host]:
        time.sleep(max(0, 2.1 - (time.monotonic() - _LAST_REQUEST[host])))
        _LAST_REQUEST[host] = time.monotonic()


DDL = '''
CREATE TABLE IF NOT EXISTS social_profiles (
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 display_name TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '',
 public_profile INTEGER NOT NULL DEFAULT 0, leaderboard INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS social_accounts (
 platform TEXT NOT NULL, account_key TEXT NOT NULL, handle TEXT NOT NULL,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 credential_hash TEXT NOT NULL, verified_at INTEGER NOT NULL,
 PRIMARY KEY(platform,account_key));
CREATE TABLE IF NOT EXISTS social_account_optouts (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 platform TEXT NOT NULL, account_key TEXT NOT NULL, PRIMARY KEY(user_id,platform,account_key));
CREATE TABLE IF NOT EXISTS social_friends (
 low_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 high_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 sender_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 status TEXT NOT NULL CHECK(status IN ('pending','accepted')),
 created_at INTEGER NOT NULL, PRIMARY KEY(low_id,high_id), CHECK(low_id<>high_id));
CREATE TABLE IF NOT EXISTS social_duels (
 id TEXT PRIMARY KEY, sender_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 target_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, difficulty INTEGER NOT NULL,
 duration_minutes INTEGER NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL, starts_at INTEGER, ends_at INTEGER, problem TEXT,
 sender_handle TEXT, target_handle TEXT, sender_ac INTEGER, target_ac INTEGER,
 winner_id TEXT, checked_at INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS social_duel_locks (
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 duel_id TEXT NOT NULL REFERENCES social_duels(id) ON DELETE CASCADE);
'''

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Profile(Strict):
    display_name: str = Field(default='', max_length=40)
    bio: str = Field(default='', max_length=500)
    public_profile: bool = False
    leaderboard: bool = False

class AccountClaim(Strict):
    platform: str = Field(pattern='^(codeforces|atcoder|luogu|nowcoder)$')
    handle: str = Field(min_length=1, max_length=150)

class FriendRequest(Strict):
    user_id: str = Field(min_length=1, max_length=64)

class DuelRequest(FriendRequest):
    difficulty: int = Field(ge=800, le=3500, strict=True)
    duration_minutes: int = Field(default=60, ge=5, le=180, strict=True)
    published_after: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')

    @field_validator('published_after')
    @classmethod
    def publication_floor(cls, value):
        try:
            chosen = date.fromisoformat(value)
        except ValueError:
            raise ValueError('请选择有效的题目最早发布日期') from None
        if chosen < date(2000, 1, 1) or chosen > datetime.now(TZ).date():
            raise ValueError('题目最早发布日期应在 2000-01-01 至今天之间')
        return value

class Action(Strict):
    action: str = Field(pattern='^(accept|decline|cancel)$')


def official_history(handle, since=None):
    """Bounded, fixed-origin public API. Complete history is required at start.

    During a duel only pages back through its start are necessary. Exhausting
    the safety bound is an error, never silently treated as a complete scan.
    """
    session = requests.Session()
    session.headers['User-Agent'] = requests.utils.default_user_agent()
    result = []
    try:
        for page in range(10):
            pace_official_api()
            with session.get('https://codeforces.com/api/user.status',
                             params={'handle': handle, 'from': page * 5000 + 1, 'count': 5000},
                             timeout=(5, 20), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise HTTPException(502, 'Codeforces 官方 API 暂时不可用，请稍后重试')
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 12 * 1024 * 1024:
                        raise HTTPException(502, 'Codeforces 返回内容超出安全上限，未确认结果')
                    chunks.append(chunk)
                payload = json.loads(b''.join(chunks))
            rows = payload.get('result')
            if payload.get('status') != 'OK' or not isinstance(rows, list):
                raise HTTPException(502, 'Codeforces 官方 API 未返回有效提交记录')
            result.extend(rows)
            if len(rows) < 5000 or (since is not None and rows and min(int(r.get('creationTimeSeconds', 0)) for r in rows) < since):
                return result
        raise HTTPException(409, '提交历史超过 50000 条，无法完整核验；本次对战未开始或结算')
    except (requests.RequestException, ValueError, TypeError, KeyError):
        raise HTTPException(502, '无法核验 Codeforces 官方记录，请稍后重试') from None
    finally:
        session.close()


def official_problemset():
    session = requests.Session()
    try:
        session.headers['User-Agent'] = requests.utils.default_user_agent()
        def read_api(path):
            pace_official_api()
            with session.get('https://codeforces.com/api/' + path, timeout=(5, 20), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise HTTPException(502, '官方 CF 题库或比赛日期不可用，本次对战未开始')
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 12 * 1024 * 1024:
                        raise HTTPException(502, '官方 CF 题库或比赛日期超出安全上限')
                    chunks.append(chunk)
            return json.loads(b''.join(chunks))

        payload = read_api('problemset.problems')
        if payload.get('status') != 'OK' or not isinstance(payload.get('result'), dict):
            raise HTTPException(502, '官方 CF 题库返回无效内容')
        contests = read_api('contest.list?gym=false')
        if contests.get('status') != 'OK' or not isinstance(contests.get('result'), list):
            raise HTTPException(502, '官方 CF 比赛日期返回无效内容，本次对战未开始')
        starts = {row['id']: publication_time(row.get('startTimeSeconds')) for row in contests['result']
                  if type(row.get('id')) is int and row.get('phase') == 'FINISHED'}
        result = []
        for p in payload['result'].get('problems', []):
            cid, index = p.get('contestId'), p.get('index')
            if isinstance(p.get('rating'), int) and isinstance(cid, int) and 0 < cid < 100000 and isinstance(index, str) and re.fullmatch(r'[A-Za-z]\d?', index):
                result.append({'id': f'codeforces:{cid}-{index}', 'platform': 'codeforces', 'problem_id': f'{cid}-{index}',
                    'title': str(p.get('name', index))[:200], 'difficulty': p['rating'], 'tags': p.get('tags') or [],
                    'published_at': starts.get(cid), 'published_at_source': 'codeforces.contest.startTimeSeconds',
                    'url': f'https://codeforces.com/problemset/problem/{cid}/{index}'})
        if not result:
            raise HTTPException(502, '官方 CF 题库未返回可用难度题目')
        return result
    except (requests.RequestException, ValueError, TypeError, KeyError):
        raise HTTPException(502, '无法获取官方 CF 题库，本次对战未开始') from None
    finally:
        session.close()


def problem_id(row):
    p = row.get('problem') or {}
    contest, index = p.get('contestId'), p.get('index')
    return f'codeforces:{contest}-{index}' if isinstance(contest, int) and isinstance(index, str) else ''


def own_record(row, handle):
    author = row.get('author') or {}
    members = author.get('members') or []
    return (len(members) == 1 and str(members[0].get('handle', '')).casefold() == handle.casefold()
            and author.get('participantType') not in {'VIRTUAL', 'MANAGER', 'OUT_OF_COMPETITION'})


class Social:
    def __init__(self, identities, workspaces):
        self.users, self.workspaces = identities, workspaces
        self.check_locks = {}
        self.external_slots = asyncio.Semaphore(2)
        with self.users.connect() as db:
            db.executescript(DDL)
            if 'published_after' not in {r['name'] for r in db.execute('PRAGMA table_info(social_duels)')}:
                db.execute('ALTER TABLE social_duels ADD COLUMN published_after TEXT')
            # Starting is a short local transaction + external fetch; interrupted
            # starts never silently become a running duel after service restart.
            db.execute("UPDATE social_duels SET status='cancelled',error='服务重启，请重新约战' WHERE status='starting'")
            db.execute("DELETE FROM social_duel_locks WHERE duel_id IN (SELECT id FROM social_duels WHERE status NOT IN ('pending','active'))")

    def profile(self, db, uid):
        row = db.execute('SELECT u.id,u.username,p.display_name,p.bio,p.public_profile,p.leaderboard FROM users u LEFT JOIN social_profiles p ON p.user_id=u.id WHERE u.id=? AND u.active=1', (uid,)).fetchone()
        if not row:
            raise HTTPException(404, '用户不存在')
        return {**dict(row), 'display_name': row['display_name'] or row['username'],
                'bio': row['bio'] or '', 'public_profile': bool(row['public_profile']), 'leaderboard': bool(row['leaderboard'])}

    def relationship(self, db, uid, other):
        if uid == other:
            return 'self'
        row = db.execute('SELECT * FROM social_friends WHERE low_id=? AND high_id=?', tuple(sorted((uid, other)))).fetchone()
        if not row:
            return 'none'
        return 'friend' if row['status'] == 'accepted' else ('outgoing' if row['sender_id'] == uid else 'incoming')

    async def claims(self, uid):
        child = await self.workspaces.get(uid)
        # Reuse only a fresh check of the exact current credential. Stored
        # claims remain durable after the short check cache expires.
        checks = child.state.credential_checks
        for result in checks.statuses()['items']:
            if result.get('login_verified') and result.get('platform') in PLATFORMS and result.get('handle'):
                try:
                    await self.claim(uid, AccountClaim(platform=result['platform'], handle=result['handle']), automatic=True)
                except HTTPException as error:
                    if error.status_code != 409:
                        raise
        with self.users.connect() as db:
            rows = db.execute('SELECT * FROM social_accounts WHERE user_id=?', (uid,)).fetchall()
        valid = []
        for row in rows:
            try:
                _, fingerprint = child.state.credential_checks.credential(row['platform'], row['handle'])
            except HTTPException:
                continue
            if secrets.compare_digest(fingerprint, row['credential_hash']):
                valid.append(dict(row))
        return valid

    async def claim(self, uid, value, *, automatic=False):
        child = await self.workspaces.get(uid)
        checks = child.state.credential_checks
        result = next((r for r in checks.statuses()['items'] if r['platform'] == value.platform and r['handle'] == value.handle and r.get('login_verified')), None)
        if not result:
            raise HTTPException(409, '请先在“连接与设置”检测此账号，确认登录身份后再用于榜单或对战')
        _, fingerprint = checks.credential(value.platform, value.handle)
        key = value.handle.casefold() if value.platform == 'codeforces' else value.handle
        with self.users.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if automatic and db.execute('SELECT 1 FROM social_account_optouts WHERE user_id=? AND platform=? AND account_key=?', (uid, value.platform, key)).fetchone():
                return {'ok': False}
            old = db.execute('SELECT user_id,credential_hash,handle FROM social_accounts WHERE platform=? AND account_key=?', (value.platform, key)).fetchone()
            if old and old['user_id'] != uid:
                raise HTTPException(409, '此平台身份已被其他站内用户认领，请联系管理员核实；不能重复参加榜单或对战')
            if automatic and old and secrets.compare_digest(old['credential_hash'], fingerprint) and old['handle'] == value.handle:
                return {'ok': True}
            db.execute('INSERT INTO social_accounts VALUES(?,?,?,?,?,?) ON CONFLICT(platform,account_key) DO UPDATE SET handle=excluded.handle,credential_hash=excluded.credential_hash,verified_at=excluded.verified_at', (value.platform, key, value.handle, uid, fingerprint, int(time.time())))
            if not automatic:
                db.execute('DELETE FROM social_account_optouts WHERE user_id=? AND platform=? AND account_key=?', (uid, value.platform, key))
        return {'ok': True}

    async def solved(self, uid, day):
        child = await self.workspaces.get(uid)
        accounts = await self.claims(uid)
        allowed = {(r['platform'], r['handle'].casefold() if r['platform'] == 'codeforces' else r['handle']) for r in accounts}
        if not allowed:
            return []
        start = int(datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp())
        end = min(start + 86400, int(time.time()) + 1)
        items = {}
        with child.state.database.connect() as db:
            # Match the catalog's (platform,id) primary key only for today's ACs.
            # Crawler history may contain either raw or canonical problem IDs.
            rows = db.execute("""SELECT s.platform,s.account,s.problem_id,s.submitted_at,s.data,
                    c.data AS catalog_data FROM submissions s
                LEFT JOIN problem_catalog c ON c.platform=s.platform AND c.id=CASE
                    WHEN substr(s.problem_id,1,length(s.platform)+1)=s.platform||':' THEN s.problem_id
                    ELSE s.platform||':'||s.problem_id END
                WHERE s.verdict='AC' AND s.submitted_at>=? AND s.submitted_at<?
                ORDER BY s.submitted_at""", (start, end)).fetchall()
            rows = [dict(row) for row in rows]
            # Older workbench versions may have saved verified judge evidence
            # without copying it to submissions. Never count drafts/self-tests,
            # and let a collected official record (including rejudges) win.
            attempts = db.execute("""SELECT a.problem_id,a.handle,a.judge_data,c.data AS catalog_data
                FROM wb_attempts a LEFT JOIN problem_catalog c ON c.id=a.problem_id
                    AND c.platform=substr(a.problem_id,1,instr(a.problem_id,':')-1)
                WHERE a.verdict='AC' AND a.submission_id IS NOT NULL AND a.last_synced>0
                AND a.handle<>'' AND NOT EXISTS (
                    SELECT 1 FROM submissions s WHERE s.id=CAST(a.submission_id AS TEXT)
                    AND s.platform=substr(a.problem_id,1,instr(a.problem_id,':')-1)
                    AND CASE WHEN s.platform='codeforces' THEN lower(s.account)=lower(a.handle)
                             ELSE s.account=a.handle END)""").fetchall()
            for attempt in attempts:
                platform, sep, raw = attempt['problem_id'].partition(':')
                if not sep or platform not in PLATFORMS:
                    continue
                try:
                    judge = json.loads(attempt['judge_data'] or '{}')
                    timestamp = judge.get('creationTimeSeconds') if platform == 'codeforces' else judge.get('submitted_at')
                    if (judge.get('verdict') not in {'OK', 'AC'} or type(timestamp) is not int
                            or not start <= timestamp < end
                            or judge.get('platform', platform) != platform
                            or platform != 'codeforces' and judge.get('source') != 'official_remote_submission'):
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                rows.append({'platform': platform, 'account': attempt['handle'], 'problem_id': raw,
                    'submitted_at': timestamp, 'data': '{}', 'catalog_data': attempt['catalog_data']})
        rows.sort(key=lambda row: row['submitted_at'])
        for r in rows:
            platform, handle = r['platform'], r['account']
            if (platform, handle.casefold() if platform == 'codeforces' else handle) not in allowed:
                continue
            raw = r['problem_id'].removeprefix(platform + ':')
            canonical = platform + ':' + raw
            if canonical in items:
                continue
            data = json.loads(r['data']); p = data.get('problem') or {}
            cat = json.loads(r['catalog_data']) if r['catalog_data'] else {}
            enriched = {**p, 'contest_id': data.get('contest_id') or p.get('contest_id')}
            raw_url = (data.get('raw') or {}).get('problem_url')
            if platform == 'nowcoder' and raw_url:
                enriched['url'] = raw_url if raw_url.startswith('https://') else 'https://ac.nowcoder.com' + raw_url
            url = self.url(platform, raw, enriched)
            items[canonical] = {'id': canonical, 'platform': platform, 'problem_id': raw,
                'title': str(p.get('name') or p.get('title') or cat.get('title') or raw)[:200],
                'url': url, 'difficulty': p.get('rating') or p.get('difficulty') or cat.get('difficulty'),
                'score': data.get('score'), 'difficulty_source': p.get('difficulty_source') or platform,
                'tags': [str(t)[:80] for t in (p.get('tags') or cat.get('tags') or [])][:30],
                'submitted_at': r['submitted_at']}
        return list(items.values())

    @staticmethod
    def url(platform, raw, problem=None):
        if platform == 'codeforces' and re.fullmatch(r'\d+-[A-Za-z]\w*', raw):
            contest, index = raw.split('-', 1)
            if int(contest) >= 100000:
                return f'https://codeforces.com/gym/{contest}/problem/{index}'
            return f'https://codeforces.com/problemset/problem/{contest}/{index}'
        if platform == 'luogu' and re.fullmatch(r'[A-Za-z0-9_]+', raw):
            return 'https://www.luogu.com.cn/problem/' + raw
        if platform == 'atcoder' and re.fullmatch(r'[A-Za-z0-9_-]+', raw):
            try:
                contest = (problem or {}).get('contest_id') or atcoder_contest(raw)
            except AIError:
                return ''
            if re.fullmatch(r'[A-Za-z0-9_-]+', str(contest)):
                return f'https://atcoder.jp/contests/{contest}/tasks/{raw}'
        if platform == 'nowcoder':
            # Use known official-origin problem URLs only.
            url = str((problem or {}).get('url') or '')
            if re.fullmatch(r'https://ac\.nowcoder\.com/acm/(?:problem/\d+|contest/\d+/[A-Za-z0-9]+)', url):
                return url
            if raw.isdigit():
                return 'https://ac.nowcoder.com/acm/problem/' + raw
        return None

    def expire(self, db):
        now = int(time.time())
        db.execute("UPDATE social_duels SET status='expired' WHERE status='pending' AND expires_at<=?", (now,))
        db.execute("DELETE FROM social_duel_locks WHERE duel_id IN (SELECT id FROM social_duels WHERE status IN ('expired','declined','cancelled','finished','draw'))")

    def public_duel(self, db, row, uid):
        if not row or uid not in {row['sender_id'], row['target_id']}:
            raise HTTPException(404, '对战不存在')
        value = dict(row)
        value['problem'] = json.loads(value['problem']) if value['problem'] else None
        for field, key in [('sender', 'sender_id'), ('target', 'target_id')]:
            try: value[field] = self.profile(db, row[key])['display_name']
            except HTTPException: value[field] = '已停用用户'
        value['self_is_sender'] = uid == row['sender_id']
        return value

    async def create_duel(self, uid, value):
        if value.difficulty % 100:
            raise HTTPException(422, 'CF 对战难度必须为 100 的整数倍')
        claims = await self.claims(uid)
        if not any(c['platform'] == 'codeforces' for c in claims):
            raise HTTPException(409, '请先验证并认领一个 Codeforces 身份')
        now, did = int(time.time()), uuid.uuid4().hex
        with self.users.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.expire(db)
            self.profile(db, value.user_id)
            if self.relationship(db, uid, value.user_id) != 'friend':
                raise HTTPException(403, '仅能邀请已互相确认的好友对战')
            if db.execute('SELECT 1 FROM social_duel_locks WHERE user_id=?', (uid,)).fetchone():
                raise HTTPException(409, '你已有待处理或进行中的对战')
            db.execute('INSERT INTO social_duels(id,sender_id,target_id,difficulty,duration_minutes,status,created_at,expires_at,published_after) VALUES(?,?,?,?,?,\'pending\',?,?,?)', (did, uid, value.user_id, value.difficulty, value.duration_minutes, now, now+86400, value.published_after))
            db.execute('INSERT INTO social_duel_locks VALUES(?,?)', (uid, did))
            return self.public_duel(db, db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone(), uid)

    async def accept(self, uid, did):
        with self.users.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.expire(db)
            row = db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone()
            if not row or row['target_id'] != uid:
                raise HTTPException(404, '邀请不存在')
            if row['status'] != 'pending':
                raise HTTPException(409, '邀请已处理或过期')
            if not row['published_after']:
                raise HTTPException(409, '旧邀请未约定题目发布日期，请撤回后选择日期重新邀请')
            sender, target = row['sender_id'], row['target_id']
            self.profile(db, sender); self.profile(db, target)
            if self.relationship(db, sender, target) != 'friend':
                raise HTTPException(409, '好友关系已解除，不能开始对战')
            if db.execute('SELECT 1 FROM social_duel_locks WHERE user_id=?', (target,)).fetchone():
                raise HTTPException(409, '你已有待处理或进行中的对战')
            db.execute('INSERT INTO social_duel_locks VALUES(?,?)', (target, did))
            db.execute("UPDATE social_duels SET status='starting',error=NULL WHERE id=?", (did,))
        try:
            both = [await self.claims(sender), await self.claims(target)]
            # A user can choose their verified CF identity by retaining exactly one
            # social claim. No arbitrary handle supplied by the opponent is used.
            handles = []
            for claims in both:
                cfs = [c for c in claims if c['platform'] == 'codeforces']
                if len(cfs) != 1:
                    raise HTTPException(409, '双方各需且只能保留一个有效的 Codeforces 对战身份')
                handles.append(cfs[0]['handle'])
            if self.external_slots.locked():
                raise HTTPException(429, '服务器正在核验其他对战，请稍后重试')
            histories = []
            async with self.external_slots:
                for handle in handles:
                    histories.append(await asyncio.to_thread(official_history, handle))
                official = await asyncio.to_thread(official_problemset)
            attempted = {problem_id(r) for history in histories for r in history}
            floor = datetime.combine(date.fromisoformat(row['published_after']), datetime.min.time(), TZ).timestamp()
            candidates = {p['id']: p for p in official if p['difficulty'] == row['difficulty']
                and p.get('published_at_source') == 'codeforces.contest.startTimeSeconds'
                and publication_time(p.get('published_at')) is not None and p['published_at'] >= floor}
            children = [await self.workspaces.get(sender), await self.workspaces.get(target)]
            for child in children:
                database = child.state.database
                with database.connect() as db:
                    attempted.update(r[0] for r in db.execute('SELECT problem_id FROM daily_plan_tasks WHERE active=1'))
                attempted.update(database.get('battle_problem_ids', []))
            pool = [p for pid, p in candidates.items() if pid not in attempted]
            if not pool:
                raise HTTPException(409, f'没有同时满足 CF {row["difficulty"]}、{row["published_after"]} 起（北京时间）、双方未尝试且未分配为每日任务的题目；缺少可靠比赛日期的题也会排除，请重新约定条件')
            chosen = secrets.choice(pool)
            # Hold both private DB writer locks until exclusions are validated.
            # AI daily planning can finish while the official API is awaited.
            with ExitStack() as stack:
                connections = [stack.enter_context(c.state.database.connect()) for c in sorted(children, key=lambda c: str(c.state.database.path))]
                for db in connections:
                    db.execute('BEGIN IMMEDIATE')
                for db in connections:
                    if db.execute('SELECT 1 FROM daily_plan_tasks WHERE active=1 AND problem_id=?', (chosen['id'],)).fetchone():
                        raise HTTPException(409, '此题刚被分配为每日任务，本次未开始，请重新接受邀请抽题')
                for db in connections:
                    current = db.execute("SELECT value FROM web_settings WHERE key='battle_problem_ids'").fetchone()
                    excluded = set(json.loads(current[0]) if current else [])
                    excluded.add(chosen['id'])
                    db.execute("INSERT INTO web_settings VALUES('battle_problem_ids',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(sorted(excluded)),))
            now = int(time.time()) + 2  # only submissions strictly after the shared start count
            with self.users.connect() as db:
                db.execute("UPDATE social_duels SET status='active',problem=?,sender_handle=?,target_handle=?,starts_at=?,ends_at=? WHERE id=? AND status='starting'", (json.dumps(chosen, ensure_ascii=False), *handles, now, now+row['duration_minutes']*60, did))
                return self.public_duel(db, db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone(), uid)
        except BaseException:
            with self.users.connect() as db:
                db.execute("UPDATE social_duels SET status='pending',error='开始失败，请检查平台身份、题库和官方 API 后重试' WHERE id=? AND status='starting'", (did,))
                db.execute('DELETE FROM social_duel_locks WHERE user_id=? AND duel_id=?', (target, did))
            raise

    async def check(self, uid, did):
        with self.users.connect() as db:
            self.public_duel(db, db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone(), uid)
        lock = self.check_locks.setdefault(did, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, '正在核验此场对战，请稍后刷新')
        try:
            async with lock:
                with self.users.connect() as db:
                    row = db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone()
                    self.public_duel(db, row, uid)
                    if row['status'] not in {'active'}:
                        return self.public_duel(db, row, uid)
                    active = db.execute('SELECT COUNT(*) FROM users WHERE id IN (?,?) AND active=1', (row['sender_id'], row['target_id'])).fetchone()[0]
                    if active != 2:
                        db.execute("UPDATE social_duels SET status='cancelled',error='参与用户已停用，对战取消' WHERE id=?", (did,))
                        db.execute('DELETE FROM social_duel_locks WHERE duel_id=?', (did,))
                        return self.public_duel(db, db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone(), uid)
                    if row['checked_at'] and time.time() - row['checked_at'] < 20:
                        raise HTTPException(429, '对战官方核验间隔为 20 秒，请稍后重试')
                    db.execute('UPDATE social_duels SET checked_at=? WHERE id=?', (int(time.time()), did))
                pid = json.loads(row['problem'])['id']
                evidence = []
                all_rows, histories = [], []
                if self.external_slots.locked():
                    raise HTTPException(429, '服务器正在核验其他对战，请稍后重试')
                async with self.external_slots:
                    for handle in (row['sender_handle'], row['target_handle']):
                        histories.append(await asyncio.to_thread(official_history, handle, row['starts_at']))
                for handle, history in zip((row['sender_handle'], row['target_handle']), histories):
                    accepted = [r for r in history if problem_id(r) == pid and own_record(r, handle)
                        and r.get('verdict') == 'OK' and isinstance(r.get('creationTimeSeconds'), int)
                        and row['starts_at'] < r['creationTimeSeconds'] <= min(int(time.time()), row['ends_at'])]
                    evidence.append(min((r['creationTimeSeconds'] for r in accepted), default=None))
                    all_rows.append(accepted)
                # Store actual official AC evidence in each isolated history so daily
                # counts/check-ins update through the same trusted submission path.
                from ojcrawler.models import Collection, Submission
                from ojcrawler.storage import Store
                for user, handle, records in zip((row['sender_id'], row['target_id']), (row['sender_handle'], row['target_handle']), all_rows):
                    if not records:
                        continue
                    child = await self.workspaces.get(user)
                    store = Store(child.state.database.path)
                    try:
                        store.save('duel-' + uuid.uuid4().hex, int(time.time()), Collection('codeforces', handle, submissions=[
                            Submission(str(r['id']), pid.split(':', 1)[1], r['creationTimeSeconds'], 'AC',
                                language=r.get('programmingLanguage'), contest_id=str(r['problem']['contestId']),
                                time_ms=r.get('timeConsumedMillis'), memory_bytes=r.get('memoryConsumedBytes'),
                                url=f"https://codeforces.com/contest/{r['problem']['contestId']}/submission/{r['id']}",
                                problem={'title': r['problem'].get('name'), 'difficulty': r['problem'].get('rating'),
                                         'tags': r['problem'].get('tags', []), 'raw': r['problem']},
                                raw=r) for r in records], complete=False), capture_snapshot=False)
                    finally:
                        store.close()
                # A currently TESTING earlier submission could still overtake the
                # first AC. Wait for all pre-AC pending judgements (and a 30s grace).
                status, winner = 'active', None
                now = int(time.time())
                if any(ts is not None for ts in evidence) or now > row['ends_at'] + 30:
                    earliest = min((ts for ts in evidence if ts is not None), default=row['ends_at'])
                    ready = now > min(earliest, row['ends_at']) + 30
                    if ready:
                        for handle, history in zip((row['sender_handle'], row['target_handle']), histories):
                            if any(problem_id(r) == pid and own_record(r, handle)
                                   and row['starts_at'] < int(r.get('creationTimeSeconds', 0)) <= earliest
                                   and r.get('verdict') in {None, 'TESTING'} for r in history):
                                ready = False
                    if ready:
                        if evidence[0] is not None and (evidence[1] is None or evidence[0] < evidence[1]):
                            status, winner = 'finished', row['sender_id']
                        elif evidence[1] is not None and (evidence[0] is None or evidence[1] < evidence[0]):
                            status, winner = 'finished', row['target_id']
                        else:
                            status = 'draw'
                with self.users.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('UPDATE social_duels SET status=?,winner_id=?,sender_ac=?,target_ac=?,error=NULL WHERE id=? AND status=\'active\'', (status, winner, *evidence, did))
                    if status != 'active':
                        db.execute('DELETE FROM social_duel_locks WHERE duel_id=?', (did,))
                    return self.public_duel(db, db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone(), uid)
        finally:
            self.check_locks.pop(did, None)


def register(app, identities, workspaces, require):
    social = Social(identities, workspaces)
    app.state.social = social

    @app.get('/api/social/me')
    async def me(request: Request):
        uid = require(request)['id']
        child = await workspaces.get(uid)
        claims = await social.claims(uid)
        with identities.connect() as db:
            profile = social.profile(db, uid)
        return {'profile': profile, 'accounts': [{'platform': r['platform'], 'handle': r['handle']} for r in claims],
                'bindings': [{'platform': r['platform'], 'handle': r['handle']} for r in child.state.credential_checks.accounts.document().get('accounts', [])]}

    @app.put('/api/social/me')
    def update_profile(payload: Profile, request: Request):
        uid = require(request)['id']
        with identities.connect() as db:
            db.execute('INSERT INTO social_profiles VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name,bio=excluded.bio,public_profile=excluded.public_profile,leaderboard=excluded.leaderboard', (uid, payload.display_name.strip(), payload.bio.strip(), int(payload.public_profile), int(payload.leaderboard)))
        return {'ok': True}

    @app.post('/api/social/accounts')
    async def claim(payload: AccountClaim, request: Request):
        uid = require(request)['id']; identities.limit('social-claim:' + uid, 10, 60)
        return await social.claim(uid, payload)

    @app.delete('/api/social/accounts/{platform}/{handle}')
    def unclaim(platform: str, handle: str, request: Request):
        uid = require(request)['id']
        with identities.connect() as db:
            social.expire(db)
            if db.execute("SELECT 1 FROM social_duel_locks WHERE user_id=?", (uid,)).fetchone():
                raise HTTPException(409, '请先结束或取消当前对战，再解除身份认领')
            key = handle.casefold() if platform == 'codeforces' else handle
            db.execute('DELETE FROM social_accounts WHERE user_id=? AND platform=? AND account_key=?', (uid, platform, key))
            db.execute('INSERT OR IGNORE INTO social_account_optouts VALUES(?,?,?)', (uid, platform, key))
        return {'ok': True}

    @app.get('/api/social/users')
    def search(request: Request, q: str = Query(default='', max_length=40)):
        uid = require(request)['id']
        with identities.connect() as db:
            rows = db.execute("SELECT id FROM users WHERE active=1 AND (username LIKE ? ESCAPE '\\' OR id IN (SELECT user_id FROM social_profiles WHERE display_name LIKE ? ESCAPE '\\')) ORDER BY username LIMIT 30", ('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%',) * 2).fetchall() if q.strip() else []
            return {'items': [{'id': r['id'], 'username': social.profile(db, r['id'])['username'], 'display_name': social.profile(db, r['id'])['display_name'], 'relationship': social.relationship(db, uid, r['id'])} for r in rows]}

    def parse_day(day):
        today = datetime.now(TZ).date()
        try:
            value = datetime.strptime(day, '%Y-%m-%d').date() if day else today
            if value > today or value < today - timedelta(days=366):
                raise ValueError
        except ValueError:
            raise HTTPException(422, '日期须在最近一年内，格式为 YYYY-MM-DD') from None
        return value.isoformat()

    @app.get('/api/social/leaderboard')
    async def leaderboard(request: Request, day: str = Query(default='', max_length=10)):
        uid = require(request)['id']; day = parse_day(day)
        with identities.connect() as db:
            rows = db.execute('SELECT u.id FROM users u JOIN social_profiles p ON p.user_id=u.id WHERE u.active=1 AND p.leaderboard=1').fetchall()
            profiles = {r['id']: social.profile(db, r['id']) for r in rows}
        result = []
        for other, profile in profiles.items():
            items = await social.solved(other, day)
            result.append({'id': other, 'username': profile['username'], 'display_name': profile['display_name'], 'count': len(items), 'self': uid == other})
        result.sort(key=lambda r: (-r['count'], r['username']))
        for i, r in enumerate(result):
            r['rank'] = i + 1 if i == 0 or r['count'] != result[i - 1]['count'] else result[i - 1]['rank']
        return {'day': day, 'items': result, 'rule': '按北京时间当天官方 AC 的题目数排名，包含自主练习、每日任务和对战，以及已同步的虚拟参赛和 Gym；按平台及题号去重，重复 AC 只计一题。部分 Gym/Mashup 不在用户历史 API 中，需补充比赛链接同步；无权限或安全验证时不能保证完整。仅统计已同步且已验证认领的平台身份，公开榜单默认关闭。'}

    @app.get('/api/social/users/{other}')
    async def user_profile(other: str, request: Request, day: str = Query(default='', max_length=10)):
        uid = require(request)['id']; day = parse_day(day)
        with identities.connect() as db:
            profile = social.profile(db, other); relationship = social.relationship(db, uid, other)
            if not profile['public_profile'] and not profile['leaderboard'] and relationship not in {'self', 'friend'}:
                raise HTTPException(403, '该用户未公开主页，可先申请好友')
            if not profile['public_profile'] and relationship not in {'self', 'friend'}:
                profile = {**profile, 'bio': ''}
        return {'profile': profile, 'relationship': relationship, 'day': day, 'items': await social.solved(other, day)}

    @app.get('/api/social/friends')
    def friends(request: Request):
        uid = require(request)['id']
        with identities.connect() as db:
            rows = db.execute('SELECT * FROM social_friends WHERE low_id=? OR high_id=? ORDER BY created_at DESC', (uid, uid)).fetchall()
            result = []
            for r in rows:
                other = r['high_id'] if r['low_id'] == uid else r['low_id']
                try: p = social.profile(db, other)
                except HTTPException: continue
                result.append({'id': other, 'username': p['username'], 'display_name': p['display_name'], 'relationship': social.relationship(db, uid, other)})
        return {'items': result}

    @app.post('/api/social/friends')
    def friend_request(payload: FriendRequest, request: Request):
        uid = require(request)['id']; identities.limit('social-friend:' + uid, 20, 3600)
        if uid == payload.user_id:
            raise HTTPException(422, '不能添加自己')
        with identities.connect() as db:
            db.execute('BEGIN IMMEDIATE'); social.profile(db, payload.user_id)
            low, high = sorted((uid, payload.user_id))
            if db.execute('SELECT 1 FROM social_friends WHERE low_id=? AND high_id=?', (low, high)).fetchone():
                raise HTTPException(409, '好友关系或申请已存在，请直接处理已有申请')
            db.execute("INSERT INTO social_friends VALUES(?,?,?,'pending',?)", (low, high, uid, int(time.time())))
        return {'ok': True}

    @app.post('/api/social/friends/{other}')
    def friend_action(other: str, payload: Action, request: Request):
        uid = require(request)['id']; low, high = sorted((uid, other))
        with identities.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM social_friends WHERE low_id=? AND high_id=?', (low, high)).fetchone()
            if not row: raise HTTPException(404, '申请不存在')
            if payload.action == 'accept':
                if row['status'] != 'pending' or row['sender_id'] == uid:
                    raise HTTPException(403, '只能接受对方发来的待处理申请')
                db.execute("UPDATE social_friends SET status='accepted' WHERE low_id=? AND high_id=?", (low, high))
            else:
                if row['status'] == 'pending' and ((payload.action == 'decline') != (row['sender_id'] != uid)):
                    raise HTTPException(403, '只能拒绝收到的申请或撤回自己发出的申请')
                db.execute('DELETE FROM social_friends WHERE low_id=? AND high_id=?', (low, high))
        return {'ok': True}

    @app.get('/api/social/duels')
    def duels(request: Request):
        uid = require(request)['id']
        with identities.connect() as db:
            social.expire(db)
            rows = db.execute('SELECT * FROM social_duels WHERE sender_id=? OR target_id=? ORDER BY created_at DESC,id DESC LIMIT 50', (uid, uid)).fetchall()
            return {'items': [social.public_duel(db, r, uid) for r in rows], 'now': int(time.time())}

    @app.post('/api/social/duels')
    async def invite(payload: DuelRequest, request: Request):
        uid = require(request)['id']; identities.limit('social-duel:' + uid, 10, 3600)
        return await social.create_duel(uid, payload)

    @app.post('/api/social/duels/{did}/action')
    async def duel_action(did: str, payload: Action, request: Request):
        uid = require(request)['id']; identities.limit('social-duel-action:' + uid, 12, 60)
        if payload.action == 'accept': return await social.accept(uid, did)
        with identities.connect() as db:
            db.execute('BEGIN IMMEDIATE'); social.expire(db)
            row = db.execute('SELECT * FROM social_duels WHERE id=?', (did,)).fetchone(); social.public_duel(db, row, uid)
            if row['status'] != 'pending': raise HTTPException(409, '仅待处理邀请可取消或拒绝，进行中的对战需官方核验结束')
            if (payload.action == 'cancel') != (row['sender_id'] == uid): raise HTTPException(403, '只能撤回自己邀请或拒绝对方邀请')
            db.execute('UPDATE social_duels SET status=? WHERE id=?', ('cancelled' if payload.action == 'cancel' else 'declined', did))
            db.execute('DELETE FROM social_duel_locks WHERE duel_id=?', (did,))
        return {'ok': True}

    @app.post('/api/social/duels/{did}/check')
    async def check(did: str, request: Request):
        uid = require(request)['id']; identities.limit('social-duel-check:' + uid, 4, 60)
        return await social.check(uid, did)
