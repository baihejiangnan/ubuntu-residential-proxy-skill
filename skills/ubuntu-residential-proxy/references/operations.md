# 运维与再次导出

先确认用户请求是只查看还是修复。记录实际版本与服务状态，不把某次成功登录当作永久在线。

只更换住宅代理/住宅出口、保留现有路由时，进入 [replace-proxy.md](replace-proxy.md) 的预检、备份和回滚流程；不要运行安装器。

## 只读检查

```bash
cat /etc/os-release
cat /etc/reality-setup/version
systemctl is-active x-ui
systemctl --failed --no-pager
sudo ss -lntup
free -h
df -h
sudo ufw status
sudo fail2ban-client status sshd
systemctl list-timers --all --no-pager
```

用进程名和监听端口盘点，避免打印可能含凭据的进程完整参数、环境变量或数据库原始行。`install-result.env` 含管理密码和 API Token，不整体回显。日志可能有节点信息，摘取错误时脱敏。

## 密钥与面板

私钥留在用户本机，由 ssh -i 路径使用。无需开启 root 密码登录。对 ubuntu 用户可 sudo 运维。关闭 SSH 密码登录属于额外变更，除非用户明确要求，不在部署时顺手执行。

本地面板隧道示例，替换实际主机、用户和密钥路径：

```bash
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes -i LOCAL_PRIVATE_KEY ubuntu@SERVER_IP
```

浏览器访问 `http://127.0.0.1:18080/panel/`。如果面板路径被修改，应只读查询数据库中的 webBasePath/webPort/webListen，而非输出所有 settings。Agent 适配安装使用随机密码，上游手动安装可能是固定密码，不能假定一致。

## 自测与升级

`sudo node-selftest --ip` 产生临时文件与临时进程，并发起外部请求；它不是纯只读检查。用户授权验证/修复时可运行，结束后确认临时客户端停止。只查看请求先说明检查范围或使用纯只读状态。

上游 Xray 自动更新 timer 自带有限自测/回滚；面板不自动更新。检查日志不等于授权立即升级。用户请求升级时先备份并检查版本兼容性；不得为了改域名重装上游脚本。不要承诺服务绝不会断连。

## 导出

```bash
sudo python3 /REMOTE_SKILL/scripts/export_client.py --stdout
```

只在用户要求导出时输出完整客户端链接/YAML，或通过原生 SSH/SFTP传到用户本地私有输出文件。Skill 和 Git 仓库不保存真实节点导出、凭据、服务器资料。用户后续分享仓库时，不会因此分享自己的代理。

## 局部修复

从实际状态判断网络、防火墙、云安全组、住宅代理套餐/白名单、面板服务或 Xray 配置问题。正常节点的 UUID、证书和出站凭据尽量保留。运行文件被覆盖时修正面板保存模板。部分安装失败不触发默认重装；读取 vendored 对应部分，仅补缺失对象。连续两次同一修复失败后停止该路径并说明阻塞。
