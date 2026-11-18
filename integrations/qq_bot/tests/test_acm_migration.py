import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

REPO=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(REPO/'src/acm'),str(REPO/'src/qq')]
from acm_connection import Connection, ConnectionUnavailable, load_connection, canonical_origin


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_missing_config_does_not_fall_back_to_expired_server(self):
        with self.assertRaises(ConnectionUnavailable) as ctx:
            load_connection(self.root/'missing.json').require_ready()
        self.assertEqual(ctx.exception.state,'waiting_server')

    def test_pending_state_never_connects_to_target(self):
        p=self.root/'server.json';p.write_text(json.dumps({'schema':1,'state':'pending','target':'https://new.example'}))
        with self.assertRaises(ConnectionUnavailable):load_connection(p).require_ready()

    def test_invalid_origins_rejected(self):
        for value in ['http://new.example','https://user:password@new.example','https://new.example/x','https://new.example?a=1','https://new.example#secret','https://new.example:bad','https://new.example\n']:
            with self.subTest(value=value),self.assertRaises(ValueError):canonical_origin(value)

    def test_cookie_bound_to_exact_https_origin(self):
        p=self.root/'session.json'
        connection=Connection('https://new.example',True)
        for data in [{'cookie':'test'},{'cookie':'test','origin':'https://old.example'},{'cookie':'test','origin':'http://new.example'}]:
            p.write_text(json.dumps(data))
            with self.assertRaises(ConnectionUnavailable):connection.cookie(p)
        p.write_text(json.dumps({'cookie':'test','origin':'https://NEW.example:443/'}))
        self.assertEqual(connection.cookie(p),'test')

    def test_pending_cli_never_uses_network(self):
        import httpx,acm_client
        def reject(request):raise AssertionError('Network used while deployment pending')
        with patch.object(acm_client,'load_connection',return_value=Connection()):
            result=acm_client.run({'operation':'status'},transport=httpx.MockTransport(reject))
        self.assertEqual(result['state'],'waiting_server')

    def test_mismatched_cookie_never_reaches_new_site(self):
        import httpx,acm_client
        (self.root/'session.json').write_text(json.dumps({'cookie':'old-private-cookie','origin':'https://old.example'}))
        def reject(request):raise AssertionError('Old cookie must not be transmitted')
        with patch.object(acm_client,'ROOT',self.root),patch.object(acm_client,'load_connection',return_value=Connection('https://new.example',True)):
            result=acm_client.run({'operation':'status'},transport=httpx.MockTransport(reject))
        self.assertEqual(result['state'],'session_origin_mismatch')

    def test_activation_probes_only_readonly_paths_without_cookie(self):
        import httpx
        spec=importlib.util.spec_from_file_location('server_script',REPO/'scripts/server.py')
        server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
        paths=[]
        def handle(request):
            self.assertEqual(request.method,'GET');self.assertNotIn('cookie',request.headers)
            paths.append(request.url.path)
            return httpx.Response(200,json={'status':'ok'} if request.url.path=='/api/health' else {'authenticated':False})
        server.probe('https://new.example',httpx.MockTransport(handle))
        self.assertEqual(paths,['/api/health','/api/auth/status'])

    def test_practice_waiting_does_not_block_other_qq_modules(self):
        from practice_backend import ACM
        from practice_core import PracticeError
        api=ACM(self.root,connection=Connection())
        with self.assertRaises(PracticeError):api.client()
        import qq_delivery_guard
        self.assertTrue(qq_delivery_guard.Policy().enabled)

    def test_old_paid_request_is_not_replayed_on_new_site(self):
        from practice_backend import ACM
        from practice_core import PracticeError
        api=ACM(self.root,connection=Connection('https://new.example',True))
        key='a'*32
        with api.memory.db() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',('https://old.example',key,'scope','{}','[]','{}','posted'))
        with self.assertRaisesRegex(PracticeError,'旧服务器') as ctx:
            asyncio.run(api.draw(key,1200,None))
        self.assertTrue(ctx.exception.definitive)

    def test_old_round_recovery_releases_slot_without_replaying_or_rerolling(self):
        import httpx
        from practice_backend import ACM, terminal
        from practice_core import Game, Store, PracticeError
        requests=[]
        def reject(request):
            requests.append(request)
            raise AssertionError('An old round must not contact either server')
        api=ACM(self.root,connection=Connection('https://new.example',True),
                transport=httpx.MockTransport(reject))
        store=Store(self.root)
        old,_=store.start('chat',1800,None,owner='owner')
        with api.memory.db() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',
                ('https://old.example',old['id'],'scope','{}','[]','{}','posted'))
        game=Game(store,api,None)
        with self.assertRaisesRegex(PracticeError,'旧服务器'):
            asyncio.run(game.handle('chat','recover','/roll 1800',user='owner'))
        self.assertEqual(requests,[])
        self.assertEqual(store.row('chat')['status'],'error')
        with store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rounds').fetchone()[0],1)
        with api.memory.db() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT origin,request_key,state FROM requests')],
                [('https://old.example',old['id'],'posted')])
        # A later explicit /roll may use a fresh key; recovery itself must not.
        with patch.object(api,'ready',new=AsyncMock()),patch.object(api,'draw',
                new=AsyncMock(side_effect=terminal('test rejection'))) as draw:
            with self.assertRaisesRegex(PracticeError,'test rejection'):
                asyncio.run(game.handle('chat','new-roll','/roll 1800',user='owner'))
            self.assertNotEqual(draw.call_args.args[0],old['id'])

    def test_codex_login_checked_before_a_round_is_reserved(self):
        import httpx
        from practice_backend import ACM
        from practice_core import Game, Store, PracticeError
        session=self.root/'session.json'
        session.write_text(json.dumps({'cookie':'test','origin':'https://new.example'}))
        for index,(channel,connected,kind,allowed) in enumerate([
                ('codex',False,None,False),('codex',True,'apiKey',False),
                ('codex',True,'chatgpt',True),('api',False,None,True)]):
            with self.subTest(channel=channel,connected=connected,kind=kind):
                paths=[]
                def handle(request):
                    self.assertEqual(request.method,'GET')
                    paths.append(request.url.path)
                    values={
                        '/api/auth/status':{'authenticated':True,'csrf_token':'csrf'},
                        '/api/hermes/capabilities':{'enabled':True,'output_formats':['image'],
                            'selection_strategy':'system_platform_balanced_random_without_replacement',
                            'exclude_ids_limit':10000,'endpoints':{'problem_memory':'/api/hermes/problem-memory'}},
                        '/api/hermes/readiness':{'ai_ready':True},
                        '/api/ai/settings':{'channel':channel},
                        '/api/ai/account':{'connected':connected,'type':kind},
                    }
                    self.assertIn(request.url.path,values)
                    return httpx.Response(200,json=values[request.url.path])
                api=ACM(self.root,session=session,connection=Connection('https://new.example',True),
                        transport=httpx.MockTransport(handle))
                if allowed:
                    asyncio.run(api.ready())
                else:
                    store=Store(self.root)
                    with self.assertRaisesRegex(PracticeError,'申请官方登录设备码'):
                        asyncio.run(Game(store,api,None).handle('chat','auth-check-'+str(index),'/roll 1800'))
                    with store.db() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM rounds').fetchone()[0],0)
                self.assertNotIn('/api/hermes/draw/image',paths)
                self.assertEqual('/api/ai/account' in paths,channel=='codex')

    def test_known_codex_auth_failure_is_actionable_without_exposing_other_errors(self):
        import httpx
        from practice_backend import ACM
        from practice_core import PracticeError
        session=self.root/'session.json'
        session.write_text(json.dumps({'cookie':'test','origin':'https://new.example'}))
        for error,code,expected in [
                ('官方账号认证已失效，请重新登录后重试',None,'申请官方登录设备码'),
                ('upstream private diagnostic','ai_auth_required','申请官方登录设备码'),
                ('upstream private diagnostic','hermes_candidates_exhausted','候选已用尽'),
                ('upstream private diagnostic','hermes_review_budget_exhausted','复核额度已用尽'),
                ('upstream private diagnostic','hermes_report_budget_exhausted','题解生成额度已用尽'),
                ('upstream private diagnostic','hermes_deadline_exceeded','服务端时限'),
                ('upstream private diagnostic','hermes_cancelled','已取消'),
                ('upstream private diagnostic','hermes_generation_failed','题目生成失败'),
                ('upstream private diagnostic','ai_timeout','AI 分析超时'),
                ('upstream private diagnostic','unknown_provider_error','ACM 抽题未完成'),
                ('upstream private diagnostic',None,'ACM 抽题未完成')]:
            with self.subTest(error=error):
                def handle(request):
                    self.assertEqual(request.method,'GET')
                    if request.url.path=='/api/auth/status':
                        return httpx.Response(200,json={'authenticated':True,'csrf_token':'csrf'})
                    self.assertEqual(request.url.path,'/api/hermes/requests/'+'b'*32)
                    return httpx.Response(200,json={'id':'c'*32,'status':'failed','error':error,'error_code':code})
                api=ACM(self.root,session=session,connection=Connection('https://new.example',True),
                        transport=httpx.MockTransport(handle))
                with patch.object(api.memory,'recovery',new=AsyncMock(return_value={})),\
                        self.assertRaisesRegex(PracticeError,expected) as ctx:
                    asyncio.run(api.draw('b'*32,1800,None))
                self.assertTrue(ctx.exception.definitive)
                self.assertNotIn('upstream private diagnostic',str(ctx.exception))

    def test_recovered_draw_waits_for_advertised_budget_without_new_post(self):
        import httpx
        from practice_backend import ACM
        from practice_core import PracticeError
        session=self.root/'session.json'
        session.write_text(json.dumps({'cookie':'test','origin':'https://new.example'}))
        for seconds,pending in [(1200,False),(600,True),(None,True)]:
            with self.subTest(seconds=seconds):
                paths=[]
                def handle(request):
                    self.assertEqual(request.method,'GET');paths.append(request.url.path)
                    if request.url.path=='/api/auth/status':
                        data={'authenticated':True,'csrf_token':'csrf'}
                    elif request.url.path=='/api/hermes/requests/'+'b'*32:
                        data={'id':'c'*32,'status':'running'}
                    elif request.url.path=='/api/hermes/capabilities':
                        data={'enabled':True,'output_formats':['image'],
                            'selection_strategy':'system_platform_balanced_random_without_replacement',
                            'exclude_ids_limit':10000,'endpoints':{'problem_memory':'/api/hermes/problem-memory'}}
                        if seconds is not None:data['execution_budget']={'max_run_seconds':seconds}
                    else:
                        self.assertEqual(request.url.path,'/api/hermes/draws/'+'c'*32)
                        data={'id':'c'*32,'status':'failed','error':'官方账号认证已失效，请重新登录后重试'}
                    return httpx.Response(200,json=data)
                api=ACM(self.root,session=session,connection=Connection('https://new.example',True),
                        transport=httpx.MockTransport(handle))
                with patch.object(api.memory,'recovery',new=AsyncMock(return_value={})),\
                        patch('practice_backend.time',SimpleNamespace(monotonic=lambda:next(clock))),\
                        patch('practice_backend.asyncio.sleep',new=AsyncMock()),\
                        self.assertRaises(PracticeError) as ctx:
                    clock=iter([0,700])
                    asyncio.run(api.draw('b'*32,1800,None))
                self.assertEqual(getattr(ctx.exception,'pending',False),pending)
                self.assertEqual('/api/hermes/draws/'+'c'*32 in paths,not pending)

    def test_invalid_draw_budgets_do_not_disable_bounded_wait(self):
        from practice_backend import ACM
        for value in [None,True,'1200',-1,0,3601]:
            with self.subTest(value=value):
                self.assertEqual(ACM.draw_wait_seconds({'execution_budget':{'max_run_seconds':value}}),660)

    def test_progress_shows_reselection_counts_without_private_reviews(self):
        from practice_core import Game, Store
        store=Store(self.root);row,_=store.start('chat',1800,None)
        job={'status':'running','control':{'stage':'rerolling'},'selection_progress':{
            'selected_count':3,'report_attempts':1,'current_problem_id':'PRIVATE-PROBLEM',
            'reviews':[{'problem_id':'PRIVATE-PROBLEM','rating':1400,'decision':'PRIVATE-REPORT'}]}}
        api=SimpleNamespace(recover=AsyncMock(return_value=job))
        reply=asyncio.run(Game(store,api,None).progress(row))
        self.assertIn('评分未达标，正在换题',reply.text)
        self.assertIn('已检查 3 道候选',reply.text)
        self.assertIn('题解生成 1 次',reply.text)
        self.assertNotIn('PRIVATE',reply.text)
        job['selection_progress']['selected_count']=True
        job['selection_progress']['report_attempts']='PRIVATE-COUNTER'
        reply=asyncio.run(Game(store,api,None).progress(row))
        self.assertNotIn('已检查',reply.text)
        self.assertNotIn('PRIVATE',reply.text)


if __name__=='__main__':unittest.main()
