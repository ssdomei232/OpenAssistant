import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import BatteryFullIcon from '@mui/icons-material/BatteryFull';
import BluetoothConnectedIcon from '@mui/icons-material/BluetoothConnected';
import BluetoothDisabledIcon from '@mui/icons-material/BluetoothDisabled';
import BluetoothSearchingIcon from '@mui/icons-material/BluetoothSearching';
import CloudSyncIcon from '@mui/icons-material/CloudSync';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import ListAltIcon from '@mui/icons-material/ListAlt';
import RefreshIcon from '@mui/icons-material/Refresh';
import ReplayIcon from '@mui/icons-material/Replay';
import RouterIcon from '@mui/icons-material/Router';
import WifiIcon from '@mui/icons-material/Wifi';
import WifiOffIcon from '@mui/icons-material/WifiOff';
import { api } from '../api/client';
import { useDevices } from '../api/queries';
import type { DeviceConfig } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ErrorAlert } from '../components/ErrorAlert';
import { useFeedback } from '../components/FeedbackProvider';
import { formatDateTime, relativeLabel } from '../lib/format';
import {
  connectAgent,
  formatUptime,
  getBleSupport,
  readJson,
  requestQueue,
  subscribeStatus,
  writeJson,
  type AgentConfig,
  type AgentConnection,
  type AgentStatus,
  type QueueResponse,
} from '../platform/ble';

function logTime(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

export function DevicePage() {
  const { notify } = useFeedback();
  const bleSupport = getBleSupport();
  const supported = bleSupport.supported;
  const devicesQuery = useDevices();

  const [connection, setConnection] = useState<AgentConnection | null>(null);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [config, setConfig] = useState({
    ssid: '',
    password: '',
    retryMax: '5',
    offlineBuffer: true,
    uploadEndpoint: '',
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [queueResponse, setQueueResponse] = useState<QueueResponse | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const connectionRef = useRef<AgentConnection | null>(null);

  useEffect(() => {
    connectionRef.current = connection;
  }, [connection]);

  const appendLog = useCallback((message: string) => {
    setLogs((previous) => [`${logTime()}  ${message}`, ...previous].slice(0, 10));
  }, []);

  const handleDisconnected = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    setConnection(null);
    setStatus(null);
    appendLog('设备连接已断开');
    notify('蓝牙连接已断开', 'warning');
  }, [appendLog, notify]);

  useEffect(() => {
    return () => {
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
      const conn = connectionRef.current;
      if (conn) {
        try {
          conn.device.removeEventListener?.('gattserverdisconnected', handleDisconnected);
          conn.server.disconnect();
        } catch {
          // 页面卸载时忽略断开异常
        }
      }
    };
  }, [handleDisconnected]);

  const handleConnect = async () => {
    setBusy('connect');
    setError(null);
    try {
      appendLog('正在请求蓝牙设备（名称前缀 OpenAssistant）…');
      const conn = await connectAgent();
      setConnection(conn);
      conn.device.addEventListener('gattserverdisconnected', handleDisconnected);
      appendLog(`已连接：${conn.device.name ?? conn.device.id}`);

      try {
        const initial = await readJson<AgentStatus>(conn.status);
        setStatus(initial);
        appendLog('已读取 STATUS');
      } catch {
        appendLog('读取 STATUS 失败，等待 notify');
      }

      try {
        const initialConfig = await readJson<AgentConfig>(conn.config);
        setConfig((previous) => ({
          ...previous,
          ssid: initialConfig.wifi?.ssid ?? '',
          retryMax: String(initialConfig.retry_max ?? 5),
          offlineBuffer: initialConfig.offline_buffer ?? true,
          uploadEndpoint: initialConfig.upload_endpoint ?? '',
        }));
        appendLog('已读取 CONFIG');
      } catch {
        appendLog('读取 CONFIG 失败，可继续写入配置');
      }

      try {
        const unsubscribe = await subscribeStatus(conn.status, (next) => {
          setStatus(next as AgentStatus);
          appendLog('收到 STATUS 通知');
        });
        unsubscribeRef.current = unsubscribe;
      } catch {
        appendLog('订阅 STATUS 通知失败');
      }

      notify('设备已连接');
    } catch (caught) {
      const message =
        caught instanceof Error
          ? caught.name === 'NotFoundError'
            ? '未选择设备，或附近没有名为 OpenAssistant* 的设备。'
            : caught.message
          : '连接失败';
      setError(message);
      appendLog(`连接失败：${message}`);
      notify(message, 'error');
    } finally {
      setBusy(null);
    }
  };

  const handleDisconnect = () => {
    if (!connection) return;
    try {
      connection.device.removeEventListener?.('gattserverdisconnected', handleDisconnected);
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
      connection.server.disconnect();
      appendLog('已主动断开连接');
    } finally {
      setConnection(null);
      setStatus(null);
    }
  };

  const handleRefreshStatus = async () => {
    if (!connection) return;
    setBusy('status');
    try {
      const next = await readJson<AgentStatus>(connection.status);
      setStatus(next);
      notify('状态已刷新');
      appendLog('手动刷新 STATUS');
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : '读取状态失败', 'error');
    } finally {
      setBusy(null);
    }
  };

  const handleSaveConfig = async () => {
    if (!connection) return;
    if (!config.ssid.trim()) {
      notify('请填写 WiFi SSID', 'warning');
      return;
    }
    const retryMax = Number(config.retryMax);
    if (!Number.isFinite(retryMax) || retryMax < 0 || retryMax > 20) {
      notify('失败重传次数需在 0–20 之间', 'warning');
      return;
    }
    const payload: AgentConfig = {
      wifi: { ssid: config.ssid.trim(), password: config.password },
      retry_max: Math.round(retryMax),
      offline_buffer: config.offlineBuffer,
    };
    if (config.uploadEndpoint.trim()) payload.upload_endpoint = config.uploadEndpoint.trim();

    setBusy('config');
    try {
      await writeJson(connection.config, payload);
      appendLog('CONFIG 写入成功（分片 ≤200 字节）');
      try {
        const readBack = await readJson<AgentConfig>(connection.config);
        appendLog(`CONFIG 回读：retry_max=${readBack.retry_max ?? '?'}`);
      } catch {
        appendLog('CONFIG 回读失败（不影响写入）');
      }
      notify('配置已写入设备');

      const deviceId = status?.device_id || connection.device.name || 'unknown-device';
      try {
        await api.putDeviceConfig(deviceId, payload as DeviceConfig);
        appendLog(`Server 配置备份成功（${deviceId}）`);
        void devicesQuery.refetch();
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : '未知错误';
        appendLog(`Server 配置备份失败：${message}`);
        notify('设备已保存，但 Server 备份失败（不影响使用）', 'warning');
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '写入失败';
      appendLog(`CONFIG 写入失败：${message}`);
      notify(message, 'error');
    } finally {
      setBusy(null);
    }
  };

  const runQueueAction = async (action: 'list' | 'retry' | 'clear') => {
    if (!connection) return;
    setBusy(action);
    try {
      const response = await requestQueue(connection.queue, action);
      setQueueResponse(response ?? { action, result: '未收到设备响应' });
      appendLog(`QUEUE ${action} 完成`);
      notify(
        action === 'list' ? '已获取待上传队列' : action === 'retry' ? '已触发重传' : '已清空队列',
      );
      try {
        const next = await readJson<AgentStatus>(connection.status);
        setStatus(next);
      } catch {
        // 状态读取失败不影响队列操作结果
      }
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : '队列操作失败', 'error');
    } finally {
      setBusy(null);
      setClearOpen(false);
    }
  };

  return (
    <Box>
      <Stack spacing={2}>
        {!supported ? (
          <Alert severity="warning" icon={<BluetoothDisabledIcon />} role="alert">
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              当前环境不支持蓝牙
            </Typography>
            <Typography variant="body2" component="div">
              {bleSupport.message}
            </Typography>
          </Alert>
        ) : null}

        {supported ? (
          <Alert severity="info" variant="outlined" icon={<BluetoothSearchingIcon />}>
            蓝牙通道：{bleSupport.platform === 'native' ? '原生 BLE（App）' : 'Web Bluetooth（浏览器）'}。
            {bleSupport.message}
          </Alert>
        ) : null}

        {/* 设备连接 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              {connection ? (
                <BluetoothConnectedIcon color="primary" />
              ) : (
                <BluetoothSearchingIcon color="disabled" />
              )}
              <Typography component="h2" variant="subtitle1" sx={{ flex: 1 }}>
                Agent 设备（BLE）
              </Typography>
              <Chip
                size="small"
                label={connection ? '蓝牙已连接' : '未连接'}
                color={connection ? 'success' : 'default'}
                variant="outlined"
              />
            </Stack>

            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              通过蓝牙管理树莓派 Agent：写入 WiFi、失败重传次数与无网暂存开关，查看待上传队列。
              服务 UUID <code>a001</code>，配置/状态/队列特征分别为 <code>a002/a003/a004</code>。
            </Typography>

            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
              <Button
                variant="contained"
                startIcon={
                  busy === 'connect' ? (
                    <CircularProgress size={16} color="inherit" />
                  ) : (
                    <BluetoothSearchingIcon />
                  )
                }
                onClick={handleConnect}
                disabled={!supported || busy === 'connect' || Boolean(connection)}
                aria-label="连接 Agent 设备"
              >
                {busy === 'connect' ? '连接中…' : connection ? '已连接' : '连接设备'}
              </Button>
              <Button
                variant="outlined"
                onClick={handleRefreshStatus}
                disabled={!connection || busy === 'status'}
                startIcon={<RefreshIcon />}
                aria-label="刷新设备状态"
              >
                刷新状态
              </Button>
              <Button
                variant="text"
                color="error"
                onClick={handleDisconnect}
                disabled={!connection}
                aria-label="断开设备连接"
              >
                断开
              </Button>
            </Stack>

            {error ? (
              <Box sx={{ mt: 1.5 }}>
                <Alert severity="error" variant="outlined" role="alert">
                  {error}
                </Alert>
              </Box>
            ) : null}
          </CardContent>
        </Card>

        {/* 设备状态 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
              <RouterIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1" sx={{ flex: 1 }}>
                设备状态
              </Typography>
              {status?.device_id ? <Chip size="small" label={status.device_id} variant="outlined" /> : null}
            </Stack>

            {!status ? (
              <Typography variant="body2" color="text.secondary">
                连接设备后可查看 WiFi、录音、队列与离线暂存状态。
              </Typography>
            ) : (
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: { xs: '1fr 1fr', sm: 'repeat(4, 1fr)' },
                  gap: 1.25,
                }}
              >
                <Stat
                  icon={status.wifi_connected ? <WifiIcon /> : <WifiOffIcon />}
                  label="WiFi"
                  value={status.wifi_connected ? '已联网' : '未联网'}
                  tone={status.wifi_connected ? 'success' : 'error'}
                />
                <Stat
                  icon={<CloudSyncIcon />}
                  label="待上传队列"
                  value={`${status.queue ?? 0} 条`}
                />
                <Stat
                  icon={<BluetoothConnectedIcon />}
                  label="录音"
                  value={status.recording ? '录音中' : '待机'}
                  tone={status.recording ? 'primary' : 'default'}
                />
                <Stat icon={<RefreshIcon />} label="运行时长" value={formatUptime(status.uptime_s)} />
                {typeof status.battery === 'number' ? (
                  <Stat icon={<BatteryFullIcon />} label="电量" value={`${status.battery}%`} />
                ) : null}
                <Stat label="重试上限" value={`${status.retry_max ?? '—'} 次`} />
                <Stat
                  label="无网暂存"
                  value={status.offline_buffer ? '已开启' : '已关闭'}
                  tone={status.offline_buffer ? 'success' : 'default'}
                />
                {status.wifi_ssid ? <Stat label="SSID" value={String(status.wifi_ssid)} /> : null}
              </Box>
            )}
          </CardContent>
        </Card>

        {/* 配置写入 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <WifiIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1">
                WiFi 与上传配置
              </Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
              JSON 单次写入上限 512 字节；App 会按 200 字节分片追加。写入成功后会同时调用
              Server <code>PUT /devices/{'{device_id}'}/config</code> 备份（失败不阻塞）。
            </Typography>

            <Stack spacing={2}>
              <TextField
                label="WiFi SSID"
                value={config.ssid}
                onChange={(event) => setConfig({ ...config, ssid: event.target.value })}
                fullWidth
                disabled={!connection}
                autoComplete="off"
              />
              <TextField
                label="WiFi 密码"
                type="password"
                value={config.password}
                onChange={(event) => setConfig({ ...config, password: event.target.value })}
                fullWidth
                disabled={!connection}
                autoComplete="new-password"
                helperText="写入后由 Agent 保存；服务端备份中的密码会被打码。"
              />
              <TextField
                label="失败重传最大次数"
                type="number"
                value={config.retryMax}
                onChange={(event) => setConfig({ ...config, retryMax: event.target.value })}
                fullWidth
                disabled={!connection}
                slotProps={{ htmlInput: { min: 0, max: 20, step: 1 } }}
                helperText="0 表示不重试；建议 3–5 次。"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={config.offlineBuffer}
                    onChange={(event) => setConfig({ ...config, offlineBuffer: event.target.checked })}
                    disabled={!connection}
                    inputProps={{ 'aria-label': '无网暂存开关' }}
                  />
                }
                label="无网暂存（断网时先保存到本地，恢复后上传）"
              />
              <TextField
                label="上传地址（可选）"
                value={config.uploadEndpoint}
                onChange={(event) => setConfig({ ...config, uploadEndpoint: event.target.value })}
                fullWidth
                disabled={!connection}
                placeholder="https://your-server/api/v1"
                helperText="留空则沿用 Agent 当前配置。"
              />
              <Button
                variant="contained"
                onClick={handleSaveConfig}
                disabled={!connection || busy === 'config'}
                startIcon={busy === 'config' ? <CircularProgress size={16} color="inherit" /> : undefined}
                aria-label="写入设备配置"
              >
                {busy === 'config' ? '写入中…' : '写入配置'}
              </Button>
            </Stack>
          </CardContent>
        </Card>

        {/* 队列 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <ListAltIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1">
                待上传队列
              </Typography>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              通过 QUEUE 特征（a004）执行 list / retry / clear。队列清理不可恢复，请谨慎操作。
            </Typography>
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1, mb: 1.5 }}>
              <Button
                variant="outlined"
                startIcon={<ListAltIcon />}
                onClick={() => void runQueueAction('list')}
                disabled={!connection || Boolean(busy)}
                aria-label="查看待上传队列"
              >
                查看队列
              </Button>
              <Button
                variant="outlined"
                startIcon={<ReplayIcon />}
                onClick={() => void runQueueAction('retry')}
                disabled={!connection || Boolean(busy)}
                aria-label="手动重传队列"
              >
                手动重传
              </Button>
              <Button
                variant="outlined"
                color="error"
                startIcon={<DeleteSweepIcon />}
                onClick={() => setClearOpen(true)}
                disabled={!connection || Boolean(busy)}
                aria-label="清空待上传队列"
              >
                清空队列
              </Button>
            </Stack>
            {queueResponse ? (
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 1.5,
                  borderRadius: 3,
                  bgcolor: 'action.hover',
                  fontSize: '0.78rem',
                  overflowX: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {JSON.stringify(queueResponse, null, 2)}
              </Box>
            ) : null}
          </CardContent>
        </Card>

        {/* 日志 */}
        <Card>
          <CardContent>
            <Typography component="h2" variant="subtitle1" sx={{ mb: 1 }}>
              连接日志
            </Typography>
            {logs.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                尚未进行蓝牙操作。
              </Typography>
            ) : (
              <Box
                component="ul"
                role="log"
                aria-live="polite"
                sx={{ m: 0, pl: 2, fontSize: '0.78rem', color: 'text.secondary' }}
              >
                {logs.map((line, index) => (
                  <li key={`${line}-${index}`} style={{ marginBottom: 4 }}>
                    {line}
                  </li>
                ))}
              </Box>
            )}
          </CardContent>
        </Card>

        {/* Server 备份 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <CloudSyncIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1" sx={{ flex: 1 }}>
                Server 端设备备份
              </Typography>
              <Button size="small" onClick={() => void devicesQuery.refetch()} aria-label="刷新设备列表">
                刷新
              </Button>
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
              来自 <code>GET /devices</code>，用于无蓝牙环境时核对最近一次写入的配置。
            </Typography>

            {devicesQuery.isLoading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
                <CircularProgress size={26} />
              </Box>
            ) : devicesQuery.isError ? (
              <ErrorAlert error={devicesQuery.error} onRetry={() => void devicesQuery.refetch()} />
            ) : (devicesQuery.data?.items.length ?? 0) === 0 ? (
              <Typography variant="body2" color="text.secondary">
                服务器上还没有已注册的设备。
              </Typography>
            ) : (
              <Stack spacing={1.5} divider={<Divider flexItem />}>
                {devicesQuery.data?.items.map((device) => (
                  <Box key={device.id}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {device.name}
                      </Typography>
                      <Chip
                        size="small"
                        label={device.status}
                        color={device.status === 'online' ? 'success' : 'default'}
                        variant="outlined"
                      />
                      <Box sx={{ flex: 1 }} />
                      <Typography variant="caption" color="text.secondary">
                        {device.last_seen ? relativeLabel(device.last_seen) : '—'}
                      </Typography>
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                      ID：{device.id}
                      {device.last_seen ? ` · 最后在线 ${formatDateTime(device.last_seen)}` : ''}
                    </Typography>
                    <Box
                      component="pre"
                      sx={{
                        m: 0,
                        mt: 0.75,
                        p: 1.25,
                        borderRadius: 2,
                        bgcolor: 'action.hover',
                        fontSize: '0.72rem',
                        overflowX: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}
                    >
                      {JSON.stringify(device.config, null, 2)}
                    </Box>
                  </Box>
                ))}
              </Stack>
            )}
          </CardContent>
        </Card>
      </Stack>

      <ConfirmDialog
        open={clearOpen}
        title="清空待上传队列？"
        description="将删除 Agent 本地尚未上传的音频片段，删除后无法恢复。请确认这些片段已经不再需要。"
        confirmLabel="清空队列"
        destructive
        loading={busy === 'clear'}
        onCancel={() => setClearOpen(false)}
        onConfirm={() => void runQueueAction('clear')}
      />
    </Box>
  );
}

function Stat({
  icon,
  label,
  value,
  tone = 'default',
}: {
  icon?: ReactNode;
  label: string;
  value: string;
  tone?: 'default' | 'primary' | 'success' | 'error';
}) {
  const color =
    tone === 'primary'
      ? 'primary.main'
      : tone === 'success'
        ? 'success.main'
        : tone === 'error'
          ? 'error.main'
          : 'text.primary';
  return (
    <Box sx={{ p: 1.25, borderRadius: 3, border: '1px solid', borderColor: 'divider' }}>
      <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mb: 0.5, color: 'text.secondary' }}>
        {icon ? <Box sx={{ display: 'flex', '& svg': { fontSize: 16 } }}>{icon}</Box> : null}
        <Typography variant="caption">{label}</Typography>
      </Stack>
      <Typography variant="body2" sx={{ fontWeight: 700, color }}>
        {value}
      </Typography>
    </Box>
  );
}
