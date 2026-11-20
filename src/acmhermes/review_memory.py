"""Workspace-local candidate leases, verified reading reviews and safe progress.

Not delivery memory: a rejected candidate is never marked solved or delivered.
Changing statement, model/configuration, requested range, topic or policy makes
the prior decision inapplicable. No provider body or credentials are stored.
"""
import hashlib
import json
import time

from .models import AssessedChoice
from .rating_review import decision

DDL = '''CREATE TABLE IF NOT EXISTS hermes_candidate_claims (
 problem_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS hermes_candidate_reviews (
 problem_id TEXT NOT NULL, statement_hash TEXT NOT NULL, context_hash TEXT NOT NULL,
 data TEXT NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(problem_id,statement_hash,context_hash));
CREATE INDEX IF NOT EXISTS hermes_reviews_context ON hermes_candidate_reviews(context_hash);
CREATE TABLE IF NOT EXISTS hermes_selection_progress (
 job_id TEXT PRIMARY KEY, data TEXT NOT NULL);'''


def context_hash(fingerprint, selection, request, topic):
    from acmweb.prompts import PROMPT_VERSION
    from .base_filter import VERSION
    return hashlib.sha256(json.dumps([fingerprint, selection['channel'], selection['model'],
        request.score_range, topic, PROMPT_VERSION, VERSION],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class ReviewMemory:
    def __init__(self, database):
        self.db = database
        with database.connect() as c: c.executescript(DDL)

    @staticmethod
    def clean(c):
        c.execute('''DELETE FROM hermes_candidate_claims WHERE expires_at<=? OR job_id IN
            (SELECT id FROM web_jobs WHERE status NOT IN ('queued','running'))''',(int(time.time()),))

    def claimed(self, owner=None):
        with self.db.connect() as c:
            return {r[0] for r in c.execute('''SELECT problem_id FROM hermes_candidate_claims WHERE job_id<>?
                AND expires_at>? AND job_id NOT IN(SELECT id FROM web_jobs WHERE status NOT IN ('queued','running'))''',
                (owner or '',int(time.time())))}

    def claim(self, pid, owner, seconds):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.clean(c)
            if c.execute('SELECT 1 FROM hermes_drawn_problems WHERE problem_id=?',(pid,)).fetchone(): return False
            c.execute('INSERT OR IGNORE INTO hermes_candidate_claims VALUES(?,?,?)',(pid,owner,int(time.time())+seconds+60))
            return c.execute('SELECT job_id FROM hermes_candidate_claims WHERE problem_id=?',(pid,)).fetchone()[0] == owner

    def release(self, owner):
        with self.db.connect() as c: c.execute('DELETE FROM hermes_candidate_claims WHERE job_id=?',(owner,))

    def save(self, pid, fingerprint, context, choice):
        value = AssessedChoice.model_validate(choice).model_dump()
        if value['problem_id'] != pid: raise ValueError('review problem mismatch')
        with self.db.connect() as c:
            c.execute('INSERT OR REPLACE INTO hermes_candidate_reviews VALUES(?,?,?,?,?)',
                (pid,fingerprint,context,json.dumps(value,ensure_ascii=False),int(time.time())))

    def get(self, pid, fingerprint, context):
        with self.db.connect() as c:
            row=c.execute('SELECT data,updated_at FROM hermes_candidate_reviews WHERE problem_id=? AND statement_hash=? AND context_hash=?',
                (pid,fingerprint,context)).fetchone()
        if row is None: return None
        value=AssessedChoice.model_validate_json(row['data']).model_dump()
        # Unknown confidence is a temporary lack of evidence, never a permanent
        # problem ban. Re-evaluate after a day or any material/context change.
        if value['difficulty_assessment']['confidence']=='unknown' and time.time()-row['updated_at']>=86400: return None
        return value

    def excluded(self, context, low, high):
        from acmweb.selection_reports import _statement_material
        from acmweb.codex_bridge import AIError
        with self.db.connect() as c:
            rows=c.execute('''SELECT r.problem_id,r.statement_hash,p.data FROM hermes_candidate_reviews r
                JOIN wb_problems p ON p.id=r.problem_id WHERE r.context_hash=?''',(context,)).fetchall()
        result=set()
        for row in rows:
            try:
                _, fingerprint=_statement_material(json.loads(row['data']),row['problem_id'])
                if fingerprint != row['statement_hash']: continue
                choice=self.get(row['problem_id'],fingerprint,context)
                if choice and (not choice['suitable'] or decision(choice['difficulty_assessment'],low,high)!='accepted'):
                    result.add(row['problem_id'])
            except (AIError,ValueError,TypeError): continue
        return result

    def recent_outcomes(self, context, low, high):
        """Recent definite reviews inform platform sampling for the same request."""
        from acmweb.codex_bridge import AIError
        from acmweb.selection_reports import _statement_material

        outcomes = {}
        seen = set()
        with self.db.connect() as c:
            rows = c.execute('''SELECT problem_id,statement_hash,data FROM hermes_candidate_reviews
                WHERE context_hash=? AND updated_at>=?
                ORDER BY updated_at DESC, rowid DESC LIMIT 96''',
                (context, int(time.time()) - 30 * 86400)).fetchall()
            for row in rows:
                problem_id = row['problem_id']
                if problem_id in seen:
                    continue
                seen.add(problem_id)
                try:
                    choice = AssessedChoice.model_validate_json(row['data'])
                    verdict = decision(choice.difficulty_assessment.model_dump(), low, high)
                    if verdict == 'accepted' and choice.suitable:
                        cached = c.execute('SELECT data FROM wb_problems WHERE id=?', (problem_id,)).fetchone()
                        if cached is None or _statement_material(json.loads(cached['data']), problem_id)[1] != row['statement_hash']:
                            continue
                    elif verdict == 'accepted':
                        verdict = 'topic_or_statement_mismatch'
                except (AIError, TypeError, ValueError):
                    continue
                if verdict != 'undetermined':
                    outcomes[problem_id] = verdict
        return outcomes

    def progress(self, jid, value=None):
        if jid is None: return {}
        with self.db.connect() as c:
            if value is not None:
                c.execute('INSERT OR REPLACE INTO hermes_selection_progress VALUES(?,?)',(jid,json.dumps(value,ensure_ascii=False)))
            row=c.execute('SELECT data FROM hermes_selection_progress WHERE job_id=?',(jid,)).fetchone()
        return json.loads(row[0]) if row else {}
