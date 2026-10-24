import {
  BarChart3,
  BookOpen,
  Code2,
  LayoutGrid,
  Layers3,
  Megaphone,
  Settings2,
  ShieldCheck,
  Star,
  Target,
  Trophy,
} from 'lucide-react'

export type Page =
  | 'home'
  | 'overview'
  | 'submissions'
  | 'records'
  | 'workbench'
  | 'practice'
  | 'settings'
  | 'announcements'
  | 'social'
  | 'community'
  | 'admin'
export const navigationGroups = [
  { id: 'training', name: '开始训练', description: '选好今天的题，进入工作台专心练习。' },
  { id: 'records', name: '记录与社交', description: '回看每次尝试，也和朋友一起进步。' },
  { id: 'manage', name: '连接与管理', description: '平台绑定、AI 配置和网站更新，集中在这里。' },
] as const
export const nav = [
  {
    id: 'home',
    name: '主要目录',
    icon: LayoutGrid,
    group: '',
    description: '所有功能的起点',
    keywords: '',
  },
  {
    id: 'practice',
    name: '训练',
    icon: Target,
    group: 'training',
    description: '每日训练与专项训练，模板起步、逐级进阶和 AI 评估。',
    keywords: '每日 专项 专题 模板 评估 对话 任务 推荐 题单 K签',
  },
  {
    id: 'workbench',
    name: '刷题工作台',
    icon: Code2,
    group: 'training',
    description: '读题、写代码、自测和小提示，关联官方提交结果。',
    keywords: '编辑器 Vim 编译 C++20 翻译 提示 交题',
  },
  {
    id: 'overview',
    name: '训练总览',
    icon: BarChart3,
    group: 'records',
    description: '查看训练趋势、知识点分布和 AI 训练建议。',
    keywords: '分析 统计 Rating',
  },
  {
    id: 'submissions',
    name: '提交记录',
    icon: Layers3,
    group: 'records',
    description: '筛选历史提交，回到工作台查看对应题目。',
    keywords: '历史 AC WA 错题',
  },
  {
    id: 'records',
    name: '收藏与备注',
    icon: Star,
    group: 'records',
    description: '收藏好题、记录个人备注，随时回到工作台复习。',
    keywords: '题目 笔记 记录 收藏 备注 复习',
  },
  {
    id: 'social',
    name: '排行榜与好友',
    icon: Trophy,
    group: 'records',
    description: '每日做题榜、个人主页、好友检索和 CF 对战。',
    keywords: '比赛 朋友 搜索 用户',
  },
  {
    id: 'settings',
    name: '连接与设置',
    icon: Settings2,
    group: 'manage',
    description: '绑定算法平台、安装 Cookie 油猴脚本、接入 AI 与调整训练偏好。',
    keywords: '教程 API Key 模型 登录 DeepSeek ChatGPT Tampermonkey 用户脚本 插件',
  },
  {
    id: 'community',
    name: '共享资料与成员',
    icon: BookOpen,
    group: 'manage',
    description: '按需分享学习资料，管理个人账号与密码。',
    keywords: '邀请 协作 安全',
  },
  {
    id: 'announcements',
    name: '更新公告',
    icon: Megaphone,
    group: 'manage',
    description: '了解这次更新了什么，随时回看历史公告。',
    keywords: '版本 新功能',
  },
  {
    id: 'admin',
    name: '管理后台',
    icon: ShieldCheck,
    group: 'manage',
    description: '管理成员、邀请码与共享译文审核。',
    keywords: '管理员 审核 邀请',
  },
] as const

export function availableNavigation(multiUser: boolean, role?: string) {
  return nav.filter(
    (item) =>
      (!['community', 'social'].includes(item.id) || multiUser) &&
      (item.id !== 'admin' || role === 'admin'),
  )
}
