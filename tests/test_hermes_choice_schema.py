from pydantic import ValidationError
import pytest
from acmhermes.models import candidate_choice
from acmweb.ai_schema import strict_output_schema

def test_exact_candidate_ids_are_bound_to_strict_output_schema():
    model=candidate_choice(['codeforces:1914-D','codeforces:1903-B'])
    schema=strict_output_schema(model.model_json_schema())
    assert schema['properties']['problem_id']['enum']==['codeforces:1914-D','codeforces:1903-B']
    assert model.model_validate({'problem_id':'codeforces:1914-D','reason':'已选'}).problem_id=='codeforces:1914-D'
    for pid in ['codeforces:1914D','1914D','codeforces:4-A']:
        with pytest.raises(ValidationError):model.model_validate({'problem_id':pid,'reason':'无效题号'})

def test_single_candidate_and_empty_pool():
    model=candidate_choice(['codeforces:4-A'])
    schema=strict_output_schema(model.model_json_schema())
    assert schema['properties']['problem_id'].get('const')=='codeforces:4-A'
    with pytest.raises(ValueError):candidate_choice([])
