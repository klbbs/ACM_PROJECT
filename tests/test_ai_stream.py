"""Synthetic Responses streams only: no live models, billing or OJ requests."""
import asyncio
import json

import httpx
import pytest

from acmweb import ai_stream
from acmweb.ai_stream import read_responses_stream
from acmweb.codex_bridge import AIError


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def response(chunks, content_type='text/event-stream'):
    return httpx.Response(200, headers={'content-type': content_type}, stream=Chunks(chunks))


def final(text='题目报告'):
    return {'id': 'resp_synthetic', 'status': 'completed', 'output': [
        {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'private reasoning'}]},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


def event(kind, **fields):
    return ('data: ' + json.dumps({'type': kind, **fields}, ensure_ascii=False) + '\n\n').encode()


def read(chunks, content_type='text/event-stream'):
    return asyncio.run(read_responses_stream(response(chunks, content_type)))


def assert_failure(chunks, reason, content_type='text/event-stream'):
    with pytest.raises(AIError) as caught:
        read(chunks, content_type)
    assert caught.value.reason == reason
    assert 'private secret' not in str(caught.value)
    assert 'private reasoning' not in str(caught.value)


def test_full_terminal_output_wins_over_reasoning_and_text_deltas():
    terminal = final()
    chunks = [b': heartbeat\n\n', event('response.created'),
              event('response.reasoning_text.delta', delta='private reasoning'),
              event('response.output_text.delta', delta='partial wrong text'),
              event('response.output_text.done', text='another wrong text'),
              event('response.completed', response=terminal)]
    assert read(chunks) == terminal


def test_split_utf8_chunks_multiline_data_and_crlf():
    body = final()
    pretty = json.dumps({'type': 'response.completed', 'response': body}, ensure_ascii=False, indent=2)
    stream = ': heartbeat\r\nevent: response.completed\r\n'
    stream += ''.join('data: ' + line + '\r\n' for line in pretty.splitlines()) + '\r\n'
    assert read([bytes([byte]) for byte in stream.encode()]) == body


def test_bare_cr_lines_and_type_from_sse_event_field():
    payload = json.dumps({'response': final()}, ensure_ascii=False).encode()
    assert read([b'event: response.completed\rdata: ' + payload + b'\r\r']) == final()


@pytest.mark.parametrize('status', [None, 'in_progress', 'queued', 'cancelled'])
def test_terminal_event_requires_completed_status(status):
    assert_failure([event('response.completed', response={'status': status, 'output': []})],
                   'ai_response_incomplete')


@pytest.mark.parametrize('partial', [
    event('response.output_text.delta', delta='private secret'),
    event('response.output_text.done', text='private secret'),
    b'data: [DONE]\n\n',
    b'',
    event('response.completed', response=final())[:-1],
])
def test_eof_or_done_without_terminal_confirmation_is_never_success(partial):
    assert_failure([partial], 'ai_stream_interrupted')


def test_completed_without_full_output_does_not_rebuild_from_deltas():
    assert_failure([event('response.output_text.done', text='private secret'),
                    event('response.completed', response={'status': 'completed'})], 'ai_empty_final')


@pytest.mark.parametrize('kind, extra, reason', [
    ('response.failed', {'response': {'status': 'failed', 'error': {'message': 'private secret'}}}, 'ai_response_failed'),
    ('error', {'message': 'private secret'}, 'ai_response_failed'),
    ('response.incomplete', {'response': {'status': 'incomplete'}}, 'ai_response_incomplete'),
    ('response.incomplete', {'response': {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}}, 'ai_output_truncated'),
    ('response.incomplete', {'response': {'status': 'incomplete', 'incomplete_details': {'reason': 'content_filter'}}}, 'ai_response_refused'),
])
def test_terminal_failure_diagnostics_are_safe(kind, extra, reason):
    assert_failure([event(kind, **extra)], reason)


def test_sse_error_event_cannot_be_disguised_by_payload_type():
    payload = json.dumps({'type': 'response.completed', 'response': final()}).encode()
    assert_failure([b'event: error\ndata: ' + payload + b'\n\n'], 'ai_response_failed')


@pytest.mark.parametrize('data, reason', [
    (b'data: invalid private secret\n\n', 'ai_invalid_output'),
    (b'data: []\n\n', 'ai_invalid_output'),
    (b'data: \xff\n\n', 'ai_invalid_output'),
    (b'data: \xe4', 'ai_invalid_output'),
    (event('response.completed', response=[]), 'ai_invalid_output'),
])
def test_malformed_stream_is_rejected(data, reason):
    assert_failure([data], reason)


def test_explicit_json_fallback_is_bounded_and_requires_completed():
    body = json.dumps(final(), ensure_ascii=False).encode()
    assert read([body[:3], body[3:]], 'application/json; charset=utf-8') == final()
    assert_failure([b'{"output":[]}'], 'ai_response_incomplete', 'application/json')
    assert_failure([b'{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}'],
                   'ai_output_truncated', 'application/json')
    assert_failure([b'{"status":"failed","error":{"message":"private secret"}}'],
                   'ai_response_failed', 'application/json')
    assert_failure([b'\xff'], 'ai_invalid_output', 'application/json')


@pytest.mark.parametrize('content_type', ['text/html', '', 'application/octet-stream'])
def test_does_not_sniff_error_pages_as_json(content_type):
    assert_failure([json.dumps(final()).encode()], 'ai_invalid_output', content_type)


@pytest.mark.parametrize('content_type', ['text/event-stream', 'application/json'])
def test_total_limit(monkeypatch, content_type):
    monkeypatch.setattr(ai_stream, 'MAX_TOTAL_BYTES', 12)
    assert_failure([b' ' * 7, b' ' * 7], 'ai_response_too_large', content_type)


def test_event_limit_including_comments(monkeypatch):
    monkeypatch.setattr(ai_stream, 'MAX_EVENT_BYTES', 20)
    assert_failure([b':123456789\n:123456789\n:123456789\n'], 'ai_response_too_large')


def test_line_limit_including_unterminated_line(monkeypatch):
    monkeypatch.setattr(ai_stream, 'MAX_LINE_CHARS', 10)
    assert_failure([b':12345', b'678901'], 'ai_response_too_large')
    assert_failure([b':12345678901\n'], 'ai_response_too_large')


def test_network_disconnect_propagates_without_reclassifying_as_completion():
    class Disconnect(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield event('response.output_text.delta', delta='partial')
            raise httpx.ReadError('synthetic disconnect')
    stream = httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Disconnect())
    with pytest.raises(httpx.ReadError):
        asyncio.run(read_responses_stream(stream))


def test_cancellation_propagates():
    async def scenario():
        entered = asyncio.Event()
        class Block(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b''
        stream = httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Block())
        task = asyncio.create_task(read_responses_stream(stream))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())


@pytest.mark.parametrize('output', [[None], [{'content': None}], [{'content': ['not an object']}]])
def test_invalid_full_output_shape_is_rejected_safely(output):
    assert_failure([event('response.completed', response={'status': 'completed', 'output': output})],
                   'ai_invalid_output')
