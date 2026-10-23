"""Read-only startup checks for features that may be unavailable or incomplete."""
import logging
import os
import shutil
from pathlib import Path

from .codex_bridge import AIError

log = logging.getLogger('acmweb.startup')
PLATFORMS = ('codeforces', 'atcoder', 'luogu', 'nowcoder')
REMOTE_SUBMISSION_PLATFORMS = ('codeforces', 'atcoder')


def _finding(level, feature, detail):
    return {'level': level, 'feature': feature, 'detail': detail}


def _accounts(accounts):
    try:
        return accounts.public()['items'], None
    except Exception:
        return [], '账号配置无法读取；请检查配置文件格式和读取权限。'


def _single_findings(settings, accounts, ai, selftest, codex):
    findings = []
    configured, account_error = _accounts(accounts)
    if account_error:
        findings.append(_finding('warning', '平台账号', account_error))
    by_platform = {name: next((row for row in configured if row.get('platform') == name), None)
                   for name in PLATFORMS}
    if not configured and not account_error:
        findings.append(_finding('warning', '平台账号',
            '尚未绑定任何平台；提交记录、个人化选题和采集需要先在“连接与设置”添加账号。'))
    elif configured:
        missing = [name for name, row in by_platform.items() if row is None]
        if missing:
            findings.append(_finding('warning', '未绑定的平台',
                '尚未配置：%s；对应平台数据不会进入本账号的采集和选题。' % ', '.join(missing)))
    for platform, row in by_platform.items():
        if row is None:
            continue
        cookie = row.get('cookie_info') or {}
        if cookie.get('status') == 'invalid':
            findings.append(_finding('warning', platform,
                '已保存凭据格式无效；登录受限功能及远程提交可能不可用，请重新保存并检测登录。'))
        elif cookie.get('status') == 'valid':
            findings.append(_finding('info', platform,
                '已保存 Cookie 且格式通过；启动时未连接平台验证会话，请在设置页执行“检测登录”。'))
        elif platform in REMOTE_SUBMISSION_PLATFORMS:
            findings.append(_finding('warning', platform,
                '账号未配置 Cookie；公开资料可能可用，但登录检测和工作台远程提交不可用。'))
        else:
            findings.append(_finding('info', platform,
                '账号未配置 Cookie；公开资料仍可同步，登录受限资料可能不完整。该平台暂不支持工作台代提交。'))
    remote_ready = any(by_platform[name] and
                       (by_platform[name].get('cookie_info') or {}).get('status') == 'valid'
                       for name in REMOTE_SUBMISSION_PLATFORMS)
    if remote_ready:
        findings.append(_finding('info', '工作台远程提交',
            'CF / AtCoder 至少有一项凭据格式可用；平台是否仍登录须在设置页检测。洛谷和牛客使用官方页面提交。'))
    elif not account_error:
        findings.append(_finding('warning', '工作台远程提交',
            'CF / AtCoder 远程提交尚无格式有效的账号 Cookie；洛谷和牛客不支持网站代提交。'))

    try:
        ai_config = ai.public_config()
        api_ready = bool(ai_config.get('has_key') and ai_config.get('model'))
    except Exception:
        ai_config, api_ready = {}, False
        findings.append(_finding('warning', 'AI API',
            'AI 配置无法读取；请在“连接与设置 → AI API 接入”检查提供方配置。'))
    codex_enabled = bool(getattr(codex, 'enabled', False))
    codex_installed = codex_enabled and bool(shutil.which(getattr(codex, 'binary', 'codex')))
    if api_ready:
        findings.append(_finding('info', 'AI API',
            '已配置 API Key 和模型；启动时未发送连接测试，请在设置页主动测试。'))
    elif not any(item['feature'] == 'AI API' and item['level'] == 'warning' for item in findings):
        alternative = 'Codex 命令可用，但账号登录状态尚未检查。' if codex_installed else 'Codex 通道也未就绪。'
        findings.append(_finding('warning', 'AI 分析与选题',
            'AI API 尚未同时配置 Key 和模型；请完成 API 设置并测试，或检查官方账号通道。 ' + alternative))
    if codex_enabled and not codex_installed:
        findings.append(_finding('warning', 'Codex 通道',
            '已启用但找不到 Codex CLI；请安装 CLI，或改用 AI API Key。'))
    elif codex_installed:
        findings.append(_finding('info', 'Codex 通道',
            'CLI 已安装且通道已启用；当前账号是否登录仅能在设置页检测。'))

    if getattr(selftest, 'enabled', False):
        findings.append(_finding('info', '代码自测',
            '已配置隔离运行服务地址；启动时未连接测试，实际可用语言以工作台能力检查为准。'))
    else:
        findings.append(_finding('warning', '代码自测',
            '未配置 ACM_SELFTEST_URL，自测功能不可用；可配置专用 Judge0，代码不会在网站主机执行。'))

    google = bool(os.getenv('ACM_GOOGLE_TRANSLATE_KEY'))
    bing = bool(os.getenv('ACM_BING_TRANSLATE_KEY'))
    if google or bing:
        services = ', '.join(name for name, ready in (('Google', google), ('Bing', bing)) if ready)
        if bing and not os.getenv('ACM_BING_TRANSLATE_REGION'):
            findings.append(_finding('warning', '普通翻译 API',
                f'{services} Key 已设置，但缺少 ACM_BING_TRANSLATE_REGION；Bing 请求可能失败。'))
        else:
            findings.append(_finding('info', '普通翻译 API',
                f'{services} 翻译已配置；未发起网络测试，AI 翻译使用上方 AI 通道。'))
    else:
        findings.append(_finding('info', '普通翻译 API',
            '未配置 Google/Bing 服务 Key；可使用网页翻译，AI 翻译需先配置 AI 通道。'))

    if (Path(settings.static_dir) / 'index.html').is_file():
        findings.append(_finding('info', '网页前端', '已找到构建后的前端页面。'))
    else:
        findings.append(_finding('warning', '网页前端',
            '未找到 frontend/dist/index.html；请构建前端，或从包含前端资源的发布包启动。'))
    return findings


def _multiuser_findings(settings, identities, workspaces):
    findings = []
    registered = identities.count()
    platform_counts = {name: 0 for name in PLATFORMS}
    invalid_credentials = 0
    account_config_errors = 0
    remote_ready = 0
    remote_accounts = 0
    ai_ready = 0
    loaded = 0
    for app, _lifecycle in workspaces.apps.values():
        loaded += 1
        accounts, error = _accounts(app.state.account_config)
        if error:
            account_config_errors += 1
            continue
        by_platform = {row.get('platform'): row for row in accounts}
        for name, row in by_platform.items():
            if name not in platform_counts:
                continue
            platform_counts[name] += 1
            if (row.get('cookie_info') or {}).get('status') == 'invalid':
                invalid_credentials += 1
            if name in REMOTE_SUBMISSION_PLATFORMS:
                remote_accounts += 1
                if (row.get('cookie_info') or {}).get('status') == 'valid':
                    remote_ready += 1
        try:
            config = app.state.ai.public_config()
            ai_ready += int(bool(config.get('has_key') and config.get('model')))
        except Exception:
            pass
    configured_platforms = ', '.join(f'{name} {count}人' for name, count in platform_counts.items() if count)
    if configured_platforms:
        findings.append(_finding('info', '平台账号', f'已加载用户空间中有平台绑定：{configured_platforms}；未逐一显示用户名。'))
    elif registered:
        findings.append(_finding('warning', '平台账号',
            f'已加载的 {loaded} 个用户空间没有平台绑定；其他用户登录后才会读取配置，请在“连接与设置”添加账号。'))
    else:
        findings.append(_finding('warning', '平台账号', '尚未初始化管理员和用户，平台绑定状态要在用户注册后配置。'))
    if invalid_credentials:
        findings.append(_finding('warning', '平台凭据',
            f'{invalid_credentials} 项已保存凭据格式异常；请用户在设置页重新保存并检测登录。'))
    if account_config_errors:
        findings.append(_finding('warning', '平台账号配置',
            f'{account_config_errors} 个用户空间的账号配置无法读取；请检查配置文件格式和权限。'))
    if remote_accounts and remote_ready == remote_accounts:
        findings.append(_finding('info', '工作台远程提交',
            f'{remote_ready} 个 CF / AtCoder 账号的 Cookie 格式通过；启动时未验证平台会话。洛谷和牛客使用官方页面提交。'))
    elif remote_ready:
        findings.append(_finding('warning', '工作台远程提交',
            f'{remote_ready}/{remote_accounts} 个已绑定的 CF / AtCoder 账号 Cookie 格式通过；其余账号不能远程提交，仍需检测实际登录。'))
    else:
        findings.append(_finding('warning', '工作台远程提交',
            '当前没有格式有效的 CF / AtCoder Cookie；远程提交不可用。洛谷和牛客不支持网站代提交。'))
    if loaded and ai_ready == loaded:
        findings.append(_finding('info', 'AI API',
            f'{ai_ready}/{loaded} 个已加载用户空间配置了 Key 和模型；未对外发送连接测试。'))
    elif ai_ready:
        findings.append(_finding('warning', 'AI API',
            f'{ai_ready}/{loaded} 个已加载用户空间配置了 Key 和模型；其他已加载用户可能尚未完成 AI 设置。'))
    else:
        findings.append(_finding('warning', 'AI 分析与选题',
            '已加载用户空间尚无完整的 AI Key + 模型配置；用户登录后需自行配置并测试。'))
    if settings.server_codex:
        if shutil.which(settings.codex_binary):
            findings.append(_finding('info', 'Codex 通道',
                '服务器 Codex 已启用且 CLI 存在；各用户授权状态未在启动时检查。'))
        else:
            findings.append(_finding('warning', 'Codex 通道',
                '已启用服务器 Codex，但找不到 CLI；请安装 CLI，或关闭该通道。'))
    else:
        findings.append(_finding('info', 'Codex 通道', '服务器 Codex 未启用；用户仍可配置自己的 AI API。'))
    selftest_url = os.getenv('ACM_SELFTEST_URL', '')
    findings.append(_finding('info' if selftest_url else 'warning', '代码自测',
        '已配置隔离服务地址，启动时未连接测试。' if selftest_url else
        '未配置 ACM_SELFTEST_URL；代码自测不可用，用户可在部署中接入专用 Judge0。'))
    google = bool(os.getenv('ACM_GOOGLE_TRANSLATE_KEY'))
    bing = bool(os.getenv('ACM_BING_TRANSLATE_KEY'))
    if google or bing:
        findings.append(_finding('info', '普通翻译 API',
            'Google/Bing 至少一项已配置；未发起网络测试，网页翻译入口仍可用。'))
    else:
        findings.append(_finding('info', '普通翻译 API',
            '未配置 Google/Bing Key；可使用网页翻译，AI 翻译需用户配置 AI 通道。'))
    if not loaded and registered:
        findings.append(_finding('info', '用户配置检查',
            f'{registered} 个账号中没有已有工作空间数据可检查；登录后会读取各自配置。'))
    if (Path(settings.static_dir) / 'index.html').is_file():
        findings.append(_finding('info', '网页前端', '已找到构建后的前端页面。'))
    else:
        findings.append(_finding('warning', '网页前端', '未找到 frontend/dist/index.html；请构建前端或使用完整发布包。'))
    return findings


def emit(findings, *, mode):
    attention = sum(item['level'] == 'warning' for item in findings)
    log.info('启动配置巡检（%s）：%s 项需要留意，%s 项为状态说明。',
             mode, attention, len(findings) - attention)
    for item in findings:
        message = '%s：%s' % (item['feature'], item['detail'])
        if item['level'] == 'warning':
            log.warning(message)
        else:
            log.info(message)


def report_single(app):
    findings = _single_findings(app.state.settings, app.state.account_config,
                                app.state.ai, app.state.selftest, app.state.codex)
    emit(findings, mode='个人')
    return findings


def report_multiuser(settings, identities, workspaces):
    findings = _multiuser_findings(settings, identities, workspaces)
    emit(findings, mode='多人')
    return findings
