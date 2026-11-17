import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        with self.assertRaisesRegex(PracticeError,'旧服务器'):
            asyncio.run(api.draw(key,1200,None))


if __name__=='__main__':unittest.main()
