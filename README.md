# Ubuntu 住宅代理运维 Skill

给 Agent 使用的完整流程：从全新 Ubuntu VPS、本机 SSH 访问和已有住宅 SOCKS5 代理开始，部署 **VLESS + Reality / Hysteria2 双节点**，验证后交付可复制的节点链接与 Clash 配置。后续支持住宅分流域名增删，以及**只更换住宅代理、保留全部路由规则和节点信息**。

首次部署以配置交付结束，不主动发起域名选择问答。SSH 私钥只留在用户本机，由原生 OpenSSH 使用；首次密码在用户自己的终端输入。仓库不保存用户的服务器信息或代理凭据。

## 安装 Skill

```bash
npx skills add baihejiangnan/ubuntu-residential-proxy-skill --skill ubuntu-residential-proxy
```

或将 `skills/ubuntu-residential-proxy` 整个文件夹复制到 `~/.codex/skills/`。本机需要 Python 3、OpenSSH；服务器工具使用 Ubuntu 自带 Python 3 和标准库。

对 Agent 说：

```text
使用 $ubuntu-residential-proxy，帮我从全新 Ubuntu 云服务器部署 Reality 与 Hysteria2，并接入我的住宅 SOCKS5 代理。SSH 私钥只在本机使用，完成后给我两个节点链接和 Clash 配置。
```

后续可以说“列出走住宅出口的域名，让我选一个删除”或“把 https://example.com 加入住宅分流”。

也可以说：

```text
使用 $ubuntu-residential-proxy，只更换住宅代理，保留现有路由规则。先测试新代理，通过后备份、替换并验证；失败时回滚。
```

更换工具先通过临时回环客户端测试新 SOCKS 连接和出口，失败不改现有配置；通过后再做受保护备份、持久化及节点验证，失败自动恢复。凭据通过文件传入，不进入命令行参数。完整流程见 [住宅代理替换](skills/ubuntu-residential-proxy/references/replace-proxy.md)。

## 服务器运行包

Release 提供完整 Skill ZIP、服务器 TAR.GZ 以及 SHA-256 清单。根目录 `install.sh` 下载固定 release 并校验散列，再执行 Agent 安装适配器；输入是服务器上 root 所有、权限 600 的 JSON 文件。先按 Skill 完成 SSH 和输入准备，不把密码放到 shell 命令参数。

```bash
curl -fL https://raw.githubusercontent.com/baihejiangnan/ubuntu-residential-proxy-skill/v0.2.0/install.sh -o /root/proxyops-install.sh
bash /root/proxyops-install.sh --input /root/proxyops-input.json
```

安装器只接受全新 Ubuntu。已有面板时拒绝运行；增删域名使用 `proxyctl.py`，更换住宅代理使用 `replace_proxy.py`，都不会重装。安装需联网获取 Ubuntu 软件包和上游 3x-ui/Xray，打包上游脚本不代表完全离线安装。两个节点通过服务器自测后，还需要用户网络验证外部可达性。

## 上游来源与修改

`Finderlzy/reality-setup` **v1.2.1**（MIT，commit `56a544def5985a80d31980037071ae59b745c5e5`）的完整文件快照位于 Skill 的 `vendor/reality-setup/`。复制时移除了 `.git`，保留原许可证、作者署名与来源，不继承上游提交历史。Agent 适配在运行时生成临时脚本，原始上游文件保持原样，差异见 [upstream.md](skills/ubuntu-residential-proxy/references/upstream.md)。

## 开发验证

```bash
python -m unittest discover -s tests -v
python tools/package_release.py
```

单元与模拟 API 测试不连接生产服务器、不执行系统安装。发布前另外做 Bash 语法、Skill 元数据和泄密检查。真实全新 Ubuntu 的完整部署验收需专门测试机；不会拿已有节点的服务器做重装测试。
