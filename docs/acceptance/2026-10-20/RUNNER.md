# 隔离自测服务升级验收（2026-10-20）

根因：Judge0 配置将 `MAX_MAX_FILE_SIZE` 也设置成 64 KiB；编译阶段使用此全局值，导致 cc1plus 生成中间文件时被终止。修复为编译阶段 20480 KiB，运行阶段维持 64 KiB，应用输出/HTTP传输限制继续独立保留。

专用服务已切换 `judge0:1.13.1-cpp20-final`（GCC 12.2.0）；网站宿主不运行用户代码。基础候选与语言补丁 Dockerfile 是本次运维记录，依赖服务器原有 Judge0 cgroup2 定制基础镜像，并非完整首次安装教程。构建使用 Debian 签名密钥验证软件包；Debian 10 到 12 的 libcrypt 迁移通过独立引导目录完成，保留 Ruby 2.7 原生库兼容性。

语言 ID 100 固定为 C++20/GCC12，包含 `-std=c++20 -O2`；旧 ID 54 保留。种子改为按 ID 更新/插入，避免 stock 每次启动删除整个语言表。server/workers 均在运行，网站容器保持 healthy。

真实 Judge0 请求结果见 [runner-cpp20.jsonl](runner-cpp20.jsonl)：

- concepts、ranges::sort、views::filter 组合编译运行，输出 `4 8`，Accepted。
- 旧 C++17 iostream 程序输出 `42`，Accepted。
- 输出超限：64 KiB 后被隔离器终止，状态 NZEC，stderr 明确 File size limit exceeded（这是预期运行限制，不是编译器错误）。
- 无限循环：Time Limit Exceeded。
- Python socket：输出 BLOCKED，隔离网络仍被禁止。

配置与完整数据库备份在服务器 `~/judge0-upgrade-20261020/`（私有文件权限 600），原镜像保留。回滚时恢复原 compose/config 并重建 server/workers；将新增 ID 100 标为 archived，保留其已有关联，不删除提交记录。勿将数据库或含认证令牌的配置放入公开仓库。

额外应用层验证：直接调用线上网站的 `SelfTester.run`，capabilities 将 cpp20 映射至 ID 100；`<bits/stdc++.h>` + ranges::sort 编译执行，匹配输出 `1`，见 [app-selftester.json](app-selftester.json)。其 `memory` 240784 KiB 高于运行限额，疑为 cgroup 计数包含编译峰值，不能解释成程序运行时使用了该额度。

因此另行实际核验运行内存隔离：程序构建 `std::vector<char>(256*1024*1024,42)` 分配并初始化 256 MiB（实际触页）；程序在输出 ALLOCATED 前被 Killed/NZEC，结果内存恰为 131072 KiB，见 [runner-memory-limit.json](runner-memory-limit.json)。证实运行阶段 128 MiB 约束仍执行。Judge0/cgroup2 对编译峰值的展示语义仍需进一步调查，当前不能将所有 memory 数字作为纯运行时峰值。
