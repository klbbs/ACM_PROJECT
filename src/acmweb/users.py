"""Identity database. Never stores plaintext passwords, session IDs or invitations."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from fastapi import HTTPException

HASH_SLOTS = threading.BoundedSemaphore(2)
AUTH_SLOTS = threading.BoundedSemaphore(2)


@contextmanager
def authentication_slot():
    # Bound expensive work while it is running, rather than locking every
    # visitor out for a whole window after someone fills a global counter.
    if not AUTH_SLOTS.acquire(blocking=False):
        raise HTTPException(503, '认证服务暂时繁忙，请稍后重试', headers={'Retry-After':'1'})
    try:
        yield
    finally:
        AUTH_SLOTS.release()


@contextmanager
def instance_lock(path):
    """A tenant runtime must have only one serving process."""
    import fcntl
    fd = os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('多人服务正在运行，请先停止服务；同一数据目录仅支持一个进程') from None
        yield
    finally:
        os.close(fd)


def digest(value):
    try:
        return hashlib.sha256(value.encode()).hexdigest()
    except UnicodeEncodeError:
        raise HTTPException(422,'输入包含无效字符') from None


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    try:
        encoded = password.encode()
    except UnicodeEncodeError:
        raise HTTPException(422,'密码包含无效字符') from None
    with HASH_SLOTS:
        key = hashlib.scrypt(encoded, salt=bytes.fromhex(salt), n=2**17, r=8, p=1, maxmem=256*1024*1024)
    return f'scrypt$131072$8$1${salt}${key.hex()}'


def verify_password(password, encoded):
    salt = encoded.split('$')[4]
    return hmac.compare_digest(password_hash(password, salt), encoded)


def csrf_token(token):
    return hmac.new(token.encode(), b'acm-csrf', hashlib.sha256).hexdigest()


class Users:
    def __init__(self, path):
        self.path = path
        fd = os.open(path,os.O_WRONLY|os.O_CREAT,0o600)
        os.close(fd)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('admin','member')), active INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS social_profiles (
              user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              display_name TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '',
              public_profile INTEGER NOT NULL DEFAULT 0, leaderboard INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions (
              hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
              created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, seen_at INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS invitations (
              hash TEXT PRIMARY KEY, created_by TEXT NOT NULL REFERENCES users(id),
              expires_at INTEGER NOT NULL, used_by TEXT REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS shared_entries (
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
              body TEXT NOT NULL, visibility TEXT NOT NULL CHECK(visibility IN ('private','shared')),
              version INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY, actor TEXT, event TEXT NOT NULL, target TEXT, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rate_limits (
              key TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL,
              expires_at INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS identity_migrations (
              name TEXT PRIMARY KEY, completed_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS administrator_requests (
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
              reason TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
              created_at INTEGER NOT NULL, reviewed_at INTEGER,
              reviewed_by TEXT REFERENCES users(id), review_note TEXT NOT NULL DEFAULT '');
            CREATE UNIQUE INDEX IF NOT EXISTS administrator_pending_user
              ON administrator_requests(user_id) WHERE status='pending';
            PRAGMA user_version=1;
            CREATE TABLE IF NOT EXISTS online_presence (
              user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              seen_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS site_settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
        path.chmod(0o600)
        # Unknown users still perform the same expensive password verification.
        self.dummy_hash = password_hash(secrets.token_urlsafe(32))

    def normalize_administrator(self):
        """Compatibility entrypoint: existing identities are never rewritten.

        Old releases applied a deployment-specific rename/cleanup migration.
        Startup and offline recovery must now preserve every username, role,
        password, session and workspace, regardless of migration markers.
        Account changes require an explicit authenticated administration action.
        """
        return None

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def audit(self, event, actor=None, target=None, db=None):
        if db is None:
            with self.connect() as connection:
                self.audit(event,actor,target,connection)
        else:
            db.execute('INSERT INTO audit(actor,event,target,created_at) VALUES(?,?,?,?)', (actor,event,target,int(time.time())))

    def limit(self, key, maximum, seconds):
        now = int(time.time())
        window = now // seconds
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM rate_limits WHERE expires_at < ?', (now,))
            row = db.execute('SELECT window,count FROM rate_limits WHERE key=?',(key,)).fetchone()
            count = row['count']+1 if row and row['window']==window else 1
            db.execute('INSERT INTO rate_limits VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET window=excluded.window,count=excluded.count,expires_at=excluded.expires_at',(key,window,count,now+seconds))
        if count > maximum:
            raise HTTPException(429, '请求过于频繁，请稍后重试', headers={'Retry-After': str(seconds-now%seconds)})

    def count(self):
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM users').fetchone()[0]

    def create(self, username, password, *, setup=False, invitation='', max_users=100, display_name=''):
        uid, now = uuid.uuid4().hex, int(time.time())
        invitation_digest = digest(invitation)

        def validate(db):
            count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            if setup and count:
                raise HTTPException(409, '管理员已初始化')
            if not setup:
                row = db.execute('SELECT * FROM invitations WHERE hash=? AND used_by IS NULL AND expires_at>?',(invitation_digest,int(time.time()))).fetchone()
                if not row:
                    raise HTTPException(400, '邀请码无效或已过期')
                if username.lower() == 'admin':
                    raise HTTPException(409, '用户名不可用')
            if count >= max_users:
                raise HTTPException(409, '已达到本站用户上限')
            if db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
                raise HTTPException(409, '用户名不可用')

        # Reject invalid invitations without doing scrypt or disclosing whether
        # a particular account exists/is reserved. Do not hold a write lock
        # while hashing. Recheck under the final write transaction for races.
        with self.connect() as db:
            validate(db)
        with authentication_slot():
            hashed = password_hash(password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            validate(db)
            try:
                db.execute('INSERT INTO users VALUES(?,?,?,?,1,?)',(uid,username,hashed,'admin' if setup else 'member',now))
            except sqlite3.IntegrityError:
                raise HTTPException(409, '用户名不可用') from None
            if not setup:
                db.execute('UPDATE invitations SET used_by=? WHERE hash=?',(uid,invitation_digest))
            if setup and display_name:
                # Save the optional initial nickname in the same transaction
                # as the first identity; public sharing remains disabled.
                db.execute('INSERT INTO social_profiles(user_id,display_name) VALUES(?,?)',
                           (uid, display_name.strip()))
            self.audit('user.created',uid,uid,db)
        return uid

    def authenticate(self, username, password):
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        with authentication_slot():
            valid = verify_password(password, row['password_hash'] if row else self.dummy_hash)
        if not row or not valid or not row['active']:
            self.audit('login.failed')
            raise HTTPException(401, '用户名或密码不正确')
        return dict(row)

    def session(self, uid):
        token, now = secrets.token_urlsafe(32), int(time.time())
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires_at<=? OR seen_at<=?',(now,now-7200))
            # Keep at most five sessions per user.
            db.execute('DELETE FROM sessions WHERE hash IN (SELECT hash FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 4)',(uid,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(digest(token),uid,now,now+86400,now))
            self.audit('login.success',uid,None,db)
        return token

    def resolve(self, token):
        if not token or len(token)>200:
            return None
        now = int(time.time())
        with self.connect() as db:
            row = db.execute('SELECT u.id,u.username,u.role FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.hash=? AND u.active=1 AND s.expires_at>? AND s.seen_at>?',(digest(token),now,now-7200)).fetchone()
            if row:
                db.execute('UPDATE sessions SET seen_at=? WHERE hash=? AND seen_at<?',(now,digest(token),now-60))
        return dict(row) if row else None
