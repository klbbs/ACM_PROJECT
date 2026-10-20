"""Release notes are shipped with code; read state stays in each user's database."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

RELEASES = [
    {'id': '0.14.1', 'date': '2026-10-20', 'title': '新的主要目录，让功能更好找', 'items': [
        '登录后先进入主要目录，按开始训练、记录与社交、连接与管理整理入口；支持搜索功能和首次使用路线。',
        '侧栏同步分组，始终保留主要目录入口；按账号权限展示管理与协作功能，手机及收起侧栏也可使用。',
    ]},
    {'id': '0.14.0', 'date': '2026-10-20', 'title': '绑定平台更轻松，更新内容看得见', 'items': [
        '新增浏览器 Cookie 助手下载：支持 Chrome / Edge / Firefox，点击读取当前官方平台并复制，回到本站粘贴后保存、检测。',
        '平台绑定教程与连接设置合并；插件安装、F12 手动获取、账号配置和登录检测可在同一处完成。',
        '新增更新公告栏：登录后提示未读更新，已读状态跟随账号保存，并可随时查看历史公告。',
        '插件只在用户主动点击并授权后读取当前平台，不自动上传。复制成功不代表服务器已登录；目前仍需前往官方页面交题。',
    ]},
    {'id': '0.13.1', 'date': '2026-10-20', 'title': '保留平台资料与训练总览', 'items': [
        '对战核验只合并官方提交，保留平台资料、Rating、历史快照和采集进度，修复训练总览被空资料覆盖的问题。',
    ]},
    {'id': '0.13.0', 'date': '2026-10-20', 'title': '一起训练：好友、榜单与对战', 'items': [
        'AI 从所有已验证登录平台混合选题，遵守用户设置的题目发布日期下限。',
        '可选择开启判题错误后的自动小提示，默认关闭，只引导思考，不提供完整答案。',
        '新增每日做题榜、个人主页、用户检索、好友申请和 Codeforces 对战；对战不抵扣每日任务。',
        '调整为清爽绿色和轻动效，支持系统减少动画设置。',
    ]},
]


class MarkRead(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ids: list[str] = Field(min_length=1, max_length=50)


def register(app, database):
    @app.get('/api/announcements')
    def announcements():
        read = set(database.get('announcements.read', []))
        return {'items': RELEASES, 'unread_ids': [r['id'] for r in RELEASES if r['id'] not in read]}

    @app.post('/api/announcements/read')
    def mark_read(payload: MarkRead):
        allowed = {r['id'] for r in RELEASES}
        if not set(payload.ids) <= allowed:
            raise HTTPException(422, '公告版本不存在')
        # Merge atomically: concurrent devices may read different release notes.
        import json
        with database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM web_settings WHERE key='announcements.read'").fetchone()
            read = set(json.loads(row[0]) if row else []) | set(payload.ids)
            db.execute("INSERT INTO web_settings VALUES('announcements.read',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(sorted(read)),))
        return {'ok': True, 'unread_ids': [r['id'] for r in RELEASES if r['id'] not in read]}
