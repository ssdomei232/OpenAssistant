# OpenAssistant Agent（树莓派录音端）

穿戴式录音端的完整实现：**采集 → VAD 切片 → 本地持久化队列 → HTTPS 上传**，
并通过 **BLE GATT** 向 App 提供配置/状态/队列管理通道。

- 只上传**含人声**的片段（16-bit mono 16 kHz wav）；
- 断网自动暂存，恢复后按序补传（指数退避 + 抖动，幂等 `client_segment_id`）；
- 断电重启不丢队列（SQLite；启动时把 `uploading` 对账回 `pending`）；
- 所有第三方依赖可选：没有 `sounddevice`/`webrtcvad`/`httpx`/`bless` 时自动降级，
  `import` 不会失败，便于在树莓派/开发机/CI 上运行。

---

## 1. 目录结构

```
agent/
  openassistant_agent/
    __init__.py         # 版本与导出
    __main__.py         # python -m openassistant_agent
    config.py           # AgentConfig：默认值 < TOML < OA_* 环境变量 < 显式参数
    capture.py          # SoundDeviceSource / FileSource / SyntheticSource
    vad.py              # webrtcvad 适配 + 自适应能量 VAD 兜底
    segmenter.py        # preroll/postroll、静音合并、最短语音过滤、最大长度切分
    spool.py            # SQLite 队列（pending/uploading/uploaded/failed）
    uploader.py         # multipart 上传、重试、离线暂存、幂等
    ble_protocol.py     # BLE 协议编解码（分片拼帧/JSON/命令路由，纯标准库）
    ble.py              # BleManager + bless GATT Server + MockBleTransport
    service.py          # AgentService 常驻主循环、优雅退出、状态上报
    cli.py              # run/status/flush/config/selftest/ble-serve
  tests/                # pytest：VAD/切片/队列/上传/BLE/服务一条龙
  systemd/openassistant-agent.service
  config.example.toml
  requirements.txt
  README.md
```

运行时数据（默认 `/var/lib/openassistant`）：

```
/var/lib/openassistant/
  spool.db              # SQLite 队列
  segments/*.wav        # 待上传/失败保留的音频片段
```

---

## 2. 树莓派安装（Raspberry Pi OS Bookworm，64 位）

### 2.1 系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
    alsa-utils pulseaudio-utils bluez \
    libasound2-dev portaudio19-dev libffi-dev
```

> 使用 PipeWire 的 Bookworm 桌面版同样适用；`pulseaudio-utils` 仅用于调试。

### 2.2 确认 DJI Mic Mini 作为 USB 声卡

把 DJI Mic Mini 的 USB-C 接收器插入树莓派，然后：

```bash
# 1) 找到录音设备（Card/Device 编号）
arecord -l
#    card 1: Mini [DJI Mic Mini], device 0: USB Audio [USB Audio]
#    → 设备为 plughw:1,0，名称包含 "DJI Mic Mini"

# 2) 试录 5 秒 16 kHz 单声道
arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/test.wav

# 3) 回放确认有声音
aplay /tmp/test.wav
```

- 如果 `arecord` 报 `Sample format non available`，请用 `plughw:` 前缀（ALSA 自动转换），
  或在 `~/.asoundrc` / `pulseaudio` 中固定 16 kHz。
- 若插拔后编号变化，Agent 按**设备名子串**匹配（`record.device`），不依赖固定编号；
  可用 `arecord -l` 查看确切的 `name`，必要时改成 `"DJI"` 这类更短的关键字。
- 树莓派 USB 供电不足会导致声卡掉线：使用带供电的 USB Hub 或官方电源。

### 2.3 部署代码与依赖

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin openassistant
sudo usermod -aG audio,bluetooth openassistant

sudo mkdir -p /opt/openassistant
sudo cp -r agent /opt/openassistant/agent           # 或 rsync/git clone
sudo python3 -m venv /opt/openassistant/venv
sudo /opt/openassistant/venv/bin/pip install -U pip
sudo /opt/openassistant/venv/bin/pip install -r /opt/openassistant/agent/requirements.txt

sudo mkdir -p /etc/openassistant /var/lib/openassistant
sudo cp /opt/openassistant/agent/config.example.toml /etc/openassistant/agent.toml
sudo chown -R openassistant:openassistant /var/lib/openassistant
sudo chmod 600 /etc/openassistant/agent.toml          # 含 API Key
```

编辑 `/etc/openassistant/agent.toml`：

```toml
server_url = "https://your-server.example.com"
api_key = "YOUR_API_KEY"
device_id = "pi-livingroom"

[record]
device = "DJI Mic Mini"
source = "device"
```

先手工验证（前台运行，Ctrl+C 退出）：

```bash
sudo -u openassistant PYTHONPATH=/opt/openassistant/agent \
  /opt/openassistant/venv/bin/python -m openassistant_agent --config /etc/openassistant/agent.toml run
```

看到 `agent started` 与 `segment ... queued` / `uploaded ...` 日志即正常。

### 2.4 systemd 常驻

```bash
sudo cp /opt/openassistant/agent/systemd/openassistant-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openassistant-agent
systemctl status openassistant-agent
journalctl -u openassistant-agent -f
```

服务使用 `Restart=always`、`KillSignal=SIGTERM`（Agent 捕获后收尾当前片段并尽量上传），
数据目录由 `StateDirectory=openassistant` 自动创建。可选的环境变量文件：

```bash
sudo tee /etc/openassistant/agent.env >/dev/null <<'EOF'
# 覆盖 TOML 中的同名配置（OA_* 优先）
# OA_SERVER_URL=https://your-server.example.com
# OA_LOG_LEVEL=DEBUG
EOF
sudo chmod 600 /etc/openassistant/agent.env
```

### 2.5 WiFi 配置

Agent 不直接改系统网络，推荐两种方式：

```bash
# 方式 1：nmcli（Bookworm 默认 NetworkManager）
sudo nmcli device wifi connect "home" password "******"
nmcli connection show

# 方式 2：raspi-config 交互式
sudo raspi-config   # System Options -> Wireless LAN
```

App 通过 BLE CONFIG 写入的 `wifi` 字段会随配置一起回传 Server 备份（便于多设备管理），
若需要 App 直接改网络，可在后续版本接入 NetworkManager D-Bus。

### 2.6 蓝牙 GATT 权限

`bless` 通过 D-Bus 使用 BlueZ，需要：

1. `openassistant` 用户在 `bluetooth` 组（见 2.3）；
2. 蓝牙适配器已上电：`bluetoothctl show` 中 `Powered: yes`，否则 `bluetoothctl power on`；
3. BlueZ ≥ 5.55（Bookworm 自带满足），并允许非 root 用户注册 GATT 服务：
   ```bash
   sudo systemctl status bluetooth
   # 若 D-Bus 拒绝，检查 /etc/dbus-1/system.d/bluetooth.conf 是否允许该用户
   ```
4. 只运行 BLE 通道调试：

```bash
/opt/openassistant/venv/bin/python -m openassistant_agent --config /etc/openassistant/agent.toml ble-serve
# 无蓝牙环境可先验证协议（内存 mock）：
python -m openassistant_agent ble-serve --mock
```

Windows 开发机安装 `bless` 后同样可以起 GATT Server（需蓝牙 4.0+ 适配器）。

---

## 3. App 端 BLE 配对说明

- **设备名**：默认 `OpenAssistant`（`ble-serve --name` 可改）。
- **服务 UUID**：`0000a001-0000-1000-8000-00805f9b34fb`
- **特征**：

| UUID 后缀 | 名称 | 属性 | 内容 |
|---|---|---|---|
| `a002` | CONFIG | read / write | 配置 JSON（UTF-8），分片追加写 |
| `a003` | STATUS | read / notify | 状态 JSON（约 10 s 通知一次） |
| `a004` | QUEUE | read / write | 队列命令/结果 JSON |

**CONFIG 写入规则**

- 单次写入 ≤ 512 字节；App 把完整 JSON 切成多片依次写入；
- Agent 累积缓冲，读到 `}` 且 JSON 可解析即生效（`{"device_id":"pi-1"}` 这类单包也支持）；
- 生效后：更新运行时配置 → 写回 `/etc/openassistant/agent.toml`（无权限时回退
  `~/.config/openassistant/agent.toml` 并记日志）→ 若配置了 `server_url` + `api_key`，
  自动 `PUT /api/v1/devices/{device_id}/config` 备份。

示例（App 写入）：

```json
{
  "device_id": "pi-livingroom",
  "upload": {"retry_max": 5, "offline_buffer": true, "endpoint": "/api/v1/audio/segments"},
  "vad": {"aggressiveness": 2, "min_speech_ms": 300, "min_silence_ms": 500},
  "wifi": {"ssid": "home", "password": "******"}
}
```

`wifi` 字段 Agent 不做系统级应用（见“已知限制”），会随配置回传 Server 备份。

**STATUS 读取/通知**：

```json
{
  "device_id": "pi-livingroom",
  "wifi_connected": true,
  "queue": {"total": 3, "pending": 1, "uploading": 0, "uploaded": 2, "failed": 0,
            "pending_bytes": 42284, "duration_s": 4.5, "oldest_pending_at": 1793357414.5},
  "recording": true,
  "uptime_s": 128.4,
  "retry_max": 5,
  "offline_buffer": true
}
```

**QUEUE 命令**（写入特征，读回结果）：

```json
{"action": "list", "limit": 20}
{"action": "retry", "id": 3}     // 不带 id 则重试所有 failed
{"action": "clear"}              // 清理已上传记录与文件；"status":"failed" 清理失败项
```

响应：`{"ok": true, "action": "list", "result": {"items": [...], "stats": {...}}}`。

Web Bluetooth 需要 Chrome/Edge/Android WebView（iOS Safari 不支持）。

---

## 4. 开发模式（无硬件）

### 4.1 文件回放 + 模拟上传

```bash
export PYTHONPATH=/path/to/OpenAssistant/agent
export OA_SPOOL_PATH=/tmp/oa-dev

# 用真实 wav 驱动整条链路（上传会访问 server_url；先把 server_url 指向本地 mock）
python -m openassistant_agent run --source file --input demo.wav --no-upload

# 只看队列
python -m openassistant_agent status
```

`FileSource` 支持任意采样率/声道的 wav（自动降混单声道并线性重采样到 16 kHz），
适合用手机录音做回归测试。上传 mock：在代码中给 `AgentService` 注入
`MockTransport(healthy=True)`（`selftest` 即是这么做的），测试不会触网。

### 4.2 全链路自检（合成音频 + mock 上传 + mock BLE）

```bash
python -m openassistant_agent selftest
python -m openassistant_agent selftest --json
```

输出 12 项检查：VAD 产段、落盘、入队、meta 契约、wav 文件、离线暂存、BLE 配置/状态/队列。

### 4.3 测试

```bash
export PYTHONPATH=/path/to/OpenAssistant/.deps:/path/to/OpenAssistant/agent
python -m pytest agent/tests -q
```

只依赖 `numpy`（且仅在测试辅助中使用标准库即可运行）；不访问网络/声卡/蓝牙。

---

## 5. CLI

```bash
python -m openassistant_agent [--config PATH] [--log-level LEVEL] <command>

run         [--source device|file|synthetic] [--input demo.wav] [--duration 10]
            [--max-blocks N] [--no-upload] [--upload-interval 10] [--no-signals]
status      [--json]                     # 配置 + 队列统计
flush       [--limit N] [--max-rounds 5] [--json]
config get [KEY] [--json]                # KEY 支持点号路径，如 upload.retry_max
config set KEY VALUE [--json]            # 写回 TOML
config path
selftest    [--workdir DIR] [--keep] [--json]
ble-serve   [--name OpenAssistant] [--adapter hci0] [--mock] [--duration N]
```

环境变量覆盖示例：`OA_SERVER_URL`、`OA_API_KEY`、`OA_DEVICE_ID`、`OA_SPOOL_PATH`、
`OA_VAD_AGGRESSIVENESS`、`OA_UPLOAD_RETRY_MAX`、`OA_UPLOAD_OFFLINE_BUFFER`、
`OA_RECORD_DEVICE`、`OA_RECORD_SAMPLE_RATE`、`OA_RECORD_SOURCE`、`OA_RECORD_INPUT`、
`OA_RETRY_MAX`、`OA_OFFLINE_BUFFER`。

---

## 6. 上传契约摘要

`POST {server_url}/api/v1/audio/segments`，`X-API-Key` 认证，`multipart/form-data`：

- `file`：wav（mono，16 kHz）
- `meta`：JSON 字符串

```json
{
  "client_segment_id": "seg-20260305T093000-0001",
  "device_id": "pi-livingroom",
  "recorded_at": "2026-03-05T09:30:00+08:00",
  "duration_s": 42.5,
  "sample_rate": 16000,
  "channels": 1,
  "sha256": "…",
  "vad": {"speech_ratio": 0.61, "segments": [[0.3, 8.2], [9.1, 40.0]]},
  "trigger": "auto"
}
```

- `202` 入队、`200`/`duplicate=true` 幂等重复、`409` 也按成功处理；
- 4xx（除 408/429）视为永久失败 → 直接 `failed`，文件保留；
- 5xx/超时/网络错误 → 指数退避重试，`retry_max` 次后 `failed`（文件保留，可 BLE retry）；
- 上传前 `sha256` 校验，损坏文件不发送。

网络探测：`GET {server}/health`（`server_url` 若带 `/api/v1` 会自动剥离到根路径）。

---

## 7. 故障排查

| 现象 | 排查 |
|---|---|
| `sounddevice is not installed` | 安装 `sounddevice`，或在开发时用 `--source file/synthetic` |
| `audio input device matching 'DJI Mic Mini' not found` | `arecord -l` 确认真实名称，修改 `record.device` 或改短关键字 |
| 一直 `PortAudio: Device unavailable` / 掉线 | USB 供电不足；换 Hub/电源，`dmesg` 查看 USB 断连 |
| 录到 48 kHz 数据或采样率报错 | 用 `plughw:`、PulseAudio/PipeWire 重采样，或只保留一种采样率配置 |
| 队列一直 `pending` 且日志 offline | 检查 `server_url`、DNS、防火墙；`curl {server}/health`；恢复后自动补传 |
| `401 invalid_api_key` | `api_key` 与 Server 不一致（注意 600 权限、无尾随空格） |
| 上传一直 `failed` | `status --json` 查看 `last_error`；必要时 BLE `{"action":"retry"}` |
| BLE 扫不到设备 | `systemctl status bluetooth`、适配器 `Powered: yes`、用户加入 `bluetooth` 组、`bless` 已安装 |
| BLE 写入无反应 | 单次写入需 ≤512 字节，完整 JSON 以 `}` 结尾；`journalctl` 看 `invalid config` |
| 磁盘增长 | `queue.uploaded` 会保留文件直到清理；定期 `flush` + `{"action":"clear"}` 或 cron 调 `flush` |
| 服务反复重启 | `journalctl -u openassistant-agent -n 100`，多为配置非法或声卡不可用 |

日志级别：`--log-level DEBUG` 可看到 VAD 阈值、上传探测等细节。

---

## 8. 已知限制

- BLE 的 `wifi` 字段只做透传备份，**不自动修改系统 WiFi**；无线网络切换请用
  `nmcli`/`raspi-config`（或后续接入 NetworkManager D-Bus）。
- `bless` 的 GATT Server 依赖系统蓝牙栈；无蓝牙环境请用 `--mock`/`MockBleTransport`。
- 能量 VAD 是 webrtcvad 的兜底方案：强稳态噪声环境建议安装 `webrtcvad` 或调高
  `energy_factor` / `absolute_floor`。
- 上传失败的文件不会自动删除（便于事后补传/取证）；需通过 `clear`/`purge` 管理磁盘。
- 单进程设计：BLE 与管理命令在独立线程，SQLite 使用 WAL，适合树莓派单机部署。
