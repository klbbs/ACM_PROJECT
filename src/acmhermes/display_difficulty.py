"""Presentation of a delivered problem's difficulty, using its frozen evidence."""
from decimal import Decimal, ROUND_HALF_UP

from .difficulty import CF_RATING_SOURCE

POLICY = 'hermes_display_v1'
WEIGHTS = {'final_ai_report': 100, 'contest_statistics': 55, 'cf_official': 45}


def display_estimate(problem, assessment):
    # Only invoked after validating the final SolutionReport and admission score.
    # Never include the short review or the already blended prefilter estimate.
    evidence = problem['score_evidence']
    components = []

    def include(key, label, rating, source):
        components.append({'key': key, 'label': label, 'rating': rating,
                           'weight': WEIGHTS[key], 'source': source})

    include('final_ai_report', 'AI 报告', assessment['rating'], 'report.difficulty_assessment')
    if problem.get('platform') == 'codeforces':
        stats = problem.get('contest_statistics') or {}
        raw = evidence.get('evidence') or {}
        # The scorer has already validated sample size, time, scope and identity.
        # Require explicit completeness, exact task identity and a known unit too.
        if (evidence.get('confidence') == 'medium' and evidence.get('empirical_rating') is not None
                and stats.get('complete') is True and raw.get('participant_unit') in {'person', 'team'}
                and problem['id'] == f"codeforces:{stats.get('contest_id')}-{stats.get('problem_index')}"
                and raw.get('source')):
            include('contest_statistics', '比赛统计', evidence['empirical_rating'], raw['source'])
        if evidence.get('official_rating') is not None:
            include('cf_official', 'CF 官方', evidence['official_rating'], CF_RATING_SOURCE)

    total = sum(c['weight'] for c in components)
    values = [Decimal(str(c['rating'])) for c in components]
    weighted = sum(value * c['weight'] for value, c in zip(values, components))
    rating = int((weighted / total).to_integral_value(rounding=ROUND_HALF_UP))
    terms = [f"{c['label']} {format(v.normalize(), 'f')}×{c['weight']}" for v, c in zip(values, components)]
    formula = '(' + ' + '.join(terms) + f') ÷ {total}'
    return {'policy': POLICY, 'rating': rating, 'components': components, 'weight_sum': total,
            'rounding': 'half_up_integer', 'display_text': f'预估难度 {rating} 分（{formula}，四舍五入）'}
