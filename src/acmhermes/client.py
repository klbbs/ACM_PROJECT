"""Small Hermes-facing SDK; supply an already authenticated httpx.Client.

It does not store passwords, cookies or API keys, follow result URLs, execute
report content, or retry a paid draw request. Normal ACM Lab login/CSRF applies.
"""
import json
from pathlib import Path
import re
import time
import uuid


class HermesClient:
    def __init__(self, client):
        self.client = client

    def _json(self, method, path, **kwargs):
        response = self.client.request(method,path,follow_redirects=False,**kwargs)
        response.raise_for_status()
        return response.json()

    def draw(self, score_range, topic=None, *, request_key):
        """Keep request_key on the Hermes side to recover an uncertain response."""
        from .models import Draw
        body=Draw(score_range=score_range,topic=topic).model_dump(exclude_none=True)
        if not re.fullmatch(r'[a-f0-9]{32}',request_key):
            raise ValueError('request_key must be a UUID hex string')
        return self._json('POST','/api/hermes/draw',json=body,
                          headers={'Idempotency-Key':request_key})

    def recover(self, request_key):
        if not re.fullmatch(r'[a-f0-9]{32}',request_key):
            raise ValueError('invalid request key')
        return self._json('GET','/api/hermes/requests/'+request_key)

    def wait(self, job_id, *, timeout=660, interval=3):
        if not re.fullmatch(r'[a-f0-9]{32}',job_id):
            raise ValueError('invalid job id')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            job=self._json('GET','/api/hermes/draws/'+job_id)
            if job['status'] not in {'queued','running'}:
                return job
            time.sleep(max(1,interval))
        raise TimeoutError('Hermes job still pending; recover by request_key, do not create a new draw')

    def download(self, job, output):
        jid=job.get('id','')
        if not re.fullmatch(r'[a-f0-9]{32}',jid) or job.get('status')!='completed':
            raise ValueError('only completed jobs can be downloaded')
        directory=Path(output)/jid
        directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        report=directory/'result.json'
        report.write_text(json.dumps(job['result'],ensure_ascii=False,indent=2),encoding='utf-8')
        report.chmod(0o600)
        for i,_ in enumerate(job['result'].get('screenshots',[])):
            # Construct only a same-service authenticated route, never follow
            # an upstream/model-supplied URL or filename from the response.
            response=self.client.get(f'/api/hermes/draws/{jid}/screenshots/{i}',follow_redirects=False)
            response.raise_for_status()
            if not response.content.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('invalid screenshot response')
            path=directory/f'{i+1}.png'
            path.write_bytes(response.content);path.chmod(0o600)
        return directory

    @staticmethod
    def request_key():
        return uuid.uuid4().hex
