"""Bounded Responses SSE decoding; only a complete final response is usable.

No deltas, reasoning text or upstream error bodies escape this module. Callers
own the streaming response context and its timeout/cancellation lifecycle.
"""
import codecs
import json
import re

from .codex_bridge import AIError

MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 4 * 1024 * 1024
MAX_LINE_CHARS = 2 * 1024 * 1024
_NEWLINE = re.compile(r'\r\n|\r|\n')
_MESSAGES = {
    'ai_stream_interrupted': 'AI 流式响应未收到完整结束确认，报告未保存；请手动重试（未自动重试）',
    'ai_response_failed': 'AI 提供方报告生成失败，报告未保存；请检查提供方状态后手动重试',
    'ai_response_incomplete': 'AI 响应未完成，报告未保存；请检查输出设置后手动重试',
    'ai_output_truncated': 'AI 输出达到 token 上限，报告未保存；请提高输出上限后手动重试',
    'ai_response_refused': 'AI 提供方未完成此次内容，报告未保存；请检查题面材料或联系提供方',
    'ai_invalid_output': 'AI 流式响应格式无效，报告未保存；请检查提供方协议配置',
    'ai_response_too_large': 'AI 响应超过安全读取上限，报告未保存；请调整输出设置',
    'ai_empty_final': 'AI 未返回完整最终文本，报告未保存（不会使用思考内容或未完成的片段）',
}


def _error(reason):
    result = AIError(_MESSAGES[reason])
    result.reason = reason
    return result


def _final(body):
    if not isinstance(body, dict):
        raise _error('ai_invalid_output')
    status = body.get('status')
    if status == 'failed' or body.get('error'):
        raise _error('ai_response_failed')
    if status == 'incomplete':
        detail = body.get('incomplete_details')
        reason = detail.get('reason') if isinstance(detail, dict) else None
        if reason == 'max_output_tokens':
            raise _error('ai_output_truncated')
        if reason == 'content_filter':
            raise _error('ai_response_refused')
        raise _error('ai_response_incomplete')
    if status != 'completed':
        raise _error('ai_response_incomplete')
    # A terminal event is required to carry the authoritative full output.
    # Never rebuild it from deltas/done events, which might be incomplete or
    # refer to another item, output index, tool call, or reasoning channel.
    if not isinstance(body.get('output'), list):
        raise _error('ai_empty_final')
    for item in body['output']:
        if not isinstance(item, dict):
            raise _error('ai_invalid_output')
        if 'content' in item and (not isinstance(item['content'], list) or
                                  any(not isinstance(part, dict) for part in item['content'])):
            raise _error('ai_invalid_output')
    return body


def _json(value):
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        raise _error('ai_invalid_output') from None


async def _lines(response):
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    total = 0
    buffer = ''
    try:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_TOTAL_BYTES:
                raise _error('ai_response_too_large')
            buffer += decoder.decode(chunk)
            while True:
                match = _NEWLINE.search(buffer)
                if match is None or (match.group() == '\r' and match.end() == len(buffer)):
                    break
                line, buffer = buffer[:match.start()], buffer[match.end():]
                if len(line) > MAX_LINE_CHARS:
                    raise _error('ai_response_too_large')
                yield line
            if len(buffer) > MAX_LINE_CHARS:
                raise _error('ai_response_too_large')
        buffer += decoder.decode(b'', final=True)
        if buffer:
            # SSE dispatch requires a blank line. Yielding this remaining line
            # cannot by itself turn an unterminated event into a final response.
            if buffer.endswith('\r'):
                yield buffer[:-1]
            else:
                yield buffer
    except UnicodeDecodeError:
        raise _error('ai_invalid_output') from None


async def read_responses_stream(response) -> dict:
    """Read SSE or explicit JSON fallback, accepting only completed responses.

    All size limits apply before JSON parsing. Network errors and cancellation
    propagate to the caller. This routine never retries or logs any content.
    """
    media = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if media == 'application/json':
        chunks = bytearray()
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_TOTAL_BYTES:
                raise _error('ai_response_too_large')
            chunks.extend(chunk)
        try:
            return _final(_json(chunks.decode('utf-8')))
        except UnicodeDecodeError:
            raise _error('ai_invalid_output') from None
    if media != 'text/event-stream':
        raise _error('ai_invalid_output')
    data = []
    event = ''
    event_size = 0
    async for line in _lines(response):
        if line == '':
            if data:
                payload = '\n'.join(data)
                if payload.strip() == '[DONE]':
                    raise _error('ai_stream_interrupted')
                value = _json(payload)
                if not isinstance(value, dict):
                    raise _error('ai_invalid_output')
                kind = value.get('type') or event
                if event == 'error' or kind in {'error', 'response.failed'}:
                    raise _error('ai_response_failed')
                if kind == 'response.incomplete':
                    body = value.get('response')
                    if not isinstance(body, dict) or body.get('status') != 'incomplete':
                        raise _error('ai_response_incomplete')
                    return _final(body)  # always raises with a safe reason
                if kind == 'response.completed':
                    return _final(value.get('response'))
            data, event, event_size = [], '', 0
            continue
        # Bound the whole event, including comments and non-data fields.
        event_size += len(line.encode('utf-8')) + 1
        if event_size > MAX_EVENT_BYTES:
            raise _error('ai_response_too_large')
        if line.startswith(':'):
            continue
        field, separator, value = line.partition(':')
        if separator and value.startswith(' '):
            value = value[1:]
        if field == 'data':
            data.append(value)
        elif field == 'event':
            event = value
    raise _error('ai_stream_interrupted')
