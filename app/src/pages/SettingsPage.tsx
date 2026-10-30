import { useEffect, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Radio from '@mui/material/Radio';
import RadioGroup from '@mui/material/RadioGroup';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import CloudDoneIcon from '@mui/icons-material/CloudDone';
import HistoryEduIcon from '@mui/icons-material/HistoryEdu';
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive';
import SearchIcon from '@mui/icons-material/Search';
import SendIcon from '@mui/icons-material/Send';
import TuneIcon from '@mui/icons-material/Tune';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import { api } from '../api/client';
import type { Health, MeSettings, SearchProvider } from '../api/types';
import {
  useAudit,
  useMe,
  usePushTest,
  useSettings,
  useUpdateSettings,
} from '../api/queries';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { useFeedback } from '../components/FeedbackProvider';
import { useConfig } from '../state/config';
import { formatDateTime, relativeLabel } from '../lib/format';

const ACTION_LABEL: Record<string, string> = {
  'card.correct': '卡片勘误',
  'card.delete': '卡片删除',
  'card.restore': '卡片恢复',
  'todo.delete': '待办删除',
  'schedule.delete': '日程删除',
  'voiceprint.enroll': '声纹注册',
  'voiceprint.delete': '声纹删除',
  'device.config': '设备配置备份',
  'push.test': '钉钉推送自检',
  'me.update': '账户更新',
  'profile.update': '画像修改',
};

function auditValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

interface TestResult {
  ok: boolean;
  health?: Health;
  name?: string;
  message?: string;
}

export function SettingsPage() {
  const { notify } = useFeedback();
  const { config, updateConfig, reset } = useConfig();
  const settingsQuery = useSettings();
  const meQuery = useMe();
  const updateSettings = useUpdateSettings();
  const pushTest = usePushTest();
  const auditQuery = useAudit({ limit: 20 });

  const settings = settingsQuery.data;
  const [serverUrl, setServerUrl] = useState(config.serverUrl);
  const [apiKey, setApiKey] = useState(config.apiKey);
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [resetOpen, setResetOpen] = useState(false);

  useEffect(() => {
    setServerUrl(config.serverUrl);
    setApiKey(config.apiKey);
  }, [config.serverUrl, config.apiKey]);

  const demoActive = config.demoMode || !config.apiKey.trim();

  const saveConnection = () => {
    if (serverUrl.trim() && !/^https?:\/\//i.test(serverUrl.trim())) {
      notify('服务器地址需以 http:// 或 https:// 开头', 'warning');
      return;
    }
    updateConfig({ serverUrl: serverUrl.trim().replace(/\/+$/, ''), apiKey: apiKey.trim() });
    notify('连接配置已保存');
  };

  const handleTest = async () => {
    if (serverUrl.trim() && !/^https?:\/\//i.test(serverUrl.trim())) {
      notify('服务器地址需以 http:// 或 https:// 开头', 'warning');
      return;
    }
    updateConfig({ serverUrl: serverUrl.trim().replace(/\/+$/, ''), apiKey: apiKey.trim() });
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testConnection();
      setTestResult({ ok: true, health: result.health, name: result.me.display_name });
      notify('连接成功');
    } catch (error) {
      const message = error instanceof Error ? error.message : '连接失败';
      setTestResult({ ok: false, message });
      notify(`连接失败：${message}`, 'error');
    } finally {
      setTesting(false);
    }
  };

  const patchSettings = (patch: Partial<MeSettings>) => {
    updateSettings.mutate(patch, {
      onSuccess: () => notify('设置已更新'),
      onError: (error) => notify(error.message || '更新失败', 'error'),
    });
  };

  const handlePushTest = () => {
    pushTest.mutate('OpenAssistant 测试消息：钉钉推送通道正常', {
      onSuccess: (result) =>
        notify(
          result.sent
            ? `推送成功（${result.provider}）`
            : `未发送：${result.reason === 'not_configured' ? '尚未配置钉钉 Webhook' : result.reason ?? '未知原因'}`,
          result.sent ? 'success' : 'warning',
        ),
      onError: (error) => notify(error.message || '推送失败', 'error'),
    });
  };

  const switchSetting = (
    key: 'push_enabled' | 'auto_schedule' | 'auto_profile_update' | 'correction_notice',
    checked: boolean,
  ) => patchSettings({ [key]: checked } as Partial<MeSettings>);

  return (
    <Box>
      <Stack spacing={2}>
        {/* 服务器连接 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <CloudDoneIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1" sx={{ flex: 1 }}>
                服务器连接
              </Typography>
              <Chip
                size="small"
                label={demoActive ? '演示模式' : '真实数据'}
                color={demoActive ? 'warning' : 'success'}
                variant="outlined"
              />
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
              服务器需支持 OpenAssistant API v1（基础路径 /api/v1）。留空表示与前端同源。
            </Typography>

            <Stack spacing={2}>
              <TextField
                label="服务器地址"
                value={serverUrl}
                onChange={(event) => setServerUrl(event.target.value)}
                placeholder="https://oa.example.com"
                fullWidth
                autoComplete="url"
                helperText="例如 http://192.168.1.10:8000；留空则使用同源 /api/v1"
              />
              <TextField
                label="API Key"
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Server 生成的 API Key"
                fullWidth
                autoComplete="off"
                helperText="以 X-API-Key 头发送；仅保存在本机 localStorage"
                slotProps={{
                  input: {
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          onClick={() => setShowKey((value) => !value)}
                          aria-label={showKey ? '隐藏 API Key' : '显示 API Key'}
                          edge="end"
                          size="small"
                        >
                          {showKey ? <VisibilityOffIcon /> : <VisibilityIcon />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  },
                }}
              />

              <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
                <Button variant="contained" onClick={saveConnection} aria-label="保存连接配置">
                  保存配置
                </Button>
                <Button
                  variant="outlined"
                  onClick={handleTest}
                  disabled={testing}
                  aria-label="测试连接"
                  startIcon={testing ? <CircularProgress size={16} color="inherit" /> : undefined}
                >
                  {testing ? '测试中…' : '测试连接'}
                </Button>
                <Box sx={{ flex: 1 }} />
                <Button
                  color="inherit"
                  startIcon={<DeleteSweepIcon />}
                  onClick={() => setResetOpen(true)}
                  aria-label="清除连接配置"
                >
                  清除
                </Button>
              </Stack>

              <FormControlLabel
                control={
                  <Switch
                    checked={config.demoMode}
                    onChange={(event) => {
                      updateConfig({ demoMode: event.target.checked });
                      notify(event.target.checked ? '已开启演示模式' : '已关闭演示模式');
                    }}
                    inputProps={{ 'aria-label': '演示模式开关' }}
                  />
                }
                label="演示模式（使用内置 fixtures，不请求服务器）"
              />

              {demoActive ? (
                <Alert severity="info" variant="outlined">
                  当前显示演示数据。填写并保存 API Key 后，应用会自动切换为真实服务器数据。
                </Alert>
              ) : null}

              {testResult ? (
                testResult.ok && testResult.health ? (
                  <Alert severity="success" variant="outlined" role="status">
                    连接成功：{testResult.name ?? '当前用户'} · Server v{testResult.health.version} · ASR{' '}
                    {testResult.health.mode.asr} / LLM {testResult.health.mode.llm} / 搜索{' '}
                    {testResult.health.mode.search}
                  </Alert>
                ) : (
                  <Alert severity="error" variant="outlined" role="alert">
                    连接失败：{testResult.message}
                  </Alert>
                )
              ) : null}
            </Stack>
          </CardContent>
        </Card>

        {/* 搜索工具 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
              <SearchIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1">
                搜索工具
              </Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              指令执行资料查询时使用；保存后 Server 立即生效。
            </Typography>
            {settingsQuery.isError ? (
              <ErrorAlert error={settingsQuery.error} onRetry={() => void settingsQuery.refetch()} />
            ) : (
              <RadioGroup
                value={settings?.search_provider ?? 'builtin'}
                onChange={(event) =>
                  patchSettings({ search_provider: event.target.value as SearchProvider })
                }
                aria-label="选择搜索工具"
              >
                <FormControlLabel
                  value="builtin"
                  control={<Radio />}
                  label={
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        Server 端搜索引擎
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        使用 Server 自实现的搜索与归纳（builtin）
                      </Typography>
                    </Box>
                  }
                />
                <FormControlLabel
                  value="deepseek"
                  control={<Radio />}
                  label={
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        AI 提供商查询
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        使用 AI 模型提供商的联网查询能力（deepseek）
                      </Typography>
                    </Box>
                  }
                />
              </RadioGroup>
            )}
          </CardContent>
        </Card>

        {/* 通知与自动化 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <NotificationsActiveIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1" sx={{ flex: 1 }}>
                通知与自动化
              </Typography>
              {updateSettings.isPending ? <CircularProgress size={18} /> : null}
            </Stack>
            {(
              [
                {
                  key: 'push_enabled',
                  label: '钉钉推送',
                  description: '指令结果、查询答案与定时提醒推送到钉钉',
                },
                {
                  key: 'auto_schedule',
                  label: '自动添加日程',
                  description: '从「使用者」的发言中自动提取日程',
                },
                {
                  key: 'auto_profile_update',
                  label: '自动更新画像',
                  description: '每完成一段总结后增量修正用户画像',
                },
                {
                  key: 'correction_notice',
                  label: '勘误提示',
                  description: '勘误/删除后推送「已按勘误更新」通知',
                },
              ] as const
            ).map((item) => (
              <FormControlLabel
                key={item.key}
                sx={{ display: 'flex', alignItems: 'flex-start', mx: 0, mb: 1 }}
                control={
                  <Switch
                    checked={settings?.[item.key] ?? true}
                    onChange={(event) => switchSetting(item.key, event.target.checked)}
                    disabled={!settings || updateSettings.isPending}
                    inputProps={{ 'aria-label': item.label }}
                    sx={{ mt: -0.5 }}
                  />
                }
                label={
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {item.label}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {item.description}
                    </Typography>
                  </Box>
                }
              />
            ))}
          </CardContent>
        </Card>

        {/* 钉钉自检 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <SendIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1">
                钉钉推送自检
              </Typography>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              发送一条测试消息，验证 Server 的钉钉 Webhook 配置是否可用。
            </Typography>
            <Button
              variant="outlined"
              startIcon={pushTest.isPending ? <CircularProgress size={16} /> : <SendIcon />}
              onClick={handlePushTest}
              disabled={pushTest.isPending}
              aria-label="发送钉钉测试消息"
            >
              {pushTest.isPending ? '发送中…' : '发送测试消息'}
            </Button>
          </CardContent>
        </Card>

        {/* 审计记录 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <HistoryEduIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1" sx={{ flex: 1 }}>
                审计记录
              </Typography>
              <Button
                size="small"
                onClick={() => void auditQuery.refetch()}
                aria-label="刷新审计记录"
              >
                刷新
              </Button>
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              勘误、删除、声纹与设备配置变更均可追溯（GET /audit）。
            </Typography>

            {auditQuery.isLoading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
                <CircularProgress size={26} />
              </Box>
            ) : auditQuery.isError ? (
              <ErrorAlert error={auditQuery.error} onRetry={() => void auditQuery.refetch()} />
            ) : (auditQuery.data?.items.length ?? 0) === 0 ? (
              <EmptyState title="暂无审计记录" description="进行勘误或删除后会在这里留下记录。" />
            ) : (
              <List dense disablePadding>
                {auditQuery.data?.items.map((entry, index) => (
                  <Box key={entry.id}>
                    {index > 0 ? <Divider component="li" /> : null}
                    <ListItem alignItems="flex-start" disableGutters>
                      <ListItemText
                        primary={
                          <Stack direction="row" spacing={1} alignItems="center" sx={{ flexWrap: 'wrap' }}>
                            <Chip
                              size="small"
                              label={ACTION_LABEL[entry.action] ?? entry.action}
                              variant="outlined"
                            />
                            <Typography variant="caption" color="text.secondary">
                              {entry.entity}:{entry.entity_id}
                            </Typography>
                            <Box sx={{ flex: 1 }} />
                            <Typography variant="caption" color="text.secondary">
                              {entry.at ? relativeLabel(entry.at) : ''}
                            </Typography>
                          </Stack>
                        }
                        secondary={
                          <Box sx={{ mt: 0.5 }}>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ display: 'block', wordBreak: 'break-all' }}
                            >
                              {auditValue(entry.before)} → {auditValue(entry.after)}
                            </Typography>
                            {entry.at ? (
                              <Typography variant="caption" color="text.secondary">
                                {formatDateTime(entry.at)}
                              </Typography>
                            ) : null}
                            {entry.note ? (
                              <Typography variant="caption" color="text.secondary">
                                {' · '}
                                {entry.note}
                              </Typography>
                            ) : null}
                          </Box>
                        }
                      />
                    </ListItem>
                  </Box>
                ))}
              </List>
            )}
          </CardContent>
        </Card>

        {/* 其他 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <TuneIcon fontSize="small" color="primary" />
              <Typography component="h2" variant="subtitle1">
                当前账户
              </Typography>
            </Stack>
            <Stack spacing={0.5}>
              <Typography variant="body2">
                显示名称：{meQuery.data?.display_name ?? '—'}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                称呼：{meQuery.data?.aliases.join('、') || '—'}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                声纹：{meQuery.data?.voiceprint.enrolled ? '已注册' : '未注册'} · 阈值{' '}
                {meQuery.data?.voiceprint.threshold ?? 60}
              </Typography>
            </Stack>
          </CardContent>
        </Card>
      </Stack>

      <ConfirmDialog
        open={resetOpen}
        title="清除连接配置？"
        description="将清除本机保存的服务器地址与 API Key，并回到演示模式。服务器上的数据不会受影响。"
        confirmLabel="清除配置"
        destructive
        onCancel={() => setResetOpen(false)}
        onConfirm={() => {
          reset();
          setServerUrl('');
          setApiKey('');
          setTestResult(null);
          setResetOpen(false);
          notify('已清除连接配置');
        }}
      />
    </Box>
  );
}
