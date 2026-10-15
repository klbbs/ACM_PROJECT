import json
from pathlib import Path
from urllib.parse import quote

import pytest

from ojcrawler.adapters.atcoder import AtCoder
from ojcrawler.adapters.codeforces import Codeforces
from ojcrawler.adapters.luogu import Luogu, page_data
from ojcrawler.adapters.nowcoder import Nowcoder, parse_submissions
from ojcrawler.models import CrawlError

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def next(self, url, params, headers):
        self.calls.append((url, params, headers))
        result = next(self.values)
        if isinstance(result, Exception):
            raise result
        return result

    def json(self, url, params=None, headers=None):
        return self.next(url, params, headers)

    def get(self, url, params=None, headers=None):
        return self.next(url, params, headers)


def cf_row(sid, timestamp, status="OK"):
    return {"id": sid, "contestId": 1, "creationTimeSeconds": timestamp, "verdict": status,
            "problem": {"contestId": 1, "index": "A", "name": "Test", "rating": 800, "tags": ["math"]}}


def ok(value):
    return {"status": "OK", "result": value}


def test_cf_pagination_and_inclusive_cutoff():
    client = FakeClient([ok([{"handle": "test"}]), ok([]), ok([cf_row(3, 200), cf_row(2, 100)]), ok([cf_row(1, 99)])])
    out = Codeforces(client, "test", page_size=2).collect(100)
    assert out.complete
    assert [s.id for s in out.submissions] == ["3", "2"]
    assert out.submissions[0].problem["tags"] == ["math"]
    assert client.calls[-1][1]["from"] == 3


def test_cf_cap_and_bad_account():
    out = Codeforces(FakeClient([ok([{}]), ok([]), ok([cf_row(1, 100)])]), "a", page_size=1, max_pages=1).collect()
    assert not out.complete and len(out.submissions) == 1 and "max_pages" in out.error
    out = Codeforces(FakeClient([{"status": "FAILED"}]), "a").collect()
    assert not out.complete and "FAILED" in out.error


AT_PROFILE = '<div id="main-container"><table class="dl-table"><tr><th>Rating</th><td>1000</td></tr></table></div>'


def at_row(sid, ts):
    return {"id": sid, "epoch_second": ts, "problem_id": "abc001_1", "contest_id": "abc001", "result": "AC", "language": "C++"}


def test_atcoder_boundary_is_replayed():
    first = [at_row(i, 100 + i // 2) for i in range(500)]
    second = [first[-1], at_row(500, 349), at_row(501, 350)]
    client = FakeClient([[], AT_PROFILE, first, second])
    out = AtCoder(client, "a").collect(100)
    assert out.complete and len(out.submissions) == 502
    assert client.calls[-1][1]["from_second"] == 349


def test_atcoder_timestamp_saturation_fails():
    out = AtCoder(FakeClient([[], AT_PROFILE, [at_row(i, 100) for i in range(500)]]), "a").collect(100)
    assert not out.complete and "500" in out.error


def test_atcoder_profile_not_found_is_not_empty_success():
    out = AtCoder(FakeClient([[], '<html>404</html>']), "a").collect()
    assert not out.complete


def test_atcoder_real_api_fixture():
    rows = json.loads((FIXTURES / "atcoder-submissions.json").read_text())
    out = AtCoder(FakeClient([[], AT_PROFILE, rows]), "tourist").collect()
    assert out.complete and len(out.submissions) == 2
    assert out.submissions[0].id == str(rows[0]["id"])
    assert out.submissions[0].problem_id == rows[0]["problem_id"]
    assert out.submissions[0].submitted_at == rows[0]["epoch_second"]


def test_luogu_all_state_formats_and_challenge():
    data = {"user": {"uid": 1}}
    assert page_data(json.dumps({"currentData": data})) == data
    assert page_data('<script id="lentille-context" type="application/json">' + json.dumps({"data": data}) + '</script>') == data
    assert page_data('window._feInjection = JSON.parse(decodeURIComponent("' + quote(json.dumps({"currentData": data})) + '"));') == data
    with pytest.raises(CrawlError):
        page_data('<html>Please verify you are human</html>')


def test_luogu_pagination_and_units():
    row = {"id": 1, "submitTime": 123, "status": 12, "language": 0, "score": 100, "memory": 64,
           "problem": {"pid": "P1000", "title": "Test", "difficulty": 1}}
    client = FakeClient([json.dumps({"user": {"uid": 1}}), json.dumps({"records": {"result": [row], "count": 1, "perPage": 20}})])
    out = Luogu(client, "1").collect()
    assert out.complete and out.submissions[0].verdict == "AC"
    assert out.submissions[0].memory_bytes == 65536 and out.submissions[0].language == "0"


def test_luogu_real_profile_and_record_auth_failure():
    profile = (FIXTURES / "luogu-user.json").read_text()
    out = Luogu(FakeClient([profile, CrawlError("HTTP 401")]), "1").collect()
    assert not out.complete and out.profile["name"] == "kkksc03"
    assert out.rating_history[0]["contest_id"] == "107343"
    assert out.error == "HTTP 401"


def test_nowcoder_live_page_fixture_and_timezone():
    rows, next_page = parse_submissions((FIXTURES / "nowcoder-practice.html").read_text())
    assert len(rows) == 10 and next_page == 2
    assert rows[0].id == "49530848" and rows[0].verdict == "AC"
    assert rows[0].submitted_at == 1637669366
    assert rows[0].memory_bytes == 476 * 1024
    assert rows[4].verdict == "WA"


def test_nowcoder_error_page_rejected():
    with pytest.raises(CrawlError):
        parse_submissions('<html>服务器错误</html>')


def test_nowcoder_empty_and_cap():
    profile = '<div class="profile-menu"></div>'
    empty = profile + '<div class="my-state-main"></div><table><thead><tr><th>提交时间</th></tr></thead><tbody></tbody></table>'
    out = Nowcoder(FakeClient([profile, {"code": 0, "data": []}, empty]), "1").collect()
    assert out.complete and out.submissions == []
    fixture = (FIXTURES / "nowcoder-practice.html").read_text()
    out = Nowcoder(FakeClient([profile, {"code": 0, "data": []}, fixture]), "1", max_pages=1).collect()
    assert not out.complete and len(out.submissions) == 10


def test_optional_rating_failure_is_visible():
    out = Codeforces(FakeClient([ok([{}]), CrawlError("blocked"), ok([])]), "a").collect()
    assert out.complete and any("rating_history" in w for w in out.warnings)


def test_normalized_rating_fields():
    row = {"contestId": 1, "ratingUpdateTimeSeconds": 123, "oldRating": 1000, "newRating": 1100, "rank": 5}
    out = Codeforces(FakeClient([ok([{}]), ok([row]), ok([])]), "a").collect()
    assert out.rating_history[0]["new_rating"] == 1100
    assert out.rating_history[0]["rated_at"] == 123
    ar = {"ContestScreenName": "abc001.contest.atcoder.jp", "EndTime": "2026-10-14T09:00:00+09:00", "IsRated": True, "NewRating": 1000}
    out = AtCoder(FakeClient([[ar, {"IsRated": False}], AT_PROFILE, []]), "a").collect()
    assert len(out.rating_history) == 1
    assert out.rating_history[0]["contest_id"] == "abc001"
    assert out.rating_history[0]["rated_at"] == 1791936000


def test_malformed_optional_atcoder_history_is_warning():
    out = AtCoder(FakeClient([[{"EndTime": "invalid"}], AT_PROFILE, []]), "a").collect()
    assert out.complete and out.rating_history == [] and out.warnings
