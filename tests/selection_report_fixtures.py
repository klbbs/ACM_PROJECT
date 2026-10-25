"""Explicit synthetic report links for tests of other selection components.

These fixtures never fetch a statement or claim a real AI has read a problem.
"""


class SyntheticSelectionReports:
    def __init__(self):
        self.calls = []

    async def prepare(self, items, provider, model):
        self.calls.append(([dict(item) for item in items], provider, model))
        return [{**item, 'ai_report_id': 'synthetic-report:' + str(item.get('id') or item['problem_id'])}
                for item in items]


def with_selection_reports(service):
    service.selection_reports = SyntheticSelectionReports()
    return service
