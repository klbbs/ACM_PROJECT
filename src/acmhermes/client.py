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
        return self._draw('/api/hermes/draw',score_range,topic,request_key)

    def draw_image(self,score_range,topic=None,*,request_key):
        return self._draw('/api/hermes/draw/image',score_range,topic,request_key)

    def _draw(self,path,score_range,topic,request_key):
        """Keep request_key on the Hermes side to recover an uncertain response."""
        from .models import Draw
        body=Draw(score_range=score_range,topic=topic).model_dump(exclude_none=True)
        if not re.fullmatch(r'[a-f0-9]{32}',request_key):
            raise ValueError('request_key must be a UUID hex string')
        return self._json('POST',path,json=body,
                          headers={'Idempotency-Key':request_key})

    def recover(self, request_key):
        if not re.fullmatch(r'[a-f0-9]{32}',request_key):
            raise ValueError('invalid request key')
        return self._json('GET','/api/hermes/requests/'+request_key)

    @staticmethod
    def _id(value):
        if not re.fullmatch(r'[a-f0-9]{32}',value):
            raise ValueError('invalid Hermes identifier')
        return value

    def status(self,job_id):
        return self._json('GET','/api/hermes/draws/'+self._id(job_id))

    def list_jobs(self,*,status=None,limit=20,offset=0):
        params={'limit':limit,'offset':offset}
        if status:params['status']=status
        return self._json('GET','/api/hermes/draws',params=params)

    def readiness(self):
        return self._json('GET','/api/hermes/readiness')

    def candidates(self,score_range,topic=None):
        from .models import Draw
        request=Draw(score_range=score_range,topic=topic)
        params={'minimum':request.score_range[0],'maximum':request.score_range[1]}
        if topic:params['topic']=topic
        return self._json('GET','/api/hermes/candidates',params=params)

    def cancel(self,job_id):
        return self._json('POST','/api/hermes/draws/'+self._id(job_id)+'/cancel',json={})

    def cancel_request(self,request_key):
        return self._json('POST','/api/hermes/requests/'+self._id(request_key)+'/cancel',json={})

    def stop_all(self):
        return self._json('POST','/api/hermes/stop',json={})

    def wait_stopped(self,job_id,*,timeout=30,interval=1):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            job=self.status(job_id)
            control=job.get('control') or {}
            if control.get('stop_state')=='stopped' or (
                    not control.get('cancel_requested') and job['status'] not in {'queued','running'}):
                return job
            time.sleep(max(.1,interval))
        raise TimeoutError('Stop requested but local cleanup is still pending; query status again')

    def wait(self, job_id, *, timeout=660, interval=3):
        if not re.fullmatch(r'[a-f0-9]{32}',job_id):
            raise ValueError('invalid job id')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            job=self.status(job_id)
            if job['status'] not in {'queued','running'}:
                return job
            time.sleep(max(1,interval))
        raise TimeoutError('Hermes job still pending; recover by request_key, do not create a new draw')

    def download(self, job, output, *, include_images=True):
        jid=job.get('id','')
        if not re.fullmatch(r'[a-f0-9]{32}',jid) or job.get('status')!='completed':
            raise ValueError('only completed jobs can be downloaded')
        directory=Path(output)/jid
        directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        report=directory/'result.json'
        report.write_text(json.dumps(job['result'],ensure_ascii=False,indent=2),encoding='utf-8')
        report.chmod(0o600)
        for name,content in (('problem.txt',self.explain(job)),('solution.txt',self.solution(job))):
            path=directory/name
            path.write_text(content,encoding='utf-8');path.chmod(0o600)
        if include_images and job['result'].get('format')=='image':
            for i,_ in enumerate(job['result'].get('screenshots',[])):
                response=self.client.get(f'/api/hermes/draws/{jid}/screenshots/{i}',follow_redirects=False)
                response.raise_for_status()
                if not response.content.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('invalid screenshot response')
                path=directory/f'{i+1}.png'
                path.write_bytes(response.content);path.chmod(0o600)
        return directory

    @staticmethod
    def explain(job):
        """Text Hermes can tell the user directly, without solution spoilers."""
        result=job.get('result') or {}
        problem=result.get('problem_explanation')
        if not problem:
            return '此历史任务尚无中文题目说明，请阅读原题；不会自动重新调用 AI。\n'
        lines=[problem['title'], '', '题目要你做什么：', problem['description'],
               '', '输入：',problem['input_format'], '', '输出：',problem['output_format'],
               '', '约束：', *('- '+s for s in problem['constraints'])]
        for i,sample in enumerate(problem.get('samples',[]),start=1):
            lines.extend(['',f'样例 {i} 输入：',sample['input'],f'样例 {i} 输出：',sample['output']])
        if problem.get('notes'):
            lines.extend(['','注意事项：',*('- '+s for s in problem['notes'])])
        lines.extend(['','原题：'+problem['original_url'],'AI 阅读说明可能有误，请结合原题核对。'])
        return '\n'.join(lines)+'\n'

    @staticmethod
    def solution(job):
        report=(job.get('result') or {}).get('report') or {}
        lines=['AI 解题报告（含剧透，未运行代码）']
        for key,title in (('summary','概述'),('topic_fit','题型适配'),('constraints','约束'),
                ('approach','解题思路'),('correctness','正确性论证'),('complexity','复杂度'),
                ('sample_walkthrough','样例解释'),('pitfalls','实现注意事项'),
                ('alternative_approaches','其他方法'),('limitations','分析限制')):
            value=report.get(key)
            if value:
                lines.extend(['',title+'：',*('- '+s for s in value)] if isinstance(value,list)
                             else ['',title+'：',str(value)])
        return '\n'.join(lines)+'\n'

    @staticmethod
    def request_key():
        return uuid.uuid4().hex
