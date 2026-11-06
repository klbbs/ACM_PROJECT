"""Exact topic aliases and shared multi-topic matching for Hermes metadata queries.

Parsing never changes the persisted request text, preserving old idempotency
fingerprints. Broad platform tags do not certify a finer algorithm category.
"""
import re

from acmweb.training_topics import TOPICS, _CF_TAG_TO_LUOGU, _TOPIC_ALIASES, matches_topic

MAX_TOPICS = 8
MAX_TOPIC_LENGTH = 80
MAX_QUERY_LENGTH = MAX_TOPICS * (MAX_TOPIC_LENGTH + 1) - 1


def _key(value):
    return ' '.join(value.casefold().split())


def _aliases():
    # Prefer official broad categories when their titles overlap legacy topics.
    result = {_key(t['title']): t['id'] for t in TOPICS.values()}
    for tag, names in _TOPIC_ALIASES.items():
        if f'luogu-{tag}' in TOPICS:
            for name in names:
                result[_key(name)] = f'luogu-{tag}'
    for tag, number in _CF_TAG_TO_LUOGU.items():
        if f'luogu-{number}' in TOPICS:
            result[_key(tag)] = f'luogu-{number}'
    result.update({'动态规划':'luogu-3','游戏':'luogu-13','game theory':'luogu-13'})
    result.update({_key(tid): tid for tid in TOPICS})
    return result


ALIASES = _aliases()


def parse_topics(query):
    if query is None:
        return []
    if not isinstance(query, str) or not 1 <= len(query) <= MAX_QUERY_LENGTH:
        raise ValueError('topic must be a comma-separated string with at most 8 categories')
    if any(symbol in query for symbol in '()（）'):
        raise ValueError('topic does not accept parentheses; & has precedence over |')
    groups = [re.split('[&,，]', group) for group in query.split('|')]
    if sum(map(len, groups)) > MAX_TOPICS:
        raise ValueError('topic accepts at most 8 categories')
    result, seen = [], {}
    for group_index, values in enumerate(groups):
        for value in values:
            value = value.strip()
            if not 1 <= len(value) <= MAX_TOPIC_LENGTH:
                raise ValueError('topic categories must not be blank or longer than 80 characters')
            canonical = ALIASES.get(_key(value))
            identity = canonical or _key(value)
            if identity not in seen:
                topic = TOPICS.get(canonical)
                seen[identity] = {'id': identity, 'title': topic['title'] if topic else value,
                                 'topic': topic, 'raw_tag': _key(value), 'groups': []}
                result.append(seen[identity])
            if group_index not in seen[identity]['groups']:
                seen[identity]['groups'].append(group_index)
    return result


def method_evidence(service, topics):
    result = {}
    for item in topics:
        if item['topic']:
            for pid, evidence in service.app.state.topic_feasibility.approved_map(item['id']).items():
                result.setdefault(pid, {})[item['id']] = evidence
    return result


def matches_topics(problem, topics, methods):
    if not topics:
        return True
    tags = {_key(str(tag)) for tag in problem.get('tags', [])}
    evidence = methods.get(problem['id'], {})
    checks = {item['id']: item['id'] in evidence or (matches_topic(problem, item['topic'])
              if item['topic'] else item['raw_tag'] in tags) for item in topics}
    return any(all(checks[item['id']] for item in topics if group in item['groups'])
               for group in {group for item in topics for group in item['groups']})


def context(topics):
    groups = [[{'id': t['id'], 'title': t['title']} for t in topics if group in t['groups']]
              for group in sorted({g for t in topics for g in t['groups']})]
    return {'operator': 'OR' if len(groups) > 1 else 'AND',
            'topics': [{'id': t['id'], 'title': t['title']} for t in topics],
            'any_of_all': groups,
            'notice': '各组取并集，每组内全部类别须同时满足；&及逗号优先于|，宽泛标签不证明细分算法。'}
