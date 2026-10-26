"""Optional, signed official API reads of one previously verified submission.

The transport must pace GETs and enforce a response size limit; it receives a
private signed URL and must never log it. No credentials or responses are cached.
See https://codeforces.com/apiHelp/ and /apiHelp/methods#user.status.
"""
import base64
import binascii
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode

from .remote_judges import PlatformSession, SubmissionError


_URL = 'https://codeforces.com/api/user.status'
_MAX_BODY = 2 * 1024 * 1024
_MAX_SOURCE = 65536
_MAX_SOURCE_BASE64 = ((_MAX_SOURCE + 2) // 3) * 4


def _signed_url(api_key, api_secret, handle, first, count, *, include_sources=False):
    params = {'apiKey': api_key, 'time': str(int(time.time())), 'handle': handle,
              'from': str(first), 'count': str(count)}
    if include_sources:
        params['includeSources'] = 'true'
    rand = ''.join(secrets.choice('0123456789abcdefghijklmnopqrstuvwxyz') for _ in range(6))
    canonical = '&'.join(f'{key}={value}' for key, value in sorted(params.items()))
    signature = hashlib.sha512(f'{rand}/user.status?{canonical}#{api_secret}'.encode()).hexdigest()
    params['apiSig'] = rand + signature
    return _URL + '?' + urlencode(params)


def _page(request, api_key, api_secret, handle, first, count, *, include_sources=False):
    url = _signed_url(api_key, api_secret, handle, first, count, include_sources=include_sources)
    try:
        status, _, text = request('GET', url)
    except SubmissionError as error:
        raise SubmissionError(error.reason) from None
    except Exception:
        raise SubmissionError('network') from None
    if type(status) is not int:
        raise SubmissionError('network')
    if status == 429:
        raise SubmissionError('rate_limited')
    if status in (401, 403) or 300 <= status < 400:
        raise SubmissionError('access_restricted')
    if status == 400:
        raise SubmissionError('api_rejected')
    if status != 200:
        raise SubmissionError('network')
    try:
        body_size = len(text.encode('utf-8')) if isinstance(text, str) else _MAX_BODY + 1
    except UnicodeError:
        raise SubmissionError('source_unavailable') from None
    if body_size > _MAX_BODY:
        raise SubmissionError('source_unavailable')
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise SubmissionError('source_unavailable') from None
    if not isinstance(payload, dict):
        raise SubmissionError('source_unavailable')
    if payload.get('status') != 'OK':
        comment = payload.get('comment')
        reason = 'rate_limited' if isinstance(comment, str) and 'call limit exceeded' in comment.casefold() else 'api_rejected'
        raise SubmissionError(reason)
    rows = payload.get('result')
    if not isinstance(rows, list) or len(rows) > count or any(not isinstance(row, dict) for row in rows):
        raise SubmissionError('source_unavailable')
    return rows


def _bind(expected, handle, row):
    record = PlatformSession.codeforces_record(expected['problem_id'], handle, str(expected['id']), row)
    if (record['submitted_at'] != expected['submitted_at']
            or record['language'] != expected['language']):
        raise SubmissionError('record_mismatch')
    if not isinstance(record['language'], str) or not record['language'].strip():
        raise SubmissionError('source_unavailable')
    if record['verdict'] == 'PENDING':
        raise SubmissionError('source_unavailable')
    return record


def _source(row):
    # Official includeSources responses expose sourceBase64. Validate the
    # encoded size before allocating the decoded bytes and decode UTF-8 strictly.
    encoded = row.get('sourceBase64')
    if not isinstance(encoded, str) or len(encoded) > _MAX_SOURCE_BASE64:
        raise SubmissionError('source_unavailable')
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > _MAX_SOURCE:
            raise SubmissionError('source_unavailable')
        code = raw.decode('utf-8', errors='strict')
    except (ValueError, binascii.Error, UnicodeError):
        raise SubmissionError('source_unavailable') from None
    if not code.strip() or code.strip().upper() == 'N/A':
        raise SubmissionError('source_unavailable')
    # An unexpected parallel plaintext field must never override the verified
    # bytes. Refuse conflicting fields instead of choosing one silently.
    if 'source' in row and row['source'] != code:
        raise SubmissionError('source_unavailable')
    return code


def fetch_source(*, api_key, api_secret, expected, handle, request):
    """Return a matching official record plus ``code``, or a fixed safe error.

    Scan at most 600 metadata-only records, then fetch sources for just the
    target's exact position. A concurrent new submission changes the position:
    fail closed rather than reading or accepting that other submission's code.
    ``expected`` is a freshly verified normalized PlatformSession judge record.
    """
    if (not isinstance(api_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', api_key)
            or not isinstance(api_secret, str) or not 1 <= len(api_secret) <= 512
            or any(ord(char) < 33 or ord(char) > 126 for char in api_secret)
            or not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', handle)
            or not isinstance(expected, dict)
            or not re.fullmatch(r'[1-9]\d{0,11}', str(expected.get('id', '')))
            or not re.fullmatch(r'[1-9]\d{0,8}-[A-Z][A-Z0-9]{0,9}', str(expected.get('problem_id', '')))
            or not isinstance(expected.get('account'), str)
            or expected['account'].casefold() != handle.casefold()
            or type(expected.get('submitted_at')) is not int
            or not isinstance(expected.get('language'), str)):
        raise SubmissionError('invalid_input')
    sid = str(expected['id'])
    for first in (1, 201, 401):
        rows = _page(request, api_key, api_secret, handle, first, 200)
        matches = [(index, row) for index, row in enumerate(rows) if str(row.get('id')) == sid]
        if len(matches) > 1:
            raise SubmissionError('record_mismatch')
        if matches:
            index, row = matches[0]
            _bind(expected, handle, row)
            # Do not copy or store metadata-page rows, even if the upstream
            # unexpectedly included source fields without our requesting them.
            del rows, matches, row
            source_rows = _page(request, api_key, api_secret, handle, first + index, 1, include_sources=True)
            if len(source_rows) != 1:
                raise SubmissionError('record_unavailable')
            record = _bind(expected, handle, source_rows[0])
            return {**record, 'code': _source(source_rows[0])}
        if len(rows) < 200:
            break
    raise SubmissionError('source_unavailable')
