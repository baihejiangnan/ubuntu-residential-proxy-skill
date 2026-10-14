# 首次部署

目标是**全新 Ubuntu**、两种代理均可用、住宅出口验证、最后交付客户端导入信息。不要把只提供命令或仅完成 Reality 当作任务完成。

## 1. 最小资料与本地访问

确认服务器公网 IPv4、SSH 端口、登录用户（云镜像可能是 ubuntu 而非 root）、现有本地密钥路径，以及住宅 SOCKS5 主机/端口/账号/密码。只追问缺失的资料；不收取 SSH 私钥内容。不擅自固定用户的服务器 IP、住宅出口或操作系统路径。

若已有密钥，先独立验证：

```text
python <skill>/scripts/ssh_access.py verify --host SERVER_IP --user ubuntu --port 22 --key LOCAL_PRIVATE_KEY
```

若尚未生成/安装公钥，在用户原生交互终端运行：

```text
python <skill>/scripts/ssh_access.py bootstrap --host SERVER_IP --user ubuntu --key LOCAL_PRIVATE_KEY --status-file RUNTIME_DIR/ssh-status.json
```

- 工具仅创建缺失的密钥，不覆盖已有私钥。默认由 ssh-keygen 询问密钥口令，口令仅在本地终端输入。
- 用户选择独立的无人值守密钥时可用 `--no-passphrase`；否则使用本机 ssh-agent 并让用户在其终端执行 `ssh-add LOCAL_PRIVATE_KEY`。不要代填口令或打印私钥。
- Windows 可用 PowerShell `Start-Process` 打开用户可见终端运行此命令（需用户输入的交互窗口可以可见）。参数必须逐项正确引用；状态文件放运行目录而非仓库。macOS/Linux 使用用户自己的终端，不用密码写入脚本、sshpass 或环境变量。
- 未知主机在该用户终端核对指纹；有云控制台指纹时传 `--host-fingerprint SHA256:...`。已有记录不自动覆盖。
- bootstrap 通过原生 ssh 提示一次服务器密码，追加公钥后再以 BatchMode 做独立密钥验证。非 root 用户还需 sudo 权限；sudo 需要密码时让用户在原生终端完成提权，不把密码送进日志。
- 已有云厂商 SSH 密钥但无法导出私钥时，使用其支持的本机 SSH 集成；不要要求用户绕过密钥保护。

## 2. 准备受保护的输入与程序

凭据缺失时让用户在本地交互终端运行：

```text
python <skill>/scripts/collect_credentials.py --server-ip SERVER_IP --output RUNTIME_DIR/deploy-input.json
```

输入文件结构（下面是占位示例，不能直接用于安装）：

```json
{"server_ip":"203.0.113.10","proxy":{"host":"proxy.example.com","port":12345,"username":"USER","password":"PASSWORD"}}
```

工具以专属 ACL / 0600 保存。用户已给出有效代理资料时，可直接生成同样受保护的文件，避免再次让用户输入。代理用户名和密码都不放命令行参数。

通过原生 scp/sftp 上传整个 Skill 到服务器临时工作目录；也可使用本仓库固定 release 的 `install.sh` 与运行包。原生 scp 的 `-i` 使用私钥**路径**，不读取其内容。不向服务器复制本地 SSH 私钥。

如果登录用户不是 root：先上传到该用户目录，再用 `sudo install -m 600` 将输入放 `/root/proxyops-input.json`，确认成功后删除上传的凭据副本。输入应是 root 所有、权限 600。

服务器先只读检查：Ubuntu 版本、已有服务、`/etc/x-ui`、443 TCP/UDP 占用、sudo、磁盘和网络。已有面板/非全新服务器就切换维护；不得默认删库重装。云端安全组允许当前 SSH 端口及 TCP 443、UDP 443。不要为了面板放开 8080。

## 3. 执行安装

本 Skill 适配器固定使用 Finderlzy/reality-setup v1.2.1，3x-ui v3.9.0。上游来源与校验见 [upstream.md](upstream.md)。在服务器执行：

```bash
sudo python3 /REMOTE_SKILL/scripts/install.py --input /root/proxyops-input.json
```

耗时流程可以通过一个**唯一命名**的 `systemd-run --collect` 临时服务启动，命令行只有程序和受保护文件路径。这样 SSH 断线不直接杀死安装。不要启动多个并行安装；适配器持有安装锁。通过临时单元状态和 `/var/lib/ubuntu-residential-proxy/*.status.json` 跟踪。

安装日志写受保护的服务器文件；上游会打印节点信息，所以不要把原始日志上传仓库或整段回显。适配器只返回自测结果、状态和日志路径。输入文件在安装尝试结束后删除；本地凭据文件在交付完成后删除或按用户保留偏好处理。

已知适配：拒绝重装、非交互软件包安装、随机面板密码、保留凭据特殊字符、兼容面板 API 字符串/对象响应、要求两种协议及全部导出文件存在。上游原文件不改写。

## 4. 验证与局部恢复

- 安装器退出成功之外，还要确认 x-ui 活跃、运行配置中含 VLESS/Reality 与 Hysteria2、节点文件齐全、两者自测通过。
- `sudo node-selftest --ip` 是本机回环节点测试。另用住宅 SOCKS 出站直连 ipinfo.io 与节点出口比对，不打印代理凭据。用户从本机/手机实际连接的结果用于验证外部 TCP/UDP 可达性。
- 安装器判定失败时先查受保护日志的末尾并**脱敏**；从实际错误决定修复，不能从“上游允许跳过备用节点”推断完整成功。
- 如果只有备用节点缺失，参考 vendored setup.sh 的 Hysteria2 建入站与导出部分，读取已有面板状态后仅补缺失对象/导出。保留健康的 Reality UUID、Reality 服务端私钥、现有住宅出站和分流规则。网络或安全组故障优先修复网络条件。
- 连续两次相同修复失败就给出具体阻塞条件与保留状态，不回到整机重装。

## 5. 最终交付，不进入域名问答

```bash
sudo python3 /REMOTE_SKILL/scripts/export_client.py --stdout
```

该命令只导出 `/root/vless.txt`、`/root/hy2.txt`、`/root/clash.yaml`。它们含客户端访问凭据，属于用户明确请求的交付；可以提供两个可复制链接和完整 YAML 代码块，或通过安全传输保存到用户本地 outputs 下的私有文件再链接。不要把节点链接放 GitHub issue、公开仓库或公开附件。

另交付面板 SSH 隧道命令及登录信息（按需在用户本地私密文件保存随机面板密码，不读取/输出整个 install-result.env）。说明 Reality 与 Hysteria2 的客户端用法及 Clash YAML 导入方式。

首次部署的最终答复只报告验证结果、两个节点与 Clash 配置的获取/导入方法、面板访问方法和仍需用户处理的明确外部条件。**不询问要添加哪些域名，不列出当前分流域名。**
