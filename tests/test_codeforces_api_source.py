"""Synthetic official API fixtures, not evidence of a live source retrieval."""
import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest

from acmweb import codeforces_api_source as api
from acmweb.remote_judges import PlatformSession, SubmissionError


def official(sid=123, handle='demo', **changes):
    code = changes.pop('source', 'int main() {}')
    row = {'id': sid, 'problem': {'contestId': 4, 'index': 'A'},
           'author': {'members': [{'handle': handle}]},
           'creationTimeSeconds': 1700000000, 'verdict': 'WRONG_ANSWER',
           'programmingLanguage': 'GNU C++20',
           'sourceBase64': base64.b64encode(code.encode()).decode()}
    row.update(changes)
    return row


def expected(handle='demo', sid=123):
    return PlatformSession.codeforces_record('4-A', handle, str(sid), official(sid, handle))


def transport(responses):
    calls = []
    def request(method, url):
        assert method == 'GET'
        parsed = urlsplit(url)
        assert parsed.scheme == 'https' and parsed.netloc == 'codeforces.com'
        assert parsed.path == '/api/user.status'
        calls.append(parse_qs(parsed.query))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, tuple):
            return response
        return 200, {}, json.dumps({'status': 'OK', 'result': response})
    return request, calls


def read(request, **changes):
    args = dict(api_key='fixture-key', api_secret='fixture-secret', expected=expected(), handle='demo', request=request)
    args.update(changes)
    return api.fetch_source(**args)


def test_official_signature_and_exact_single_source_request(monkeypatch):
    monkeypatch.setattr(api.time, 'time', lambda: 1800000000)
    monkeypatch.setattr(api.secrets, 'choice', lambda _: 'x')
    request, calls = transport([[official(125), official()], [official()]])
    assert read(request)['code'] == 'int main() {}'
    assert calls[0]['from'] == ['1'] and calls[0]['count'] == ['200']
    assert 'includeSources' not in calls[0]
    assert calls[1]['from'] == ['2'] and calls[1]['count'] == ['1']
    assert calls[1]['includeSources'] == ['true']
    canonical = 'apiKey=fixture-key&count=1&from=2&handle=demo&includeSources=true&time=1800000000'
    digest = hashlib.sha512(f'xxxxxx/user.status?{canonical}#fixture-secret'.encode()).hexdigest()
    assert calls[1]['apiSig'] == ['xxxxxx' + digest]
    assert 'fixture-secret' not in json.dumps(calls)


def test_metadata_pagination_uses_absolute_position_and_no_other_source_returned():
    page = [official(1000 + i, source='unrelated') for i in range(200)]
    request, calls = transport([page, page, [official(124), official()], [official()]])
    result = read(request)
    assert [call['from'] for call in calls] == [['1'], ['201'], ['401'], ['402']]
    assert result['id'] == '123' and 'unrelated' not in json.dumps(result)


def test_bounds_metadata_search_to_600_then_stops():
    page = [official(1000 + i) for i in range(200)]
    request, calls = transport([page, page, page])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'source_unavailable' and len(calls) == 3
    assert all('includeSources' not in call for call in calls)


@pytest.mark.parametrize('changes', [
    {'author': {'members': [{'handle': 'another-user'}]}},
    {'problem': {'contestId': 5, 'index': 'A'}},
    {'creationTimeSeconds': 1700000001},
    {'programmingLanguage': 'Python 3'},
])
@pytest.mark.parametrize('phase', ['metadata', 'source'])
def test_record_binding_before_and_after_source_lookup(changes, phase):
    altered = official(**changes)
    responses = [[altered]] if phase == 'metadata' else [[official()], [altered]]
    request, calls = transport(responses)
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'record_mismatch'
    assert len(calls) == (1 if phase == 'metadata' else 2)


def test_concurrent_new_submission_changes_offset_and_must_not_replace_target():
    request, _ = transport([[official()], [official(124, source='other code')]])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'record_mismatch'


@pytest.mark.parametrize('source', ['', '   ', 'N/A', 'n/a', 'a' * 65537, '汉' * 21846])
def test_source_missing_sentinel_invalid_or_oversize_is_not_used(source):
    request, _ = transport([[official()], [official(source=source)]])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'source_unavailable'


@pytest.mark.parametrize('verdict', [None, 'TESTING', 'SUBMITTED', 'UNKNOWN_VALUE'])
def test_pending_or_invalid_verdict_is_not_source_evidence(verdict):
    request, calls = transport([[official(verdict=verdict)]])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'source_unavailable' and len(calls) == 1


@pytest.mark.parametrize(('response', 'reason'), [
    ((403, {}, 'SECRET'), 'access_restricted'),
    ((302, {'Location': 'https://other.example'}, ''), 'access_restricted'),
    ((429, {}, 'SECRET'), 'rate_limited'),
    ((400, {}, 'SECRET rejected signature URL'), 'api_rejected'),
    ((200, {}, json.dumps({'status': 'FAILED', 'comment': 'Call limit exceeded SECRET'})), 'rate_limited'),
    ((200, {}, json.dumps({'status': 'FAILED', 'comment': 'API key invalid SECRET'})), 'api_rejected'),
    ((200, {}, json.dumps({'status': 'FAILED', 'comment': 'Permission denied SECRET'})), 'api_rejected'),
    ((200, {}, '{SECRET'), 'source_unavailable'),
    ((200, {}, 'null'), 'source_unavailable'),
    ((503, {}, 'SECRET'), 'network'),
    (RuntimeError('SECRET signed URL'), 'network'),
])
def test_failures_only_expose_fixed_reason(response, reason):
    request, _ = transport([response])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == reason
    assert 'SECRET' not in str(error.value)
    assert error.value.__context__ is None or error.value.__suppress_context__


def test_parallel_user_reads_keep_credentials_records_and_code_isolated():
    def worker(i):
        handle = f'user{i}'
        code = f'// fixture source {i}'
        request, calls = transport([[official(i + 1, handle)], [official(i + 1, handle, source=code)]])
        result = read(request, api_key=f'key-{i}', api_secret=f'secret-{i}',
                      handle=handle, expected=expected(handle, i + 1))
        assert all(call['apiKey'] == [f'key-{i}'] and call['handle'] == [handle] for call in calls)
        assert result['account'] == handle and result['code'] == code
        return result['id']
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(worker, range(8))) == [str(i) for i in range(1, 9)]


def test_invalid_expected_account_never_sends_api_key():
    request, calls = transport([])
    with pytest.raises(SubmissionError) as error:
        read(request, handle='another-user')
    assert error.value.reason == 'invalid_input' and calls == []


def test_missing_source_field_does_not_accept_an_unofficial_alias():
    row = official()
    row.pop('sourceBase64')
    row['source'] = 'not a verified encoded source field'
    row['sourceCode'] = 'not an accepted official field'
    request, _ = transport([[official()], [row]])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'source_unavailable'


@pytest.mark.parametrize('result', [[], [official(), official()]])
def test_missing_or_duplicate_source_rows_are_rejected(result):
    request, _ = transport([[official()], result])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason in {'source_unavailable', 'record_unavailable'}


def test_duplicate_target_metadata_cannot_select_a_source_position():
    request, calls = transport([[official(), official()]])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'record_mismatch' and len(calls) == 1


def test_official_observed_source_base64_shape_with_synthetic_3152_char_value():
    code = '// ' + 'x' * 2360 + '\n'
    row = official(source=code)
    assert len(row['sourceBase64']) == 3152
    assert 'source' not in row
    request, _ = transport([[official()], [row]])
    assert read(request)['code'] == code


@pytest.mark.parametrize('encoded', [None, 42, {}, '!', 'a', 'YQ', 'YQ==\n', '汉', '/w=='])
def test_malformed_base64_or_non_utf8_is_rejected_without_plaintext_fallback(encoded):
    row = official(sourceBase64=encoded)
    row['source'] = 'int main() {}'
    request, _ = transport([[official()], [row]])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'source_unavailable'
    assert error.value.__context__ is None or error.value.__suppress_context__


def test_maximum_encoded_length_checked_before_decoding(monkeypatch):
    encoded = 'A' * (api._MAX_SOURCE_BASE64 + 1)
    request, _ = transport([[official()], [official(sourceBase64=encoded)]])
    def must_not_decode(*args, **kwargs):
        pytest.fail('oversized encoded content reached decoder')
    monkeypatch.setattr(api.base64, 'b64decode', must_not_decode)
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'source_unavailable'


def test_decoded_64kib_boundary_and_utf8_are_preserved():
    code = '// 中文\n' + 'x' * (65536 - len('// 中文\n'.encode()))
    request, _ = transport([[official()], [official(source=code)]])
    assert len(code.encode()) == 65536
    assert read(request)['code'] == code


@pytest.mark.parametrize('plaintext', ['different source', None, 42])
def test_conflicting_plaintext_field_cannot_override_decoded_source(plaintext):
    row = official()
    row['source'] = plaintext
    request, _ = transport([[official()], [row]])
    with pytest.raises(SubmissionError) as error:
        read(request)
    assert error.value.reason == 'source_unavailable'


def test_matching_parallel_plaintext_field_is_consistent_evidence():
    row = official()
    row['source'] = 'int main() {}'
    request, _ = transport([[official()], [row]])
    assert read(request)['code'] == row['source']
