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
        ('network-flow', '网络流', '增广路、最大流与建模', ['maxflow', 'max flow', '网络流', '最大流'], ['luogu:P3376', 'atcoder:practice2_d']),
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
    'fft': 313, 'number theory': 72, 'sortings': 113,
    'dfs and similar': 4, 'shortest paths': 160, 'mst': 166,
    'bipartite': 187, 'hashing': 235, 'combinatorics': 252,
    'probabilities': 266, 'flows': 79, 'trees': 230,
    'matrices': 71, 'divide and conquer': 112, 'two pointers': 345,
    'ternary search': 67, 'constructive algorithms': 318,
    'brute force': 111, 'meet-in-the-middle': 380, 'bitmasks': 314,
    'chinese remainder theorem': 250, 'string suffix structures': 2,
}

# Language aliases name algorithms, not inferred solutions. Parent topics may
# include descendants; a broad platform tag must never establish a child topic.
# For example CF `dp` does not prove tree DP and `data structures` cannot prove
# Fenwick trees. These aliases also work for exact official task titles/tags on
# platforms which do not expose numeric Luogu tags.
_TOPIC_ALIASES = {
    3: ['dynamic programming', 'dp'], 5: ['math'],
    6: ['graphs', 'graph theory'], 8: ['geometry'], 13: ['game theory'],
    41: ["mo's algorithm", '莫队'], 42: ['segment tree'],
    45: ['binary search'], 47: ['dsu', 'disjoint set union', 'union find', 'union-find'],
    49: ['centroid decomposition'], 50: ['balanced binary search tree'],
    51: ['heap', 'priority queue'], 53: ['fenwick tree', 'binary indexed tree'],
    56: ['monotonic queue'], 67: ['ternary search'],
    71: ['matrix operations', 'matrices'], 72: ['number theory'],
    79: ['network flow', 'max flow', 'maxflow', 'maximum flow'],
    101: ['suffix automaton', 'sam'], 112: ['divide and conquer'],
    113: ['sortings', 'sorting'], 126: ['bfs', 'breadth first search', 'breadth-first search'],
    127: ['dfs', 'depth first search', 'depth-first search'],
    129: ['memoization', '记忆化'], 139: ['knapsack', '背包'],
    141: ['digit dp', '数位dp', '数位 dp'],
    144: ['interval dp', '区间dp', '区间动态规划'],
    150: ['convex hull trick'], 152: ['tree dp', '树形dp', '树形动态规划'],
    159: ['topological sort', 'topological sorting'],
    160: ['shortest path', 'shortest paths', 'dijkstra', 'bellman-ford'],
    166: ['minimum spanning tree', 'mst', 'kruskal', 'prim'],
    176: ['2-sat', 'two sat'], 179: ['scc', 'strongly connected components'],
    181: ['biconnected components'], 182: ['eulerian circuit', 'eulerian path'],
    185: ['difference constraints'], 187: ['bipartite graph', 'bipartite'],
    198: ['minimum cut', 'min cut'], 204: ['minimum cost flow', 'min cost flow'],
    211: ['lca', 'lowest common ancestor'], 228: ['heavy light decomposition', 'hld'],
    229: ['link cut tree', 'lct'], 230: ['trees'], 233: ['persistent segment tree'],
    235: ['hashing'], 241: ['gcd', 'greatest common divisor'],
    242: ['extended euclidean algorithm'], 250: ['chinese remainder theorem', 'crt'],
    251: ['mobius inversion', 'möbius inversion'], 252: ['combinatorics'],
    254: ['prefix sum', 'prefix sums'], 259: ['inclusion exclusion'],
    266: ['probability', 'probabilities'], 270: ['expected value', 'expectation'],
    271: ['linear algebra'], 272: ['matrix multiplication'], 274: ['gaussian elimination'],
    277: ['xor basis', 'linear basis'], 287: ['stack'], 288: ['queue'],
    290: ['sparse table', 'st表', 'st 表'], 291: ['convex hull'],
    300: ['trie'], 301: ['aho corasick', 'aho-corasick'],
    302: ['kmp', 'prefix function'], 303: ['suffix array'], 304: ['suffix tree'],
    313: ['fft', 'fast fourier transform'], 314: ['bitmask', 'bitmasks', 'bit manipulation'],
    318: ['constructive algorithms'], 324: ['ntt', 'number theoretic transform'],
    325: ['palindromic tree', 'eertree'], 329: ['manacher'],
    330: ['difference array'], 345: ['two pointers', 'two pointer', '双指针'],
    377: ['li chao tree'], 378: ['segment tree beats'],
    380: ['meet in the middle', 'meet-in-the-middle'], 385: ['monotonic stack'],
    444: ['linear dp', '线性dp', '线性动态规划'],
    464: ['subset dp', 'bitmask dp', '状压dp'],
    474: ['z function', 'z-function'], 475: ['linear sieve', 'sieve of eratosthenes'],
    476: ['floyd warshall', 'floyd-warshall'],
}


def _topic_text_terms(tag_ids):
    terms = []
    for tag_id in sorted(tag_ids):
        name = _TAGS[tag_id]['name'].lstrip('\ufeff')
        terms.append(name)
        # Official names often append an English acronym, e.g. 后缀自动机 SAM.
        # Preserve the Chinese algorithm name without requiring that suffix.
        if re.match(r'[\u4e00-\u9fff]', name):
            terms.append(re.split(r'\s+(?=[A-Za-z0-9])', name, maxsplit=1)[0])
        terms.extend(_TOPIC_ALIASES.get(tag_id, []))
        for alias, tags in LUOGU_TOPIC_TAGS.items():
            if tag_id in tags:
                terms.extend(TOPICS[alias]['keywords'])
    return list(dict.fromkeys(terms))


# Verified public implementation exercises from the official ACL practice
# contest (https://atcoder.jp/contests/practice2/tasks). These are algorithm
# library templates, not arbitrary ABC tasks relabelled by an AI.
_ATCODER_TEMPLATES = {
    'practice2_a': {47}, 'practice2_b': {53}, 'practice2_d': {79},
    'practice2_e': {204}, 'practice2_f': {324}, 'practice2_g': {179},
    'practice2_h': {176}, 'practice2_j': {42},
    'practice2_k': {42}, 'practice2_l': {42},
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
        templates.extend('atcoder:' + pid for pid, tags in _ATCODER_TEMPLATES.items()
                         if tags & inherited and 'atcoder:' + pid not in templates)
        topic = {'id': f'luogu-{tag_id}', 'title': path[-1],
                 'description': ' / '.join(path), 'path': path, 'depth': depth,
                 'parent_id': f"luogu-{row['parent']}" if row['parent'] is not None else None,
                 'keywords': [path[-1]] + sorted(tag for tag, target in _CF_TAG_TO_LUOGU.items() if target in inherited),
                 'source_url': _CATALOG['source_url'],
                 'source_version': _CATALOG['source_version']}
        TOPICS[topic['id']] = {**topic, 'template_ids': templates,
            'luogu_tags': inherited, 'official_exact': True,
            'text_keywords': _topic_text_terms(inherited),
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


def topic_evidence(problem, topic):
    """Return published metadata evidence, never an AI-invented algorithm tag.

    Missing/conflicting metadata stays unknown. In particular a bare DP or
    graph tag cannot certify one of its finer topics. Title/tag evidence is
    only a shortlist aid; selection reports still read the actual statement.
    """
    if problem.get('id') in topic['template_ids']:
        return {'matched': True, 'source': 'curated_official_template', 'terms': [problem['id']]}
    platform = problem.get('platform')
    if platform == 'luogu':
        numeric = _luogu_tags(problem)
        expected = topic.get('luogu_tags', LUOGU_TOPIC_TAGS.get(topic['id'], set()))
        common = numeric & expected
        if common:
            return {'matched': True, 'source': 'official_luogu_tag', 'terms': sorted(common)}
        if topic['id'] == 'string-hash' and {2, 235} <= numeric:
            return {'matched': True, 'source': 'official_luogu_tag', 'terms': [2, 235]}
        if topic.get('official_exact'):
            # On Luogu the platform-scoped numeric taxonomy is authoritative.
            # A suggestive title cannot override absent/conflicting official tags.
            return {'matched': False, 'source': 'unknown', 'terms': []}
    if platform == 'codeforces' and topic.get('official_exact'):
        common = {str(tag).casefold().strip() for tag in problem.get('tags', [])} & topic.get('cf_tags', set())
        if common:
            return {'matched': True, 'source': 'official_codeforces_tag', 'terms': sorted(common)}
    terms = _matching_text_terms(problem, topic.get('text_keywords', topic['keywords']))
    if terms:
        return {'matched': True, 'source': 'official_title_or_text_tag', 'terms': terms}
    return {'matched': False, 'source': 'unknown', 'terms': []}


def matches_topic(problem, topic):
    return topic_evidence(problem, topic)['matched']


def _matching_text_terms(problem, keywords):
    text_tags = [tag for tag in problem.get('tags', [])
                 if isinstance(tag, str) and not re.fullmatch(r'-?[0-9]+', tag)]
    # Match each published field independently: a title ending in `binary`
    # and an unrelated tag `search` must not manufacture `binary search`.
    texts = [re.sub(r'[-_–—]+', ' ', field.casefold())
             for field in [str(problem.get('title', '')), *text_tags]]
    # Normalize separators so union-find and union find are the same name.
    result = []
    for keyword in keywords:
        term = re.sub(r'[-_–—]+', ' ', keyword.casefold())
        found = any(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text)
                    if term.isascii() else term in text for text in texts)
        if found:
            result.append(keyword)
    return result


def _matches_text(problem, keywords):
    return bool(_matching_text_terms(problem, keywords))


def is_template(problem, topic):
    if problem.get('id') in topic['template_ids']:
        return True
    title = str(problem.get('title', '')).casefold()
    return matches_topic(problem, topic) and ('【模板】' in title or '[template]' in title or title.startswith('template '))
