"""Local adapter tests use temporary synthetic bridges, never the running bot."""
import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from acmhermes.commands import parse_roll, select_score

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('bridge_patcher',ROOT/'scripts/patch_hermes_bridge.py')
patcher=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patcher)
HELPER=(ROOT/'src/acmhermes/commands.py').read_bytes()


@pytest.mark.parametrize(('argument','expected'),[
    ('dp&博弈论',(None,'dp&博弈论')),(' dp | 博弈论 ',(None,'dp | 博弈论')),
    ('1800 dp，博弈论',(1800,'dp，博弈论')),('1800',(1800,None)),('',(None,None)),
    ('1800 tree dp&games',(1800,'tree dp&games'))])
def test_parse_roll(argument,expected):
    assert parse_roll(argument)==expected


@pytest.mark.parametrize('argument',['799 dp','3501','1800.5 math','-1800','dp|','dp&&games',
                                     'dp&(games|math)',','.join(['dp']*9),'x'*81])
def test_parse_invalid(argument):
    with pytest.raises(ValueError):parse_roll(argument)


def test_score_precedence_and_no_silent_default():
    assert select_score(1800,{'goal_rating':2000})==1800
    assert select_score(None,{'goal_rating':2000,'minimum':1000,'maximum':1600})==2000
    assert select_score(None,{'goal_rating':4000,'minimum':1700,'maximum':1900})==1800
    for document in (None,{}, {'goal_rating':True},{'goal_rating':'1800'},
                     {'minimum':2000,'maximum':1800},{'minimum':799,'maximum':1800}):
        with pytest.raises(ValueError):select_score(None,document)


def bridge_files(directory):
    core='''"""Synthetic bridge, no private data."""
class Bridge:
    async def handle(self,cmd,arg,shared,user=''):
        row=self.store.row(shared)
        if cmd=='/roll':
            if row and row['status']=='drawing':
                return ('recover',row['id'])
            if row and row['status'] in {'active','grading'}:
                return ('reuse',row['id'])
'''+patcher.CORE_OLD+'''
            return row,new
'''
    backend='''"""Synthetic backend."""
class ACM:
    async def draw(self):
        return 'frozen-body-must-not-change'
    @staticmethod
    def topic_name(topic,caps):
        if not topic:return None
        aliases={'动态规划':'dp'}
        return aliases.get(topic,topic)
'''
    (directory/'practice_core.py').write_text(core)
    (directory/'practice_backend.py').write_text(backend)
    return core,backend


def test_patcher_dry_plan_idempotence_backups_and_frozen_methods_untouched(tmp_path):
    core,backend=bridge_files(tmp_path)
    originals,changes=patcher.plan(tmp_path,HELPER)
    assert (tmp_path/'practice_core.py').read_text()==core
    assert set(changes)=={'practice_core.py','practice_backend.py','acm_roll_commands.py'}
    backup=patcher.apply(tmp_path,originals,changes)
    assert (backup/'practice_core.py').read_text()==core
    assert (backup/'practice_backend.py').read_text()==backend
    assert backup.stat().st_mode&0o777==0o700
    assert (backup/'practice_core.py').stat().st_mode&0o777==0o600
    assert "return 'frozen-body-must-not-change'" in (tmp_path/'practice_backend.py').read_text()
    assert patcher.plan(tmp_path,HELPER)[1]=={}


def test_patcher_unknown_and_concurrent_layout_rejected(tmp_path):
    bridge_files(tmp_path)
    originals,changes=patcher.plan(tmp_path,HELPER)
    target=tmp_path/'practice_core.py'
    target.write_text(target.read_text()+'\n# concurrent edit\n')
    with pytest.raises(ValueError,match='changed after'):patcher.apply(tmp_path,originals,changes)
    target.write_text('unknown = True\n')
    with pytest.raises(ValueError,match='unknown bridge layout'):patcher.plan(tmp_path,HELPER)


def test_patcher_symlink_rejected(tmp_path):
    bridge_files(tmp_path)
    target=tmp_path/'practice_backend.py'
    target.rename(tmp_path/'real.py')
    target.symlink_to(tmp_path/'real.py')
    with pytest.raises(ValueError,match='symlink'):patcher.plan(tmp_path,HELPER)


@pytest.fixture
def adapted(tmp_path,monkeypatch):
    bridge_files(tmp_path)
    originals,changes=patcher.plan(tmp_path,HELPER)
    helper=ModuleType('acm_roll_commands')
    exec(compile(HELPER,'acm_roll_commands.py','exec'),helper.__dict__)
    monkeypatch.setitem(__import__('sys').modules,'acm_roll_commands',helper)
    namespace={'Reply':lambda value:value}
    exec(compile(changes['practice_core.py'],'practice_core.py','exec'),namespace)
    bridge=namespace['Bridge']()
    bridge.api=SimpleNamespace(ready=AsyncMock(),training_preferences=AsyncMock(return_value={'goal_rating':1900}))
    class Store:
        def __init__(self):self.rows={};self.started=[]
        def row(self,scope):return self.rows.get(scope)
        def start(self,scope,score,topic,owner=''):
            self.started.append((scope,score,topic,owner));return {'score':score,'topic':topic},True
    bridge.store=Store()
    return bridge,changes


def test_omitted_score_reads_authenticated_preferences_only_without_scope_history(adapted):
    bridge,_=adapted
    bridge.store.rows['other']={'id':'other','status':'passed','score':2500}
    result=asyncio.run(bridge.handle('/roll','dp&博弈论','mine','u'))
    assert result[0]=={'score':1900,'topic':'dp&博弈论'}
    assert bridge.store.started==[('mine',1900,'dp&博弈论','u')]
    assert bridge.api.training_preferences.await_count==1
    bridge.store.rows['mine']={'id':'previous','status':'passed','score':1800}
    asyncio.run(bridge.handle('/roll','dp|博弈论','mine','u'))
    assert bridge.store.started[-1][1:3]==(1800,'dp|博弈论')
    assert bridge.api.training_preferences.await_count==1


def test_pending_and_active_rounds_reused_without_resolving_or_reserving(adapted):
    bridge,_=adapted
    for status,action in [('drawing','recover'),('active','reuse'),('grading','reuse')]:
        bridge.store.rows['mine']={'id':'saved-key','status':status,'score':1600}
        assert asyncio.run(bridge.handle('/roll','dp&博弈论','mine'))==(action,'saved-key')
    assert bridge.store.started==[]
    assert bridge.api.ready.await_count==bridge.api.training_preferences.await_count==0


def test_invalid_preferences_do_not_reserve_round_and_explicit_score_skips_settings(adapted):
    bridge,_=adapted
    bridge.api.training_preferences.return_value={}
    assert '未有效配置' in asyncio.run(bridge.handle('/roll','dp&games','mine'))
    assert not bridge.store.started
    asyncio.run(bridge.handle('/roll','2000 dp&games','mine'))
    assert bridge.store.started[-1][1:3]==(2000,'dp&games')
    assert bridge.api.training_preferences.await_count==1


def test_backend_forwards_expression_and_authenticated_get_only(adapted):
    _,changes=adapted
    namespace={}
    exec(compile(changes['practice_backend.py'],'practice_backend.py','exec'),namespace)
    bridge=namespace['ACM']()
    assert bridge.topic_name('dp&博弈论',{})=='dp&博弈论'
    assert bridge.topic_name('动态规划',{})=='dp'
    events=[]
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*unused):pass
    bridge.client=Client
    async def auth(client):events.append('authenticated')
    async def request(client,method,path):
        assert events==['authenticated'];events.append((method,path));return {'goal_rating':1800}
    bridge.auth=auth;bridge.request=request
    assert asyncio.run(bridge.training_preferences())=={'goal_rating':1800}
    assert events==['authenticated',('GET','/api/training')]
