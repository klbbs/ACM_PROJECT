import React, { useEffect, useState } from 'react'
import { api } from './api'
import { type CodeTemplate, type TemplateDocument, templateLanguageNames } from './template-types'
import './editor-templates.css'

const emptyTemplate = (): CodeTemplate => ({
  id:
    globalThis.crypto?.randomUUID?.() ||
    `template-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  title: '',
  trigger: '',
  language: 'all',
  body: '',
})

export function EditorTemplates({ onChanged }: { onChanged: (items: CodeTemplate[]) => void }) {
  const [document, setDocument] = useState<TemplateDocument>({ revision: 0, items: [] })
  const [form, setForm] = useState<CodeTemplate>(emptyTemplate)
  const [ready, setReady] = useState(false),
    [busy, setBusy] = useState(true)
  const [error, setError] = useState(''),
    [status, setStatus] = useState(''),
    [conflict, setConflict] = useState(false)
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    let alive = true
    api<TemplateDocument>('/workbench/code-templates')
      .then((value) => {
        if (!alive) return
        setDocument(value)
        onChanged(value.items)
        setReady(true)
      })
      .catch((e) => {
        if (alive) setError(e.message)
      })
      .finally(() => {
        if (alive) setBusy(false)
      })
    return () => {
      alive = false
    }
  }, [onChanged])

  const reload = async () => {
    setBusy(true)
    setError('')
    try {
      const value = await api<TemplateDocument>('/workbench/code-templates')
      setDocument(value)
      onChanged(value.items)
      setReady(true)
      setConflict(false)
      setStatus('已读取服务器最新版本；当前编辑表单已保留，请确认后再保存。')
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const persist = async (items: CodeTemplate[], reset: boolean) => {
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const value = await api<TemplateDocument>('/workbench/code-templates', 'PUT', {
        revision: document.revision,
        items,
      })
      setDocument(value)
      onChanged(value.items)
      setConflict(false)
      setStatus('模板已保存，仅当前账号可见。')
      if (reset) {
        setForm(emptyTemplate())
        setEditing(false)
      }
    } catch (e: any) {
      setError(e.message)
      setConflict(e.status === 409)
    } finally {
      setBusy(false)
    }
  }

  const save = () => {
    if (
      !form.title.trim() ||
      !form.body.trim() ||
      !/^[\p{L}_][\p{L}\p{N}_-]{0,39}$/u.test(form.trigger)
    ) {
      setError(
        '填写标题、模板代码和触发词；触发词以字母、中文或下划线开头，仅含字母、中文、数字、下划线和连字符（最多 40 字）。',
      )
      return
    }
    if (new TextEncoder().encode(form.body).byteLength > 16384) {
      setError('单个模板代码最多 16 KiB UTF-8，请缩短模板。')
      return
    }
    const items = document.items.some((item) => item.id === form.id)
      ? document.items.map((item) => (item.id === form.id ? form : item))
      : [...document.items, form]
    void persist(items, true)
  }

  return (
    <details className="editor-templates">
      <summary>
        我的代码模板{' '}
        <span className="muted">
          {ready ? `${document.items.length} / 40` : error ? '读取失败' : '正在读取'}
        </span>
      </summary>
      <p className="muted">
        输入自定义触发词后按 Tab 补全；Enter 仍然换行。模板仅保存在当前账号，不会发送给
        AI，也不会执行。相同触发词的个人模板优先于内置补全。
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {status && <p role="status">{status}</p>}
      {(!ready || conflict) && (
        <button className="secondary" disabled={busy} onClick={() => void reload()}>
          {conflict ? '读取最新版本（保留当前表单）' : '重试读取模板'}
        </button>
      )}
      {ready && (
        <>
          <ul className="editor-template-list">
            {document.items.map((item) => (
              <li key={item.id}>
                <div>
                  <strong>{item.title}</strong>
                  <code>{item.trigger}</code>
                  <span className="muted">{templateLanguageNames[item.language]}</span>
                </div>
                <div>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() => {
                      setForm({ ...item })
                      setEditing(true)
                      setError('')
                      setStatus('')
                    }}
                  >
                    编辑 {item.title}
                  </button>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() =>
                      void persist(
                        document.items.filter((row) => row.id !== item.id),
                        form.id === item.id,
                      )
                    }
                  >
                    删除 {item.title}
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {document.items.length === 0 && (
            <p className="muted">还没有个人模板；内置补全继续可用。</p>
          )}
          <fieldset disabled={busy || conflict}>
            <legend>{editing ? '编辑模板' : '添加模板'}</legend>
            <div className="editor-template-fields">
              <label>
                模板标题
                <input
                  aria-label="模板标题"
                  value={form.title}
                  maxLength={80}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </label>
              <label>
                触发词
                <input
                  aria-label="模板触发词"
                  value={form.trigger}
                  maxLength={40}
                  placeholder="例如：树状数组 / fenwick"
                  onChange={(e) => setForm({ ...form, trigger: e.target.value })}
                />
              </label>
              <label>
                适用语言
                <select
                  aria-label="模板适用语言"
                  value={form.language}
                  onChange={(e) =>
                    setForm({ ...form, language: e.target.value as CodeTemplate['language'] })
                  }
                >
                  {Object.entries(templateLanguageNames).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              模板代码
              <textarea
                aria-label="模板代码"
                value={form.body}
                maxLength={8192}
                spellCheck={false}
                rows={8}
                onChange={(e) => setForm({ ...form, body: e.target.value })}
              />
            </label>
            <p className="muted">
              支持 {'${name}'} 占位符、{'${1:默认值}'} 编号和 {'${}'} 光标位置；Tab / Shift+Tab
              跳转，同名占位符联动。若需字面量占位符，写成 {'$\\{name\\}'}。每个模板最多 8192 字符 /
              16 KiB，合计最多 128 KiB。
            </p>
            <div className="editor-template-actions">
              <button
                className="primary"
                disabled={busy || (!editing && document.items.length >= 40)}
                onClick={save}
              >
                {busy ? '保存中…' : editing ? '保存模板修改' : '添加并保存模板'}
              </button>
              {editing && (
                <button
                  className="secondary"
                  onClick={() => {
                    setForm(emptyTemplate())
                    setEditing(false)
                    setError('')
                  }}
                >
                  新建其他模板
                </button>
              )}
            </div>
          </fieldset>
        </>
      )}
    </details>
  )
}
