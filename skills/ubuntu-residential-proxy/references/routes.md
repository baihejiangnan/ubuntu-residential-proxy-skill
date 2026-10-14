# 售后：住宅分流域名

在用户请求列出、添加或删除时使用。本流程不安装面板、不轮换节点凭据。

## 列出供用户选择

将 `scripts/common.py` 与 `scripts/proxyctl.py` 安全上传到服务器运行目录（或使用已上传 Skill），执行：

```bash
sudo python3 /REMOTE_SKILL/scripts/proxyctl.py list
```

输出只有配置 revision 和域名条目，含 number、rule_index、entry、enabled、kind，不含住宅凭据。展示编号、原始条目、范围和是否启用。`geosite:anthropic` 是域名集合，删除会影响整个集合；不要假装它是一个域名。若用户只说“删一个”，等其选择；收到具体编号后把编号解析为**该次列表**的原始条目和 rule_index。

## 添加

将网址规范化为域名匹配。`domain:example.com` 覆盖本域及子域；`full:api.example.com` 仅匹配完整主机名；不匹配 URL 路径。helper 默认把普通域名/HTTP(S) URL 规范化为 domain:，显式 full: 保留。不会自动扩大到整个顶级域或添加全部关联网站。

读取最新列表取得 revision 后执行：

```bash
sudo python3 /REMOTE_SKILL/scripts/proxyctl.py add 'https://example.com/path' --expect-revision REVISION
```

多个住宅域名规则时传 `--rule-index INDEX`。字段含额外 source/port/inbound 等条件时先说明条件是否适用于目标需求，别误认为只加域名就必然生效。禁用的规则工具拒绝直接添加，先按用户要求选择合适规则。

如果之前删除了全部域名，列表为空但 residential 出站仍存在，添加时带 `--create-rule`。工具创建含明确域名的窄规则，放在内部 API 规则之后；不会创建全流量住宅规则。已有域名规则时不能使用该选项。

## 删除

使用原始条目，不能把 geosite 集合当作单个 URL 规范化：

```bash
sudo python3 /REMOTE_SKILL/scripts/proxyctl.py remove 'domain:example.com' --rule-index INDEX --expect-revision REVISION
```

同一规则多个条目可一次传入。revision 不匹配时重新读取并核对原选择，编号可能已经变化；不能按旧编号盲删。最后一个域名删除后 helper 删除该条规则，避免留下空 domain 的全流量匹配规则。

## 保存、备份与验收

helper 在服务器回环地址调用面板 POST API，先备份 Xray 模板和在线 SQLite 快照到 `/var/backups/ubuntu-residential-proxy/routes-*`（目录 700、文件 600），再持久化、重启并验证运行路由及 node-selftest。验证失败自动还原并验证旧配置；还原无法确认时清楚报告备份路径与实际状态，不宣称恢复成功。

helper 的 verified 字段是配置/运行/节点自测验证，**不等于目标域名实际走住宅出口的端到端验证**。目标是 IP 查询站时，观察其真实取 IP 的请求主机，用临时客户端经过两个节点访问该接口，比较它看到的 IP 与住宅出口，普通站点应仍走 VPS。临时客户端只监听回环、临时配置 600，结束后停止并删除。

例如 Net.Coffee 当前可从 `https://ip.net.coffee/cdn-cgi/trace` 的 ip= 字段读取出口；接口可能变化，使用前检查网站实际请求。不把手动输入一个 IP 后返回的资料当作当前出口证据。普通业务网站无法自行显示出口时，用路由命中测试/日志和连通性说明证据范围。

最终报告具体增加/删除项目、备份位置、应用结果和验证范围。维护现有服务器不要求重新导入客户端节点。
