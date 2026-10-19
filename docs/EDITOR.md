# 工作台编辑器

使用 CodeMirror 6、官方 C++ / Python / Java 语言扩展、One Dark 主题以及 Replit Vim 扩展。运行时依赖的完整许可和版本保存在 `frontend/public/third-party-notices.txt`，发布后可通过 `/third-party-notices.txt` 查看。

## 补全与快捷键

- 支持 C++17/20、Python3、Java17 的高亮和本地补全。候选标明「语言关键字」「常用名称 · 非类型推断」「基础语法片段」「当前文件单词」。不会根据题目生成答案，也不会把源码发送给补全服务。
- 输入时自动显示候选，`Ctrl+Space` 手动触发，方向键选择、Tab 接受、Esc 关闭；Enter 关闭候选并换行。无候选时 Tab 缩进四空格；在语法片段内优先跳转占位符。
- `Ctrl/Cmd+S` 保存草稿，`Ctrl/Cmd+F` 搜索，`Ctrl+H` 打开替换，支持撤销重做、折叠、括号匹配与闭合。候选不是类型推断；本版本没有语言服务器、编译诊断或 AI 续写。
- 编辑器可独立全屏；使用按钮退出全屏，Esc 留给 Vim 和补全。代码上限仍为 64 KiB UTF-8，超限输入/粘贴整体拒绝，已有代码保留。中文输入在组合结束后同步，不截断候选文本。

## Vim 与用户偏好

默认普通模式，可切换 Vim。支持 Normal / Insert / Visual、常用移动与编辑、宏和搜索，底部显示模式与命令状态。仅 Insert 模式提供补全。

`:w` 调用与保存按钮相同的异步保存流程。`:q`、`:wq`、`:x` 不退出网站，也不隐式声称已保存。切换模式、字号、换行与语言不重建编辑器；切换题目建立独立撤销历史。

`GET/PUT /api/workbench/editor-settings` 保存当前用户偏好：`keymap`（standard / vim）、`font_size`（整数 12–24，默认 14）、`line_wrapping`（布尔值，默认 false）。服务端沿用每用户训练数据库和 CSRF；未配置时直接返回默认值，无需迁移旧数据。偏好保存失败会显示错误并保持此前设置，不在 localStorage 跨账号共享。

编辑器和语言资源随部署包提供，无需 CDN。若增强编辑器加载失败，可使用「启用基础文本框」继续编辑、保存和下载，也可主动切换基础编辑器。重新载入页面可重试失败的资源加载；未保存代码请先保存或下载。

## 开发验证

```bash
cd frontend
npm ci
npm test
npm run build
npx playwright install chromium
# 项目 Python 环境需已安装后端依赖；本地使用对应虚拟环境：
ACM_TEST_PYTHON=../.venv/bin/python npm run test:e2e
```

浏览器测试只启动绑定 127.0.0.1 的临时合成数据服务，不读取真实账号、不调用 AI、不向算法平台提交代码。CI 执行同样的组件、浏览器及容器检查。
