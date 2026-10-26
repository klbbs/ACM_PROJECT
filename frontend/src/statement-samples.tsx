import React, { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'

type Sample = { input?: string; output?: string }

function SampleBlock({ title, value }: { title: string; value: string }) {
  const [status, setStatus] = useState<'idle' | 'copying' | 'copied' | 'failed'>('idle')
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  async function copy() {
    setStatus('copying')
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value)
      } else {
        // Support ordinary HTTP installations where Clipboard API is absent.
        const previous = document.activeElement as HTMLElement | null
        const field = document.createElement('textarea')
        field.value = value
        field.setAttribute('readonly', '')
        field.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0'
        document.body.append(field)
        try {
          field.focus({ preventScroll: true })
          field.select()
          if (!document.execCommand('copy')) throw new Error('Copy unavailable')
        } finally {
          field.remove()
          previous?.focus({ preventScroll: true })
        }
      }
      if (mounted.current) setStatus('copied')
    } catch {
      if (mounted.current) setStatus('failed')
    }
  }
  return (
    <div className="wb-sample-block">
      <div className="wb-sample-heading">
        <h4>{title}</h4>
        <button type="button" className="secondary" aria-label={`复制${title}`}
          disabled={status === 'copying'} onClick={() => void copy()}>
          {status === 'copied' ? <Check size={14} /> : <Copy size={14} />}
          {status === 'copied' ? '已复制' : status === 'copying' ? '复制中…' : '复制'}
        </button>
      </div>
      <pre>{value}</pre>
      <span className="wb-sample-copy-status" role="status">
        {status === 'failed' ? '复制失败，请选中样例手动复制，或再次点击重试。' : status === 'copied' ? `${title}已复制` : ''}
      </span>
    </div>
  )
}

export function StatementSamples({ samples = [] }: { samples?: Sample[] }) {
  return <>{samples.map((sample, index) => (
    <div className="wb-samples" key={index}>
      <SampleBlock key={`input:${sample.input}`} title={`样例输入 ${index + 1}`} value={sample.input ?? ''} />
      <SampleBlock key={`output:${sample.output}`} title={`样例输出 ${index + 1}`} value={sample.output ?? ''} />
    </div>
  ))}</>
}
