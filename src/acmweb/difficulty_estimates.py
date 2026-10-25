"""Transparent training estimates, never official cross-platform equivalence."""
import json
import math

ATCODER_MODELS = 'https://kenkoooo.com/atcoder/resources/problem-models.json'
ATCODER_POINTS = 'https://kenkoooo.com/atcoder/resources/merged-problems.json'
LUOGU_LEVELS = 'https://help.luogu.com.cn/manual/luogu/problem/difficulty'
CF_RATINGS = 'https://codeforces.com/apiHelp/objects#Problem'

# Application-owned conservative orientation bands using Luogu's ordered
# official levels. This is explicitly a heuristic, not a measured conversion
# between common contestants and never described as an official equivalence.
LUOGU_BANDS = {1: (800, 1200), 2: (900, 1500), 3: (1100, 1800),
               4: (1400, 2200), 5: (1600, 2500), 6: (1900, 2900), 7: (2300, 3300), 8: (2700, 3500)}
LUOGU_LABELS = {1: '入门', 2: '普及-', 3: '普及', 4: '普及+/提高-',
                5: '提高', 6: '提高+/省选-', 7: '省选/NOI-', 8: 'NOI/NOI+/CTS'}


def cf_reference(database):
    with database.connect() as db:
        rows = db.execute("SELECT data FROM problem_catalog WHERE platform='codeforces'").fetchall()
    ratings = []
    for row in rows:
        try:
            data = json.loads(row[0])
        except (ValueError, TypeError):
            continue
        value = data.get('difficulty') if isinstance(data, dict) else None
        if type(value) is int and 800 <= value <= 4000:
            ratings.append(value)
    return sorted(ratings)


def estimate(problem, reference=()):
    platform = problem.get('platform')
    base = {'kind': 'unknown', 'minimum': None, 'maximum': None,
            'confidence': 'unknown', 'method': 'unknown', 'source_urls': [],
            'notice': '暂无可核查的跨平台难度依据，不能确认满足 CF rating 范围。'}
    rating = problem.get('difficulty')
    if platform == 'codeforces' and type(rating) is int and rating > 0:
        return {**base, 'kind': 'official', 'minimum': rating, 'maximum': rating,
                'confidence': 'official', 'method': 'codeforces_official_rating',
                'source_urls': [CF_RATINGS], 'notice': 'Codeforces 官方题目 rating。'}
    native = problem.get('native_difficulty')
    if platform == 'luogu' and type(native) is int and native in LUOGU_BANDS:
        low, high = LUOGU_BANDS[native]
        return {**base, 'kind': 'estimate', 'minimum': low, 'maximum': high,
                'confidence': 'low', 'method': 'luogu_ordered_level_heuristic_v1',
                'source_urls': [LUOGU_LEVELS], 'native_value': native,
                'native_label': LUOGU_LABELS[native],
                'notice': '参考洛谷官方等级说明的人工启发式宽区间；官方等级自身含主观判断且会调整，未做共同选手标定，不是官方等价。按区间重叠筛选，实际难度可能偏离。'}
    percentile = problem.get('atcoder_difficulty_percentile')
    if (platform == 'atcoder' and problem.get('atcoder_statistics_source') == ATCODER_MODELS
            and type(percentile) in (int, float) and math.isfinite(percentile) and 0 <= percentile <= 1
            and type(problem.get('atcoder_statistics_population')) is int
            and problem['atcoder_statistics_population'] >= 100 and len(reference) >= 100):
        # Align empirical task-distribution quantiles, with a wide uncertainty
        # band. Different platform task populations do not imply equal skills.
        center = max(800, min(3500, reference[round(percentile * (len(reference) - 1))]))
        low, high = max(800, center - 400), min(3500, center + 400)
        return {**base, 'kind': 'estimate', 'minimum': low, 'maximum': high,
                'confidence': 'low', 'method': 'task_distribution_quantile_v1',
                'source_urls': [ATCODER_MODELS, CF_RATINGS],
                'native_value': problem.get('atcoder_problem_difficulty'),
                'reference_count': len(reference), 'statistics_count': problem['atcoder_statistics_population'],
                'notice': 'AtCoder Problems 非官方估计 difficulty 与本地 CF 官方题库按题目分位对齐，范围 ±400；不是共同选手标定或官方等价，实际难度可能偏离。'}
    points = problem.get('atcoder_points')
    if (platform == 'atcoder' and problem.get('atcoder_points_source') == ATCODER_POINTS
            and str(problem.get('problem_id', '')).startswith('abc')
            and type(points) in (int, float) and math.isfinite(points) and 0 < points <= 1000):
        center = 800 if points <= 100 else 1000 if points <= 200 else 1300 if points <= 300 else 1600 if points <= 400 else 2000 if points <= 500 else 2400 if points <= 600 else 2900
        return {**base, 'kind': 'estimate', 'minimum': max(800, center - 500), 'maximum': min(3500, center + 500),
                'confidence': 'low', 'method': 'abc_public_points_heuristic_v1',
                'source_urls': [ATCODER_POINTS], 'native_value': points,
                'notice': '参考公开聚合的 ABC 赛题分值作启发式宽区间；分值不等于 rating，也不是官方难度换算。'}
    if platform == 'atcoder':
        base['notice'] = '缺少 AtCoder Problems 有效统计/ABC 赛题分值或至少 100 道 CF 参考题；请同步题库。不能用题号字母冒充难度。'
    return base


def overlaps(evidence, minimum, maximum):
    return (evidence.get('kind') != 'unknown' and type(evidence.get('minimum')) is int
            and type(evidence.get('maximum')) is int
            and evidence['maximum'] >= minimum and evidence['minimum'] <= maximum)
