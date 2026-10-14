#!/usr/bin/env bash
set -euo pipefail

# 一键搭建 VLESS + Reality 节点（3x-ui 面板），附带 Hysteria2 备用节点，可选套住宅 IP
# 用法：bash <(curl -fsSL https://raw.githubusercontent.com/Finderlzy/reality-setup/main/setup.sh)
VERSION=v1.2.1     # 发新版时和 git 标签一起改；装完写进 /etc/reality-setup/version
PORT=443           # Reality 用 TCP 443，Hysteria2 用 UDP 443
HY_SNI=www.bing.com  # Hysteria2 自签证书上写的域名
PANEL_PORT=8080
# 面板固定账号，方便排查（面板只监听本机，外网访问不到，只能走 SSH 隧道）
PANEL_PATH=panel PANEL_USER=admin PANEL_PASS=123456

if [ "$(id -u)" -ne 0 ]; then echo "请用 root 用户运行"; exit 1; fi

# ---- 装过的服务器：先确认（真正删除放在所有问题问完之后）----
REINSTALL=
if [ -e /etc/x-ui ]; then
  # SSH 是经这台服务器上的节点连进来的（电脑开着代理 / TUN 模式），源地址会是本机自己的地址，
  # 一停旧节点 SSH 就断，脚本跟着被杀，服务器停在删了一半的状态
  SSH_SRC=${SSH_CLIENT%% *}
  if [ -n "$SSH_SRC" ] && { [ "$SSH_SRC" = 127.0.0.1 ] || [ "$SSH_SRC" = ::1 ] || ip -o addr | awk '{sub(/\/.*/, "", $4); print $4}' | grep -qxF "$SSH_SRC"; }; then
    echo "你现在的 SSH 是通过这台服务器上的节点连进来的，删旧节点时 SSH 会断开、重装会中途失败。"
    echo "请先关掉电脑上的代理软件（或把 TUN / 全局模式关掉），重新 SSH 连上再运行。"
    exit 1
  fi
  OLD=$(cat /etc/reality-setup/version 2>/dev/null || echo "v1.1.0 或更早")
  if [ -e /root/vless.txt ]; then echo "这台服务器用本脚本装过（版本：$OLD）。"
  else echo "这台服务器装过 3x-ui 面板（不是本脚本装的），面板里所有节点和设置都会被删掉。"; fi
  read -rp "删掉旧的重新装吗？装完旧节点链接会失效，客户端要重新导入 [y/N]: " ANS
  [ "$ANS" = y ] || [ "$ANS" = Y ] || { echo "已取消，什么都没动"; exit 1; }
  REINSTALL=1
fi

DETECTED=$(curl -s4 -m 10 https://api.ipify.org || true)
read -rp "服务器 IP 是 ${DETECTED:-（没检测到）} 吗？对就直接回车，不对就输入你 SSH 连接用的 IP: " IP
IP=${IP:-$DETECTED}
read -rp "住宅代理 IP（方案二直接回车跳过）: " RES_IP
if [ -n "$RES_IP" ]; then
  read -rp "住宅代理端口: " RES_PORT
  read -rp "住宅代理用户名: " RES_USER
  read -rp "住宅代理密码: " RES_PASS
fi
if [ -z "$IP" ]; then echo "服务器 IP 不能为空"; exit 1; fi

if [ -n "$REINSTALL" ]; then
  systemctl disable --now x-ui xray-autoupdate.timer >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/x-ui.service /etc/systemd/system/xray-autoupdate.service /etc/systemd/system/xray-autoupdate.timer
  systemctl daemon-reload
  rm -rf /etc/x-ui /usr/local/x-ui /etc/default/x-ui /usr/bin/x-ui /etc/reality-setup \
    /usr/local/bin/node-selftest /usr/local/bin/xray-autoupdate \
    /root/vless.txt /root/hy2.txt /root/clash.yaml /root/xray.bak /root/cert/hy2.crt /root/cert/hy2.key
  if command -v ufw >/dev/null; then ufw --force delete allow 8555/tcp >/dev/null 2>&1 || true; fi  # v1.0.0 及更早用的节点端口
  echo "== 旧的已删除，开始重新安装 =="
fi

apt update
apt install -y curl openssl ufw python3 unattended-upgrades fail2ban python3-systemd qrencode

# ---- 系统安全补丁每天自动安装（只装安全更新，不会自动重启）----
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true

# ---- 先测住宅代理，不通就不往下装 ----
if [ -n "$RES_IP" ]; then
  echo "== 测试住宅代理 =="
  RES_OUT=$(curl -s -m 15 --socks5-hostname "$RES_IP:$RES_PORT" --proxy-user "$RES_USER:$RES_PASS" https://ipinfo.io/ip || true)
  if [ -z "$RES_OUT" ]; then
    echo "住宅代理连不上。去商家后台检查：端口、用户名密码、IP 白名单（把 $IP 加进去）、套餐是否生效。"
    exit 1
  fi
  echo "住宅代理可用，出口 IP：$RES_OUT"
fi

# ---- 安装 3x-ui（面板只监听本机，用 SSH 隧道访问）----
XUI_NONINTERACTIVE=1 XUI_PANEL_PORT=$PANEL_PORT XUI_WEB_BASE_PATH=$PANEL_PATH XUI_USERNAME=$PANEL_USER XUI_PASSWORD=$PANEL_PASS XUI_SSL_MODE=none XUI_SERVER_IP=$IP XUI_ENABLE_FAIL2BAN=false \
  bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/v3.9.0/install.sh) v3.9.0 </dev/null
/usr/local/x-ui/x-ui setting -listenIP 127.0.0.1 >/dev/null
systemctl restart x-ui
. /etc/x-ui/install-result.env
B="http://127.0.0.1:$PANEL_PORT/$XUI_WEB_BASE_PATH"
for i in $(seq 1 30); do curl -s -o /dev/null "$B/" && break; sleep 1; done

# ---- Hysteria2 用的自签证书（有效期 10 年）----
HY_CERT=/root/cert/hy2.crt HY_KEY=/root/cert/hy2.key
mkdir -p /root/cert && chmod 700 /root/cert
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
  -subj "/CN=$HY_SNI" -addext "subjectAltName=DNS:$HY_SNI" -keyout "$HY_KEY" -out "$HY_CERT" 2>/dev/null

# ---- 在面板里建入站、客户端、住宅出站 ----
export HY_CERT HY_KEY HY_SNI IP PORT B XUI_API_TOKEN RES_IP RES_PORT="${RES_PORT:-}" RES_USER="${RES_USER:-}" RES_PASS="${RES_PASS:-}"
python3 - <<'PY'
import hashlib, json, os, secrets, ssl, subprocess, urllib.parse, urllib.request

E = os.environ
def api(path, form=None, body=None):
    data, headers = None, {"Authorization": "Bearer " + E["XUI_API_TOKEN"]}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(E["B"] + path, data=data, headers=headers, method="POST" if data is not None or path.endswith("/") else "GET")
    res = json.load(urllib.request.urlopen(req, timeout=120))
    if not res.get("success"):
        raise SystemExit(f"面板接口出错：{path} {res.get('msg')}")
    return res["obj"]

# 伪装目标：和面板里点“扫描”一样先确认可用，再挑服务器连过去 TLS 握手最快的（离得近，伪装更像真的）
# 候选都是国内能正常访问的大站：伪装成被墙的网站反而会被干扰
def handshake_ms(host):
    times = []
    for _ in range(3):
        out = subprocess.run(["curl", "-so", "/dev/null", "-m", "5", "-w", "%{time_appconnect}", f"https://{host}"],
                             capture_output=True, text=True).stdout
        if out and float(out) > 0:
            times.append(float(out) * 1000)
    return min(times) if times else None

best = None
for host in ["www.microsoft.com", "www.apple.com", "www.amazon.com", "www.nvidia.com",
             "www.amd.com", "www.samsung.com", "www.cisco.com", "www.oracle.com"]:
    scan = api("/panel/api/server/scanRealityTarget", form={"target": host + ":443"})
    ms = handshake_ms(host) if scan.get("feasible") else None
    if ms is not None and (best is None or ms < best[0]):
        best = (ms, host, scan)
if best is None:
    raise SystemExit("几个伪装网站都不可用，请到面板里手动扫描换一个")
ms, host, scan = best
target = host + ":443"
print(f"伪装网站：{host}（握手 {ms:.0f} ms）")

# 节点名用服务器 IP 所在地，比如“美国 洛杉矶（主节点）”；查不到就只写“主节点”
try:
    geo = json.load(urllib.request.urlopen(f"http://ip-api.com/json/{E['IP']}?lang=zh-CN&fields=status,country,city", timeout=10))
    place = " ".join(dict.fromkeys(v for v in (geo.get("country"), geo.get("city")) if v)) if geo.get("status") == "success" else ""
except Exception:
    place = ""
MAIN_NAME, HY_NAME = f"{place}（主节点）", f"{place}（备用节点）"

port = int(E["PORT"])
sniffing = json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic"], "metadataOnly": False, "routeOnly": True})
def add_inbound(remark, protocol, settings, stream):
    stream["externalProxy"] = [{"forceTls": "same", "dest": E["IP"], "port": port, "remark": ""}]
    api("/panel/api/inbounds/add", body={"up": 0, "down": 0, "total": 0, "remark": remark, "enable": True,
                                         "expiryTime": 0, "listen": "", "port": port, "protocol": protocol,
                                         "settings": json.dumps(settings), "streamSettings": json.dumps(stream),
                                         "sniffing": sniffing})
def client(**kw):
    return {"limitIp": 0, "totalGB": 0, "expiryTime": 0, "enable": True, "tgId": 0,
            "subId": secrets.token_hex(8), "reset": 0, **kw}

# 主节点：VLESS + Reality，走 TCP
keys = api("/panel/api/server/getNewX25519Cert")
uuid = api("/panel/api/server/getNewUUID")["uuid"]
add_inbound(MAIN_NAME, "vless",
            {"clients": [client(id=uuid, flow="xtls-rprx-vision", email="me")], "decryption": "none", "fallbacks": []},
            {"network": "tcp", "security": "reality",
             "realitySettings": {"show": False, "xver": 0, "target": target, "serverNames": scan["serverNames"],
                                 "privateKey": keys["privateKey"], "minClientVer": "", "maxClientVer": "",
                                 "maxTimediff": 0, "shortIds": [secrets.token_hex(4)],
                                 "settings": {"publicKey": keys["publicKey"], "fingerprint": "chrome",
                                              "serverName": "", "spiderX": "/"}},
             "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}}})

# 备用节点：Hysteria2，走 UDP，和主节点共用 443 端口号。没有域名，用自签证书，
# 客户端靠证书指纹（pin）只认这一张证书，不需要打开“跳过证书验证”
pem = open(E["HY_CERT"]).read()
pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
hy_auth = secrets.token_urlsafe(16)
try:
    add_inbound(HY_NAME, "hysteria",
                {"version": 2, "clients": [client(auth=hy_auth, email="me-hy2", comment="")]},
                {"network": "hysteria", "security": "tls",
                 "hysteriaSettings": {"version": 2, "auth": "", "udpIdleTimeout": 60},
                 "tlsSettings": {"serverName": E["HY_SNI"], "minVersion": "1.3", "maxVersion": "1.3", "cipherSuites": "",
                                 "rejectUnknownSni": False, "disableSystemRoot": False, "enableSessionResumption": False,
                                 "certificates": [{"certificateFile": E["HY_CERT"], "keyFile": E["HY_KEY"], "ocspStapling": 0,
                                                   "oneTimeLoading": False, "usage": "encipherment", "buildChain": False}],
                                 "alpn": ["h3"], "echServerKeys": "",
                                 "settings": {"fingerprint": "", "echConfigList": "", "pinnedPeerCertSha256": [pin],
                                              "verifyPeerCertByName": ""}}})
except SystemExit as e:  # 备用节点建不成不影响主节点
    print(f"Hysteria2 备用节点没建成，跳过（{e}）")

if E["RES_IP"]:
    xray = json.loads(api("/panel/api/xray/"))["xraySetting"]
    xray["outbounds"].append({"tag": "residential", "protocol": "socks", "settings": {"servers": [{
        "address": E["RES_IP"], "port": int(E["RES_PORT"]),
        "users": [{"user": E["RES_USER"], "pass": E["RES_PASS"]}]}]}})
    xray["routing"]["rules"].insert(1, {"type": "field", "outboundTag": "residential",
                                        "domain": ["geosite:anthropic", "domain:ipinfo.io"]})
    api("/panel/api/xray/update", form={"xraySetting": json.dumps(xray)})
api("/panel/api/server/restartXrayService", form={})

links = api("/panel/api/inbounds/allLinks")
# 面板生成的链接名是“入站名-客户端名”，换成上面定好的节点名
rename = lambda l, name: l.split("#")[0] + "#" + urllib.parse.quote(name)
link = rename(next(l for l in links if l.startswith("vless://")), MAIN_NAME)
hy_link = next((rename(l, HY_NAME) for l in links if l.startswith("hysteria2://")), None)
open("/root/vless.txt", "w").write(link + "\n")
if hy_link:
    open("/root/hy2.txt", "w").write(hy_link + "\n")

# Clash 系（Clash Verge、FlClash、Clash Meta）不能直接粘贴分享链接，另外生成一份配置
u = urllib.parse.urlparse(link); q = dict(urllib.parse.parse_qsl(u.query))
s = lambda v: json.dumps(v, ensure_ascii=False)  # JSON 字符串也是合法的 YAML 字符串，省去转义的麻烦
hy2 = f"""
  - name: {s(HY_NAME)}
    type: hysteria2
    server: {s(E["IP"])}
    port: {port}
    password: {s(hy_auth)}
    sni: {s(E["HY_SNI"])}
    fingerprint: {s(pin)}
    alpn: [h3]""" if hy_link else ""
# 默认选中 Hysteria2：mihomo 连 Reality 的兼容性还没确认
group = [s(HY_NAME), s(MAIN_NAME)] if hy_link else [s(MAIN_NAME)]
open("/root/clash.yaml", "w").write(f"""proxies:
  - name: {s(MAIN_NAME)}
    type: vless
    server: {s(E["IP"])}
    port: {u.port}
    uuid: {s(u.username)}
    network: tcp
    udp: true
    tls: true
    flow: {s(q.get("flow", ""))}
    servername: {s(q["sni"])}
    client-fingerprint: {s(q.get("fp", "chrome"))}
    reality-opts:
      public-key: {s(q["pbk"])}
      short-id: {s(q.get("sid", ""))}{hy2}
proxy-groups:
  - name: {s("节点选择")}
    type: select
    proxies: [{", ".join(group)}, DIRECT]
rules:
  - GEOIP,LAN,DIRECT,no-resolve
  - GEOIP,CN,DIRECT
  - MATCH,节点选择
""")
PY

# ---- 防火墙：放行 SSH 和节点端口 ----
SSH_PORT=$(ss -tlnpH | awk '/sshd/ {sub(/.*:/, "", $4); print $4; exit}')
ufw allow "${SSH_PORT:-22}"/tcp
ufw allow "$PORT"/tcp
ufw allow "$PORT"/udp
ufw --force enable

# ---- fail2ban：10 分钟内 SSH 密码输错 5 次，封这个 IP 1 小时 ----
cat > /etc/fail2ban/jail.d/sshd.local <<EOF
[sshd]
enabled = true
backend = systemd
port = ${SSH_PORT:-22}
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban || true
sleep 2
if fail2ban-client status sshd >/dev/null 2>&1; then
  F2B="已开启（10 分钟内输错 5 次密码封 1 小时）"
else
  F2B="没开成，不影响节点使用，可运行 systemctl status fail2ban 查看原因"
fi
echo "== fail2ban：$F2B =="

# ---- 网络优化：开启 BBR（容器类 VPS 改不了内核参数，开不了就跳过，不影响节点）----
modprobe tcp_bbr 2>/dev/null || true
if grep -qw bbr /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null; then
  printf 'net.core.default_qdisc = fq\nnet.ipv4.tcp_congestion_control = bbr\n' > /etc/sysctl.d/99-bbr.conf
  sysctl -p /etc/sysctl.d/99-bbr.conf >/dev/null 2>&1 || true
fi
if [ "$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null)" = bbr ]; then
  BBR="已开启"
else
  BBR="没开成（多半是容器类 VPS 不允许改内核参数），不影响节点使用"
fi
echo "== BBR：$BBR =="

# ---- 自测命令：服务器自己连自己的节点（Xray 自动更新也用它）----
cat > /usr/local/bin/node-selftest <<'PY'
#!/usr/bin/env python3
# 服务器自己连自己的节点（Reality 和 Hysteria2 都测）：都通了退出码 0，否则 1；带 --ip 时再打印走节点的出口 IP
import glob, json, os, subprocess, sys, tempfile, time, urllib.parse
def parse(path):
    u = urllib.parse.urlparse(open(path).read().strip())
    return u, dict(urllib.parse.parse_qsl(u.query))
u, q = parse("/root/vless.txt")
reality = {"tag": "reality", "protocol": "vless", "settings": {"vnext": [{"address": "127.0.0.1", "port": u.port,
              "users": [{"id": u.username, "encryption": "none", "flow": q.get("flow", "")}]}]},
           "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
               "serverName": q["sni"], "fingerprint": q.get("fp", "chrome"), "publicKey": q["pbk"],
               "shortId": q.get("sid", ""), "spiderX": q.get("spx", "")}}}
has_hy2 = os.path.exists("/root/hy2.txt")
u, q = parse("/root/hy2.txt") if has_hy2 else (None, None)
hy2 = None if not has_hy2 else {"tag": "hy2", "protocol": "hysteria", "settings": {"address": "127.0.0.1", "port": u.port, "version": 2},
       "streamSettings": {"network": "hysteria", "security": "tls",
           "hysteriaSettings": {"version": 2, "auth": urllib.parse.unquote(u.username), "udpIdleTimeout": 60},
           "tlsSettings": {"serverName": q["sni"], "alpn": ["h3"], "pinnedPeerCertSha256": q["pinSHA256"]}}}
client = {"inbounds": [{"tag": "in-reality", "port": 10899, "listen": "127.0.0.1", "protocol": "socks"},
                       {"tag": "in-hy2", "port": 10898, "listen": "127.0.0.1", "protocol": "socks"}],
          "outbounds": [o for o in (reality, hy2) if o],
          "routing": {"rules": [{"type": "field", "inboundTag": ["in-reality"], "outboundTag": "reality"},
                                {"type": "field", "inboundTag": ["in-hy2"], "outboundTag": "hy2"}][:2 if has_hy2 else 1]}}
fd, cfg = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w") as f:
    json.dump(client, f)
xray = sorted(glob.glob("/usr/local/x-ui/bin/xray-linux-*"))[0]
p = subprocess.Popen([xray, "run", "-c", cfg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
def via(port, url, fmt):
    return subprocess.run(["curl", "-s", "-m", "15", "-x", f"socks5h://127.0.0.1:{port}", "-o", "/dev/null" if fmt else "-",
                           "-w", fmt, url], capture_output=True, text=True).stdout.strip()
results = []
for name, port in [("Reality", 10899), ("Hysteria2", 10898)][:2 if has_hy2 else 1]:
    ok = via(port, "https://www.google.com/generate_204", "%{http_code}") == "204"
    results.append(ok)
    print(f"== 节点自测 {name}：" + ("成功" if ok else "失败") + " ==")
if results[0] and "--ip" in sys.argv:
    print("== 走节点访问 ipinfo.io 的出口 IP：" + (via(10899, "https://ipinfo.io/ip", "") or "取不到") + " ==")
p.terminate(); p.wait(); os.remove(cfg)
sys.exit(0 if all(results) else 1)
PY
chmod 755 /usr/local/bin/node-selftest
sleep 3
node-selftest ${RES_IP:+--ip} || true

# ---- Xray 内核每周自动更新：更新后自测，不通就退回旧版本（3x-ui 面板本身不自动更新）----
cat > /usr/local/bin/xray-autoupdate <<'EOF'
#!/usr/bin/env bash
# 检查 Xray 内核：有新版本就通过面板接口更新，更新后自测不通就退回旧版本
set -uo pipefail
. /etc/x-ui/install-result.env
API="http://127.0.0.1:$XUI_PANEL_PORT/$XUI_WEB_BASE_PATH/panel/api/server"
XRAY=$(ls /usr/local/x-ui/bin/xray-linux-* | head -1)
CUR=v$("$XRAY" version | awk 'NR==1 {print $2}')
# 版本列表和面板里“切换 Xray 版本”看到的一样（Xray 现在的新版本在 GitHub 上都标为预发布）
NEW=$(curl -fsS -m 60 -H "Authorization: Bearer $XUI_API_TOKEN" "$API/getXrayVersion" \
  | python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)["obj"]))' | sort -V | tail -1)
[ -n "$NEW" ] || { echo "查不到最新版本，下次再试"; exit 0; }
if [ "$(printf '%s\n%s\n' "$CUR" "$NEW" | sort -V | tail -1)" = "$CUR" ]; then echo "Xray $CUR 已是最新"; exit 0; fi

echo "Xray $CUR -> $NEW"
cp "$XRAY" /root/xray.bak
RES=$(curl -s -m 600 -X POST -H "Authorization: Bearer $XUI_API_TOKEN" "$API/installXray/$NEW")
sleep 3
if node-selftest; then
  if echo "$RES" | grep -q '"success":true'; then echo "更新成功：$NEW"; else echo "面板没有更新成功，仍是 $CUR：$RES"; fi
  rm -f /root/xray.bak
  exit 0
fi

echo "新版本自测不通，退回 $CUR"
cp /root/xray.bak /usr/local/x-ui/bin/.xray-restore && mv -f /usr/local/x-ui/bin/.xray-restore "$XRAY"
curl -s -m 60 -X POST -H "Authorization: Bearer $XUI_API_TOKEN" "$API/restartXrayService" >/dev/null
sleep 3
node-selftest
exit 1
EOF
chmod 755 /usr/local/bin/xray-autoupdate
cat > /etc/systemd/system/xray-autoupdate.service <<'EOF'
[Unit]
Description=Xray 内核自动更新（更新后自测，不通就回滚）
After=network-online.target x-ui.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/xray-autoupdate
EOF
cat > /etc/systemd/system/xray-autoupdate.timer <<'EOF'
[Unit]
Description=每周检查一次 Xray 内核新版本

[Timer]
OnCalendar=Sun 04:00
RandomizedDelaySec=2h
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now xray-autoupdate.timer >/dev/null 2>&1 || true

mkdir -p /etc/reality-setup && echo "$VERSION" > /etc/reality-setup/version

echo
echo "===== 完成（版本 $VERSION）====="
echo "【主节点 Reality】复制下面这行导入代理软件（也存在 /root/vless.txt），手机可以直接扫码："
cat /root/vless.txt
qrencode -t ANSIUTF8 -m 2 < /root/vless.txt
echo
if [ -f /root/hy2.txt ]; then
  echo "【备用节点 Hysteria2】主节点连不上或晚高峰很慢时用（也存在 /root/hy2.txt）："
  cat /root/hy2.txt
  qrencode -t ANSIUTF8 -m 2 < /root/hy2.txt
  echo
fi
echo "用 Clash 系软件（Clash Verge、FlClash、Clash Meta）的：运行 cat /root/clash.yaml，复制全部内容，"
echo "  在软件里新建一个本地配置，把内容粘贴进去保存（两个节点都在里面）"
echo "想再看二维码：qrencode -t ANSIUTF8 < /root/vless.txt"
echo
echo "商家后台有防火墙 / 安全组的话，放行 TCP 443 和 UDP 443"
echo
echo "BBR 网络优化：$BBR"
echo "系统安全更新：已设为每天自动安装"
echo "Xray 内核：每周日自动检查更新，更新后自测不通会自动退回旧版本（日志：journalctl -u xray-autoupdate）"
echo "SSH 防爆破（fail2ban）：$F2B"
echo "  自己被误封了：换个网络连上去，运行 fail2ban-client set sshd unbanip 你的IP"
echo
echo "面板（排查用，只能通过 SSH 隧道打开）："
echo "  1. 在自己电脑上运行：ssh -L $PANEL_PORT:127.0.0.1:$PANEL_PORT root@$IP"
echo "  2. 浏览器打开：http://127.0.0.1:$PANEL_PORT/$XUI_WEB_BASE_PATH"
echo "  3. 用户名：$XUI_USERNAME  密码：$XUI_PASSWORD"
echo "  忘了的话运行：cat /etc/x-ui/install-result.env"
