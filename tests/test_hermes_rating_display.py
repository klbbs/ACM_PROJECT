"""Synthetic external adapter checks, without starting or messaging the bot."""
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec=importlib.util.spec_from_file_location('rating_patch',SCRIPTS/'patch_hermes_rating_display.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_rating_adapter_is_bounded_idempotent_and_never_reads_report(tmp_path,monkeypatch):
    module=load(monkeypatch)
    path=tmp_path/'practice_text.py'
    path.write_text("def problem_sections(result):\n    explanation=result['problem_explanation']\n    lines=[explanation['title']]\n"+module.ANCHOR+"\n    return lines,tail\n")
    original,changes=module.plan(tmp_path)
    namespace={};exec(changes['practice_text.py'],namespace)
    class NoReport(dict):
        def get(self,key,*args):
            assert key!='report'
            return super().get(key,*args)
    result=NoReport(problem_explanation={'title':'合成题','constraints':[]},ai_difficulty_assessment={'rating':1840.5})
    assert '1840.5' in namespace['problem_sections'](result)[0][1]
    backup=module.apply(tmp_path,original,changes)
    assert (backup/'practice_text.py').read_bytes()==original['practice_text.py']
    assert module.plan(tmp_path)[1]=={}


def test_unknown_adapter_does_not_modify_files(tmp_path,monkeypatch):
    module=load(monkeypatch)
    path=tmp_path/'practice_text.py';path.write_text('unknown = True\n')
    with pytest.raises(ValueError):module.plan(tmp_path)
    assert path.read_text()=='unknown = True\n'
