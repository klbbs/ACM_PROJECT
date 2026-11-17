# 四平台提交统计

题库条目的 `submission_statistics` 与平台原生 `brief_statistics`、CF比赛难度证据 `contest_statistics` 分开保存。前端工作台及推荐卡片会显示提交次数、统计窗口和通过率单位；Hermes结果保留该对象，AI报告提示词明确禁止混用人数与次数。

## 真实来源与口径

| 平台 | 提交次数 | 通过率 | 来源 |
| --- | --- | --- | --- |
| CF | 正式比赛榜单的计分尝试次数，计至首次通过；不是全站全部重交次数 | 通过参赛者／尝试参赛者 | 官方 `contest.standings` |
| AtCoder | 榜单每位参赛者本题 `Count` 求和 | 完整通过参赛者／尝试参赛者 | 官方 `/contests/{id}/standings/json` |
| 洛谷 | 全站累计 `totalSubmit` | `totalAccepted / totalSubmit` | 官方题目列表 |
| 牛客 | 官方比赛问题列表 `submitCount` | `acceptedCount / submitPersonCount`，与官方比赛页“通过率”列一致 | 官方 `/acm/contest/problem-list` |

CF和AtCoder的榜单、牛客的比赛页面不是全站累计记录，不能将其标为全站累计通过率。AtCoder/牛客的通过人数不是AC提交次数，不用于除以提交次数；不会编造不存在的 `accepted_count`。零分母时通过率为null，而不是0%。已知为团队比赛时标明队伍单位。

AtCoder保留官方 `Fixed` 标志原值；旧比赛可能为false但仍返回完整榜单。结果是读取时的快照，不因此宣称官方榜单已固定。待判、冻结、截断、重复身份、错题号和错误比赛数据不作为完整统计写入。部分分数不会在有已知满分时算作完整通过。

牛客从本人登录会话能访问的题目AC提交页发现“相关比赛”，只读取固定官方统计接口并按 `problemId` 严格匹配已有题库，丢弃页面中的他人账号和提交信息。重复赛若样本更少，不会覆盖已有更有信息量的原赛快照。接口返回的单题零分母不会阻断其他题目。

## 数据字段

```json
{
  "submitted_count": 1303,
  "count_unit": "submission",
  "count_kind": "all_contest_submissions",
  "scope": "contest",
  "accepted_participants": 972,
  "attempted_participants": 988,
  "acceptance_rate": 0.9838056680161943,
  "rate_unit": "participant",
  "source": "https://ac.nowcoder.com/acm/contest/problem-list",
  "contest_id": 90070,
  "observed_at": 1794909600
}
```

上例展示真实官方页面核对过的字段口径；统计会随上游变化。`rate_unit=submission` 时才表示AC提交次数占比，`rate_unit=participant` 表示参赛者通过占比。`count_kind=scoring_attempts_until_first_solve` 对应CF计分尝试，不冒称完整提交总数。未知值保留null/缺失，不补零。

## 同步及补抓

网页“同步平台题库”会自动附带有界统计补抓，复用已保存的本人平台会话；AtCoder和牛客无需另外提交密码。扫码登录保存的有效会话与手动Cookie会话均可使用。统计失败不会清空题目、标签、已有统计或训练任务。

- 每次同步保留新统计，后续补抓跳过近期已处理的题目。
- 真实请求按现有主机限速串行发送；官方限流或访问失败停止该来源，保留旧数据。
- 来源缺失、无可用关联比赛或无有效分母时保持未知，不声称全题库字段都完整。
- 不调用AI，不读取提交源码，不代交题，不运行用户代码，不影响签到。

运维可明确指定某位用户已有目录进行批量补抓：

```bash
.venv/bin/python scripts/sync_submission_statistics.py \
  --database data/users/USER_ID/accounts.sqlite3 \
  --accounts data/users/USER_ID/accounts.web.toml \
  --target 1000 --request-budget 800
```

`--platform` 可重复传入，目标是每平台具有有效提交数及通过率的题目数量，默认1000；请求预算是每平台上限。达到目标后结束；预算耗尽或来源不可读时如实返回未达标。命令不初始化数据库、不改正在运行的应用任务状态，也不输出账号凭据。不同用户的统计保存在各自题库中，沿用现有认证、CSRF、限流及隔离。
