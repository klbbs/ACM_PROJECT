"""Small Hermes-facing SDK; supply an already authenticated httpx.Client.

It does not store passwords, cookies or API keys, follow result URLs, execute
report content, or retry a paid draw request. Normal ACM Lab login/CSRF applies.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid


class HermesClient:
    def __init__(self, client):
        self.client = client

    def _json(self, method, path, **kwargs):
        response = self.client.request(method,path,follow_redirects=False,**kwargs)
        response.raise_for_status()
        return response.json()

    def draw(self, score_range, topic=None, *, request_key, exclude_ids=None, memory_path=None):
        return self._draw('/api/hermes/draw',score_range,topic,request_key,exclude_ids,memory_path)

    def draw_image(self,score_range,topic=None,*,request_key,exclude_ids=None,memory_path=None):
        return self._draw('/api/hermes/draw/image',score_range,topic,request_key,exclude_ids,memory_path)

    def _draw(self,path,score_range,topic,request_key,exclude_ids=None,memory_path=None):
        """Keep request_key on the Hermes side to recover an uncertain response."""
        from .models import Draw
        if not re.fullmatch(r'[a-f0-9]{32}',request_key):
            raise ValueError('request_key must be a UUID hex string')
        exclusions=list(exclude_ids or [])
        if memory_path is not None:
            exclusions.extend(self.memory_ids(memory_path))
        exclusions=list(dict.fromkeys(exclusions))
        body=Draw(score_range=score_range,topic=topic,exclude_ids=exclusions).model_dump(exclude_none=True)
        if not exclusions:body.pop('exclude_ids',None)
        return self._json('POST',path,json=body,
                          headers={'Idempotency-Key':request_key})

    def memory(self,*,after=None,limit=500,revision=None):
        """Read one page of this authenticated user's canonical problem memory."""
        if type(limit) is not int or not 1<=limit<=1000:
            raise ValueError('memory limit must be between 1 and 1000')
        params={'limit':limit}
        if after is not None:params['after']=after
        if revision is not None:params['revision']=revision
        return self._json('GET','/api/hermes/problem-memory',params=params)

    @staticmethod
    def _memory_page(document):
        from acmweb.codex_bridge import AIError
        from acmweb.problem_sources import normalize
        if not isinstance(document,dict) or any(
                not isinstance(document.get(key),str) or not 1<=len(document[key])<=200
                for key in ('scope','revision')):
            raise ValueError('invalid problem memory identity')
        items=document.get('items')
        if not isinstance(items,list):raise ValueError('invalid problem memory items')
        checked=[]
        for item in items:
            if not isinstance(item,dict):raise ValueError('invalid problem memory item')
            pid=item.get('problem_id')
            try:
                valid=isinstance(pid,str) and 1<=len(pid)<=200 and normalize(pid)==pid
            except (AIError,AttributeError,TypeError,ValueError):valid=False
            if not valid:raise ValueError('invalid canonical problem ID in memory')
            flags={key:item.get(key) for key in ('attempted','solved','drawn','skipped')}
            if any(type(value) is not bool for value in flags.values()):
                raise ValueError('invalid problem memory flags')
            checked.append({'problem_id':pid,**flags})
        cursor=document.get('next_after')
        if cursor is not None and (not isinstance(cursor,str) or not checked or cursor!=checked[-1]['problem_id']):
            raise ValueError('invalid problem memory cursor')
        return {'scope':document['scope'],'revision':document['revision'],
                'items':checked,'next_after':cursor}

    def sync_memory(self,output_path):
        """Atomically replace an explicitly chosen private cache after all pages succeed.

        A 409 means the server snapshot changed; the previous cache remains intact.
        The caller may retry this read-only sync; paid draws are never retried here.
        """
        items=[];after=None;revision=None;scope=None;seen=set()
        while True:
            page=self._memory_page(self.memory(after=after,revision=revision))
            if scope is not None and (page['scope']!=scope or page['revision']!=revision):
                raise ValueError('problem memory changed during sync; synchronize again')
            scope,revision=page['scope'],page['revision']
            previous=after
            for item in page['items']:
                pid=item['problem_id']
                if pid in seen or (previous is not None and pid<=previous):
                    raise ValueError('problem memory pagination is inconsistent')
                seen.add(pid);items.append(item)
                previous=pid
            after=page['next_after']
            if after is None:break
        document={'scope':scope,'revision':revision,'items':items,'next_after':None}
        path=Path(output_path)
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                    prefix='.'+path.name+'.',delete=False) as stream:
                temporary=Path(stream.name)
                os.fchmod(stream.fileno(),0o600)
                json.dump(document,stream,ensure_ascii=False,indent=2)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return document

    def memory_ids(self,path):
        """Validate cache ownership before sending its IDs in a new paid draw."""
        document=self._memory_page(json.loads(Path(path).read_text(encoding='utf-8')))
        if document['next_after'] is not None:
            raise ValueError('problem memory cache is incomplete; synchronize again')
        current=self._memory_page(self.memory(limit=1))
        if document['scope']!=current['scope']:
            raise ValueError('problem memory belongs to another user workspace; synchronize again')
        ids=list(dict.fromkeys(item['problem_id'] for item in document['items']
            if any(item[key] for key in ('attempted','solved','drawn','skipped'))))
        if len(ids)>10000:
            raise ValueError('local exclusions exceed 10000 IDs; use server-side memory or explicit exclusions')
        return ids

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
