# 只更换住宅代理，保留现有路由

适用于住宅出口需要更换、套餐迁移或代理账号/入口变化。**只替换 `residential` SOCKS 出站的地址、端口、账号和密码**。保留标签、所有路由（含顺序、禁用状态、额外匹配条件）、其他出站、入站和客户端节点凭据。不重新安装、不生成新节点链接，也不主动让用户重新选择域名。

## 1. 准备与范围

复用已验证的 SSH 访问方式，私钥仍只由本机原生 SSH 使用。只有“更换”授权才应用；用户只要求测试新代理时只运行 test。

向用户索取缺失的新 SOCKS5 主机/IP、端口、账号和密码。优先让用户在其本地交互终端运行：

```text
python <skill>/scripts/collect_credentials.py --proxy-only --output RUNTIME_DIR/new-proxy-credentials.json
```

输出结构：`{"proxy":{"host":"proxy.example.com","port":12345,"username":"USER","password":"PASSWORD"}}`。也接受已有部署输入中的 proxy 字段，不需要再提供服务器 IP。用户已提供资料时可直接生成受保护输入，不要求重复输入。

原生 SSH/SFTP 上传到服务器后，以 `sudo install -m 600` 放到 `/root/new-proxy-credentials.json` 并确保 root 所有。凭据不放命令行参数、日志、Git 仓库或 Skill 文件夹。交付完成后清理运行目录的凭据副本，或按用户明确要求保留。

把更新后的整个 `scripts/` 上传到服务器私有工作目录，至少需要同版本的 `replace_proxy.py`、`proxyctl.py`、`common.py`。**已有服务器只更新维护工具，不运行 install.py/install.sh。**

## 2. 读取版本与单独预检

```bash
sudo python3 /REMOTE_SKILL/scripts/replace_proxy.py status
sudo python3 /REMOTE_SKILL/scripts/replace_proxy.py test --input /root/new-proxy-credentials.json
```

status 只返回配置 revision 和标签，不列域名或凭据。test 用新连接信息启动临时 Xray 客户端，监听回环上的临时端口，经过新 SOCKS 出站访问 `https://ipinfo.io/ip`。凭据只进入 600 的临时配置，子进程参数不含密码；无论成功或失败都清理临时客户端和文件。预检不保存、重启或备份正在使用的面板配置。

商家明确承诺某个静态 IP 时，可加 `--expected-exit-ip EXPECTED_IP`，不一致则停止。连通和出口测试不能证明 IP 的住宅属性、独享或信誉评分。

工具只自动处理唯一 `residential` SOCKS 出站、其中一个服务器和至多一个账号。多个服务器/账号或协议不同先说明并确认应操作的对象，不自行删除多余配置。

## 3. 替换与回滚

使用刚读取的 revision：

```bash
sudo python3 /REMOTE_SKILL/scripts/replace_proxy.py replace --input /root/new-proxy-credentials.json --expect-revision REVISION
```

可选 `--expected-exit-ip` 与 test 相同。replace 会**再次预检**，避免之前的测试结果已失效；通过后才备份和修改。

具体行为：

1. 配置 revision 不一致则停止；与本 Skill 的域名增删共用维护锁。
2. 新代理预检失败或出口与用户指定值不一致，当前配置完全不动。
3. 备份原 Xray 模板和在线 SQLite 快照到服务器 `/var/backups/ubuntu-residential-proxy/routes-*`，目录 700、文件 600。
4. 再次检查并发变更，保存替换后的连接信息。完整 routing 对象保持相同，其他配置保留。
5. 应用时重启 Xray，代理可能短暂断连；验证保存结果、运行规则、新连接信息及原有双节点连通。
6. 必须观察到 Reality、Hysteria2 分别报告自测成功，不能只根据进程退出码判断两个节点均已验证。如果能确定原规则按优先级、无额外匹配限制地将 ipinfo.io 送往住宅出口，额外比较 Reality 节点观察的出口与预检出口；不一致视为失败。Hysteria2 同时进行连通性自测，但这不等于它的出口也已单独测量。
7. 如果原规则未配置 ipinfo.io、它被前序规则覆盖，或存在无法确定的域名集合/附加条件，**不为测试添加或调整规则**。结果明确标记“无经节点出口比对”，只报告新代理预检、运行配置及双节点连通的证据；若用户要求更强的端到端证据，使用原规则中已覆盖的 IP 查询站或路由命中诊断。
8. 保存、应用或验证失败时，自动恢复旧模板、重启并验证旧运行配置与双节点。回滚检查不拿旧出口去与新出口比较。无法确认恢复时报告备份路径及具体状态，不宣称已恢复健康；旧代理本身可能仍有信誉/套餐问题。

账号和入口完全没变、只是商家替换背后出口时，工具预检并验证现有配置，返回 changed=false，不做多余的保存或重启。外部商家更换出口的动作无法由服务器配置回滚。

## 4. 向用户反馈

报告是否实际修改、新代理实测出口 IP、路由保留结果、备份位置、验证范围。失败时报告是否恢复原配置。不要输出代理密码、面板 Token 或全部原始配置；不需要用户重新导入节点。不要把“配置替换成功”说成“IP 已变干净”。
