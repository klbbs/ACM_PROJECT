import React, { useState } from 'react'
import { ArrowRight, Search } from 'lucide-react'
import { availableNavigation, navigationGroups, type Page } from './navigation'
import './main-directory.css'

export function MainDirectory({
  multiUser,
  role,
  username,
  unreadCount,
  onNavigate,
  onGuide,
}: {
  multiUser: boolean
  role?: string
  username: string
  unreadCount: number
  onNavigate: (page: Page) => void
  onGuide: () => void
}) {
  const [query, setQuery] = useState('')
  const normalized = query.trim().toLocaleLowerCase()
  const available = availableNavigation(multiUser, role).filter((item) => item.id !== 'home')
  const filtered = available.filter((item) =>
    `${item.name} ${item.description} ${item.keywords}`.toLocaleLowerCase().includes(normalized),
  )
  return (
    <div className="main-directory">
      <header className="directory-hero">
        <span className="eyebrow">YOUR ALGORITHM WORKSPACE</span>
        <h1>{username}，今天从哪里开始？</h1>
        <p>先选题，再练习，最后回看。所有工具都在这份主要目录里。</p>
        <div className="directory-quick-start">
          <button className="primary" onClick={() => onNavigate('practice')}>
            开始今日练习 <ArrowRight size={16} />
          </button>
          <button className="secondary" onClick={() => onNavigate('workbench')}>
            进入刷题工作台
          </button>
        </div>
      </header>
      <section className="directory-onboarding" aria-label="首次使用路线">
        <strong>第一次来？</strong>
        <span>① 绑定平台并检测登录</span>
        <span>② 设置题量与 AI</span>
        <span>③ 选题、练习、同步结果</span>
        <button className="text-button" onClick={onGuide}>
          查看绑定与油猴教程 <ArrowRight size={14} />
        </button>
      </section>
      <div className="directory-search">
        <Search size={18} />
        <input
          aria-label="搜索功能"
          placeholder="搜索功能：Cookie、翻译、对战、自测…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          maxLength={100}
        />
        {query && (
          <button className="text-button" onClick={() => setQuery('')}>
            清除搜索
          </button>
        )}
      </div>
      {normalized && (
        <p className="directory-search-result" role="status">
          找到 {filtered.length} 个功能
        </p>
      )}
      {navigationGroups.map((group) => {
        const items = filtered.filter((item) => item.group === group.id)
        if (!items.length) return null
        return (
          <section className="directory-group" aria-label={group.name} key={group.id}>
            <div className="directory-group-heading">
              <h2>{group.name}</h2>
              <p>{group.description}</p>
            </div>
            <div className="directory-cards">
              {items.map((item) => (
                <button
                  className={`directory-card directory-${group.id}`}
                  key={item.id}
                  aria-label={`打开${item.name}`}
                  onClick={() => onNavigate(item.id)}
                >
                  <span className="directory-icon">
                    <item.icon size={23} />
                  </span>
                  <span className="directory-card-content">
                    <strong>
                      {item.name}
                      {item.id === 'announcements' && unreadCount > 0 && (
                        <small className="directory-unread">{unreadCount} 条未读</small>
                      )}
                    </strong>
                    <span>{item.description}</span>
                  </span>
                  <ArrowRight className="directory-arrow" size={17} />
                </button>
              ))}
            </div>
          </section>
        )
      })}
      {!filtered.length && (
        <div className="directory-empty">
          <h2>没有找到这个功能</h2>
          <p>试试「题目」「AI」「Cookie」或「提交」。</p>
          <button className="secondary" onClick={() => setQuery('')}>
            查看全部功能
          </button>
        </div>
      )}
      <p className="directory-note">
        测试版 · 全程由 GPT 开发。官方交题仍在平台页面完成；AI 提示用于引导思考。
      </p>
    </div>
  )
}
