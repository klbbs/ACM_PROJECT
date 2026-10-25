"""Link confirmed reading reports without allowing a report to replace a choice."""
from .codex_bridge import AIError


async def prepare_selection_reports(service, items, provider, model, context=None):
    if service is None or not callable(getattr(service, 'prepare', None)):
        raise AIError('选题阅读报告服务尚未配置，未生成题目；请联系管理员检查配置')
    prepared = await service.prepare([{**item, **(context or {})} for item in items], provider, model)
    if not isinstance(prepared, list) or len(prepared) != len(items):
        raise AIError('选题阅读报告与题目数量不一致，未保存题单，请重试')
    linked, report_ids = [], set()
    for original, report in zip(items, prepared):
        if not isinstance(report, dict) or any(report.get(key) != original.get(key)
                for key in ('id', 'problem_id', 'platform') if key in original):
            raise AIError('选题阅读报告与题目不匹配，未保存题单，请重试')
        report_id = report.get('ai_report_id')
        if not isinstance(report_id, str) or not report_id.strip() or len(report_id) > 128 or report_id in report_ids:
            raise AIError('选题阅读报告缺失或重复，未保存题单，请重试')
        report_ids.add(report_id)
        # Preserve validated catalog metadata and selector rationale. The report
        # service contributes its identifier, never a replacement problem.
        linked.append({**original, 'ai_report_id': report_id})
    return linked
