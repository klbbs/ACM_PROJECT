import React, { useEffect, useRef, useState } from 'react'
import { api } from './api'

type Sample = { input: string; output: string }
type Capabilities = {
  enabled: boolean
  languages: Record<string, { id: number; name: string }>
  message: string
}
type Result = {
  status_id: number
  status: string
  stdout: string
  stderr: string
  compile_output: string
  time: string
  memory: number | null
  matched: boolean | null
  truncated: boolean
}

export function SelfTestPanel({
  code,
  language,
  samples,
}: {
  code: string
  language: string
  samples: Sample[]
}) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [stdin, setStdin] = useState(''),
    [expected, setExpected] = useState('')
  const [compare, setCompare] = useState(false),
    [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [result, setResult] = useState<Result | null>(null)
  const alive = useRef(true)
  const fingerprint = JSON.stringify([code, language, stdin, compare ? expected : null])
  const current = useRef(fingerprint)
  current.current = fingerprint
  useEffect(() => {
    setResult(null)
  }, [fingerprint])
  useEffect(() => {
    alive.current = true
    api<Capabilities>('/workbench/self-test/capabilities')
      .then((v) => {
        if (alive.current) setCapabilities(v)
      })
      .catch((e) => {
        if (alive.current) setError(e.message)
      })
    return () => {
      alive.current = false
    }
  }, [])
  const run = async () => {
    const submitted = fingerprint
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const value = await api<Result>('/workbench/self-test/run', 'POST', {
        code,
        language,
        stdin,
        expected: compare ? expected : null,
        consent,
      })
      if (alive.current) {
        if (current.current === submitted) setResult(value)
        else setError('代码或自测输入已变更，请重新运行自测。')
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : '自测请求失败')
    } finally {
      if (alive.current) setBusy(false)
    }
  }
  const supported = !!capabilities?.languages[language]
  return (
    <details className="wb-selftest">
      <summary>代码自测 · 自定义输入</summary>
      <p className="muted">{capabilities?.message || '正在检查隔离自测服务…'}</p>
      {capabilities?.enabled && (
        <>
          <p className="muted">
            2 秒 CPU · 5 秒运行时限 · 128 MiB 内存 ·
            禁止网络。代码、输入和结果仅用于本次自测，不计入正式提交或签到。
          </p>
          <p>当前运行环境：{capabilities.languages[language]?.name || '不支持当前语言版本'}</p>
          {!!samples.length && (
            <label>
              填入题目样例{' '}
              <select
                aria-label="填入自测样例"
                defaultValue=""
                onChange={(e) => {
                  const sample = samples[Number(e.target.value)]
                  if (sample) {
                    setStdin(sample.input)
                    setExpected(sample.output)
                    setCompare(true)
                    setResult(null)
                  }
                }}
              >
                <option value="" disabled>
                  选择样例
                </option>
                {samples.map((_, i) => (
                  <option value={i} key={i}>
                    样例 {i + 1}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            自测输入
            <textarea
              aria-label="自测输入"
              rows={5}
              maxLength={65536}
              value={stdin}
              onChange={(e) => {
                setStdin(e.target.value)
                setResult(null)
              }}
              spellCheck={false}
            />
          </label>
          <label className="wb-consent">
            <input
              type="checkbox"
              checked={compare}
              onChange={(e) => setCompare(e.target.checked)}
            />{' '}
            对照预期输出（忽略行尾空白与末尾空行）
          </label>
          {compare && (
            <label>
              预期输出
              <textarea
                aria-label="自测预期输出"
                rows={4}
                maxLength={65536}
                value={expected}
                onChange={(e) => {
                  setExpected(e.target.value)
                  setResult(null)
                }}
                spellCheck={false}
              />
            </label>
          )}
          <label className="wb-consent">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />{' '}
            我同意将本次代码和输入发送到管理员配置的隔离自测服务
          </label>
          <button
            className="secondary"
            disabled={busy || !supported || !consent || !code.trim()}
            onClick={run}
          >
            {busy ? '隔离运行中…' : '运行自测'}
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      {result && (
        <div className="wb-selftest-result" aria-live="polite">
          <p>
            <strong>{result.status_id === 3 ? '运行完成' : result.status}</strong>
            {result.matched !== null && (
              <span> · {result.matched ? '与预期输出一致' : '与预期输出不一致'}</span>
            )}
          </p>
          <p className="muted">
            用时 {result.time || '—'} 秒 · 内存 {result.memory ?? '—'} KiB
          </p>
          <h4>标准输出</h4>
          <pre>{result.stdout || '（无输出）'}</pre>
          {result.stderr && (
            <>
              <h4>标准错误</h4>
              <pre>{result.stderr}</pre>
            </>
          )}
          {result.compile_output && (
            <>
              <h4>编译信息</h4>
              <pre>{result.compile_output}</pre>
            </>
          )}
          {result.truncated && <p>输出达到 64 KiB 显示上限。</p>}
          <p className="muted">自测通过不能证明所有测试点正确，请继续在原平台提交。</p>
        </div>
      )}
    </details>
  )
}
