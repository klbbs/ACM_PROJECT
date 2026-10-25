"""Curated learning topics; IDs and known template examples are application owned."""
from copy import deepcopy
import json
from pathlib import Path
import re

# OI Wiki is a reference taxonomy, not a source of fabricated judge problems.
_DEFINITIONS = [
    ('basics', '算法基础', [
        ('binary-search', '二分查找', '在有序性与单调性中定位答案', ['binary search', '二分'], ['luogu:P2249']),
        ('prefix-sum', '前缀和与差分', '区间求和、差分和二维扩展', ['prefix sum', '前缀和', '差分'], ['luogu:P8218']),
        ('sorting', '排序', '比较、稳定性与排序应用', ['sortings', 'sorting', '排序'], ['luogu:P1177']),
        ('greedy', '贪心', '选择策略、交换论证与局部最优', ['greedy', '贪心'], []),
    ]),
    ('search', '搜索', [
        ('dfs', '深度优先搜索', '递归状态、回溯与剪枝', ['dfs', '深度优先', '回溯'], []),
        ('bfs', '广度优先搜索', '分层遍历与无权图最短路', ['bfs', '广度优先'], []),
    ]),
    ('dp', '动态规划', [
        ('knapsack', '背包动态规划', '状态、转移顺序与空间优化', ['knapsack', '背包'], ['luogu:P1048']),
        ('linear-dp', '线性动态规划', '从子问题构建状态转移', ['linear dp', '线性dp', '线性 dp', '线性动态规划'], []),
        ('interval-dp', '区间动态规划', '区间状态与合并顺序', ['interval dp', '区间dp', '区间动态规划'], ['luogu:P1880']),
        ('tree-dp', '树形动态规划', '树上子问题与换根', ['tree dp', '树形dp', '树形动态规划'], ['luogu:P1352']),
    ]),
    ('strings', '字符串', [
        ('kmp', 'KMP 字符串匹配', '前缀函数与模式匹配', ['kmp', 'prefix function', '前缀函数'], ['luogu:P3375']),
        ('trie', '字典树', '前缀查询与字符串集合', ['trie', '字典树'], ['luogu:P8306']),
        ('string-hash', '字符串哈希', '字符串比较与哈希冲突', ['string hash', '字符串哈希'], ['luogu:P3370']),
    ]),
    ('math', '数学', [
        ('sieve', '素数筛法', '线性筛、埃氏筛与整除性质', ['sieve', '素数筛', '线性筛', '埃氏筛'], ['luogu:P3383']),
        ('fast-power', '快速幂', '二进制分解与模运算', ['binary exponentiation', '快速幂'], ['luogu:P1226']),
        ('combinatorics', '组合数学', '计数、组合数与模逆元', ['combinatorics', '组合数学', '组合数'], ['luogu:P3807']),
    ]),
    ('structures', '数据结构', [
        ('dsu', '并查集', '集合合并、路径压缩与连通性', ['dsu', 'disjoint set', '并查集', 'unionfind'], ['luogu:P3367', 'atcoder:practice2_a']),
        ('fenwick', '树状数组', '前缀数据维护与区间查询', ['fenwick', 'binary indexed tree', '树状数组'], ['luogu:P3374', 'atcoder:practice2_b']),
        ('segment-tree', '线段树', '区间维护与懒标记', ['segment tree', '线段树'], ['luogu:P3372', 'luogu:P3373', 'atcoder:practice2_j']),
        ('sparse-table', 'ST 表', '静态区间查询与倍增', ['sparse table', 'st表', 'st 表'], ['luogu:P3865']),
        ('heap', '堆与优先队列', '动态最值与优先级', ['heap', 'priority queue', '堆', '优先队列'], ['luogu:P3378']),
    ]),
    ('graphs', '图论', [
        ('shortest-path', '最短路', '松弛、Dijkstra 与最短路建模', ['shortest path', '最短路', 'dijkstra'], ['luogu:P3371', 'luogu:P4779']),
        ('mst', '最小生成树', 'Kruskal、Prim 与最优连通', ['mst', 'minimum spanning', '最小生成树'], ['luogu:P3366']),
        ('topological-sort', '拓扑排序', '有向无环图与依赖关系', ['topological', '拓扑排序'], []),
        ('lca', '最近公共祖先', '树上倍增与距离查询', ['lca', 'lowest common ancestor', '最近公共祖先'], ['luogu:P3379']),
        ('scc', '强连通分量', '有向图分解与缩点', ['scc', 'strongly connected', '强连通', '缩点'], ['luogu:P3387']),
        ('network-flow', '网络流', '增广路、最大流与建模', ['flow', '网络流', '最大流'], ['luogu:P3376']),
    ]),
    ('geometry', '计算几何', [
        ('convex-hull', '凸包', '叉积、点序与凸多边形', ['convex hull', '凸包'], ['luogu:P2742']),
    ]),
]

# Official public taxonomy checked against https://www.luogu.com.cn/_lfe/tags.
# Numeric labels stay platform scoped. Broad DP/search/string tags never stand
# in for one of their specialist children (e.g. DP=3 cannot mean linear DP).
LUOGU_TOPIC_TAGS = {
    'binary-search': {45}, 'prefix-sum': {254, 330}, 'sorting': {113},
    'greedy': {7}, 'dfs': {127}, 'bfs': {126}, 'knapsack': {139},
    'linear-dp': {444}, 'interval-dp': {144}, 'tree-dp': {152},
    'kmp': {302}, 'trie': {300}, 'sieve': {475}, 'combinatorics': {252},
    'dsu': {47}, 'fenwick': {53}, 'segment-tree': {42}, 'sparse-table': {290},
    'heap': {51}, 'shortest-path': {160}, 'mst': {166},
    'topological-sort': {159}, 'lca': {211}, 'scc': {179},
    'network-flow': {79}, 'convex-hull': {291},
}


def _luogu_tags(problem):
    return {int(tag) for tag in problem.get('tags', [])
            if type(tag) is int or isinstance(tag, str) and re.fullmatch(r'-?[0-9]+', tag)}


TOPICS = {}
GROUPS = []
for group_id, title, rows in _DEFINITIONS:
    group = {'id': group_id, 'title': title, 'topics': []}
    for topic_id, name, description, keywords, templates in rows:
        topic = {'id': topic_id, 'title': name, 'description': description,
                 'source_url': 'https://oi-wiki.org/'}
        TOPICS[topic_id] = {**topic, 'keywords': keywords, 'template_ids': templates}
        group['topics'].append(topic)
    GROUPS.append(group)


# The shipped snapshot is official public metadata; no login, cookies or runtime
# network request is needed to browse it. Old application IDs remain valid for
# saved plans/API callers, but are not rendered as duplicate menu entries.
_CATALOG = json.loads(Path(__file__).with_name('training_topic_catalog.json').read_text())
_TAGS = {row['id']: row for row in _CATALOG['tags']}
_CHILDREN = {}
for _row in _TAGS.values():
    _CHILDREN.setdefault(_row['parent'], []).append(_row['id'])


def _descendants(tag_id):
    result = {tag_id}
    for child in _CHILDREN.get(tag_id, []):
        result.update(_descendants(child))
    return result


def _path(tag_id):
    row = _TAGS[tag_id]
    return (_path(row['parent']) if row['parent'] is not None else []) + [row['name'].lstrip('\ufeff')]


# Conservative, exact Codeforces tags: broad tags match only broad topics.
# In particular `dp` never means linear/tree/interval DP, and `data structures`
# does not establish a specific data structure.
_CF_TAG_TO_LUOGU = {
    'strings': 2, 'dp': 3, 'math': 5, 'graphs': 6, 'greedy': 7,
    'geometry': 8, 'games': 13, 'binary search': 45, 'dsu': 47,
    'fft': 69, 'number theory': 72, 'sortings': 113,
    'dfs and similar': 4, 'shortest paths': 160, 'mst': 166,
    'bipartite': 187, 'hashing': 235, 'combinatorics': 252,
    'probabilities': 266,
}

GROUPS = []
for root_id in _CHILDREN[None]:
    root = _TAGS[root_id]
    group = {'id': f'luogu-{root_id}', 'title': root['name'].lstrip('\ufeff'),
             'source': 'luogu', 'topics': []}
    def add_topic(tag_id, depth=0):
        row, path = _TAGS[tag_id], _path(tag_id)
        inherited = _descendants(tag_id)
        templates = list(dict.fromkeys(pid for alias, tags in LUOGU_TOPIC_TAGS.items()
            if tags & inherited for pid in TOPICS[alias]['template_ids']))
        topic = {'id': f'luogu-{tag_id}', 'title': path[-1],
                 'description': ' / '.join(path), 'path': path, 'depth': depth,
                 'parent_id': f"luogu-{row['parent']}" if row['parent'] is not None else None,
                 'keywords': [path[-1]] + sorted(tag for tag, target in _CF_TAG_TO_LUOGU.items() if target in inherited),
                 'source_url': _CATALOG['source_url'],
                 'source_version': _CATALOG['source_version']}
        TOPICS[topic['id']] = {**topic, 'template_ids': templates,
            'luogu_tags': inherited, 'official_exact': True,
            'cf_tags': {tag for tag, target in _CF_TAG_TO_LUOGU.items() if target in inherited}}
        group['topics'].append(topic)
        for child in _CHILDREN.get(tag_id, []):
            add_topic(child, depth + 1)
    add_topic(root_id)
    GROUPS.append(group)


MODES = [
    {'id': 'learn', 'title': '新学', 'description': '从模板题开始理解算法，再逐步应用'},
    {'id': 'consolidate', 'title': '巩固', 'description': '通过递进练习熟练运用已有知识'},
    {'id': 'variation', 'title': '变式', 'description': '理解条件变化、模型转换与不同应用'},
    {'id': 'strengthen', 'title': '加强', 'description': '挑战综合建模与多知识点结合'},
]


def topic_groups():
    return deepcopy(GROUPS)


def matches_topic(problem, topic):
    if problem.get('id') in topic['template_ids']:
        return True
    if topic.get('official_exact'):
        if problem.get('platform') == 'luogu':
            return bool(_luogu_tags(problem) & topic.get('luogu_tags', set()))
        if problem.get('platform') == 'codeforces':
            return bool({str(tag).casefold() for tag in problem.get('tags', [])} & topic.get('cf_tags', set()))
        return False
    if problem.get('platform') == 'luogu':
        tags = _luogu_tags(problem)
        if tags & LUOGU_TOPIC_TAGS.get(topic['id'], set()):
            return True
        # Hashing alone also covers numeric/set hashing; require string evidence.
        if topic['id'] == 'string-hash' and {2, 235} <= tags:
            return True
    text = (' '.join([str(problem.get('title', '')), *map(str, problem.get('tags', []))])).casefold()
    return any(re.search(r'(?<![a-z0-9])' + re.escape(keyword.casefold()) + r'(?![a-z0-9])', text)
               if keyword.isascii() else keyword.casefold() in text for keyword in topic['keywords'])


def is_template(problem, topic):
    if problem.get('id') in topic['template_ids']:
        return True
    title = str(problem.get('title', '')).casefold()
    return matches_topic(problem, topic) and ('【模板】' in title or '[template]' in title or title.startswith('template '))
