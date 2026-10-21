# Chromium 容器 seccomp 来源

基于 https://raw.githubusercontent.com/moby/profiles/main/seccomp/default.json ，2026-10-21 获取的原文 SHA256：

`6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`

Apache-2.0 许可原文保留在 chromium-seccomp.LICENSE。

本地仅新增两个无 capability 条件的 allow 规则：

1. clone / setns / unshare：沿用 [Playwright 沙箱说明](https://playwright.dev/python/docs/docker)中创建用户命名空间的要求。
2. chroot：外层容器 cap-drop ALL；允许 Chromium 在自身用户命名空间中建立隔离根目录，不赋予外层容器或非特权用户额外 capability。

保留 Moby 的 clone3 = SCMP_ACT_ERRNO / errnoRet 38（ENOSYS）处理，避免现代 glibc 将默认 EPERM 视为致命错误而不回退。来源：[Moby 问题及说明](https://github.com/moby/moby/issues/42680)。没有启用 clone3，也没有改成默认 allow。

早期采用的 Playwright 示例策略未覆盖当前 Chromium / glibc 环境；只读容器 CI 已发现 Crashpad 目录和 Zygote 启动问题，故先为每次浏览器分配独立可写XDG目录，再更新此策略。容器验收必须读取 chrome://sandbox 确认 PID namespace、Network namespace、Seccomp-BPF 均为 Yes；仅镜像构建成功不算验收。
