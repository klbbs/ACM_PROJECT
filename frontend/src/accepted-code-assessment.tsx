const categories: Record<string, string> = {
  strategy: '算法思路', complexity: '时间与空间', code_quality: '代码风格', robustness: '健壮性',
}
type Report = {
  summary: string
  dimensions: {category:string; finding:string; evidence:string; suggestion:string; confidence:string}[]
  strengths: string[]
  alternative_approaches: string[]
  limitations: string[]
}
export function AcceptedCodeAssessment({report}: {report:Report}) {
  return <section className="problem-submission-review" aria-label="本次 AC 代码的 AI 评估">
    <strong>AC 代码评估 · AI 静态阅读建议</strong>
    <p className="muted">未运行代码，不等同官方评审；不会改变判题、签到或完成状态。</p>
    <p>{report.summary}</p>
    {report.dimensions.map((item, i) => <div key={i}>
      <b>{categories[item.category] || item.category}</b>
      <p>{item.finding}</p><p>依据：{item.evidence}</p><p>改进建议：{item.suggestion}</p>
      <small>把握：{({high:'较高',medium:'中等',low:'较低',uncertain:'无法确定'} as Record<string,string>)[item.confidence] || '无法确定'}</small>
    </div>)}
    {([['已有优点',report.strengths],['其他思路与权衡',report.alternative_approaches],['分析限制',report.limitations]] as [string,string[]][])
      .map(([title,items]) => items.length ? <div key={title}><b>{title}</b><ul>{items.map((s,i)=><li key={i}>{s}</li>)}</ul></div> : null)}
  </section>
}
