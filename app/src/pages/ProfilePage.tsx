import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Avatar from '@mui/material/Avatar';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import AddIcon from '@mui/icons-material/Add';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import BluetoothIcon from '@mui/icons-material/Bluetooth';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import KeyIcon from '@mui/icons-material/Key';
import MicNoneIcon from '@mui/icons-material/MicNone';
import PersonIcon from '@mui/icons-material/Person';
import SettingsIcon from '@mui/icons-material/Settings';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import {
  useDeleteVoiceprint,
  useMe,
  useProfile,
  useRefreshProfile,
  useUpdateMe,
  useUpdateProfile,
  useUploadVoiceprint,
  useVoiceprint,
} from '../api/queries';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { useFeedback } from '../components/FeedbackProvider';
import { useConfig } from '../state/config';
import { formatDateTime } from '../lib/format';

const MAX_PROFILE_LENGTH = 600;
const MAX_ALIASES = 20;

export function ProfilePage() {
  const navigate = useNavigate();
  const { notify } = useFeedback();
  const { config } = useConfig();
  const meQuery = useMe();
  const profileQuery = useProfile();
  const voiceprintQuery = useVoiceprint();
  const updateMe = useUpdateMe();
  const updateProfile = useUpdateProfile();
  const refreshProfile = useRefreshProfile();
  const uploadVoiceprint = useUploadVoiceprint();
  const deleteVoiceprint = useDeleteVoiceprint();

  const me = meQuery.data;
  const profile = profileQuery.data;
  const voiceprint = voiceprintQuery.data;

  const [profileText, setProfileText] = useState('');
  const [aliases, setAliases] = useState<string[]>([]);
  const [aliasInput, setAliasInput] = useState('');
  const [editOpen, setEditOpen] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [deleteVoiceOpen, setDeleteVoiceOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (profile) setProfileText(profile.text);
  }, [profile]);

  useEffect(() => {
    if (me) setAliases(me.aliases);
  }, [me]);

  const syncAliases = (next: string[]) => {
    setAliases(next);
    updateMe.mutate(
      { aliases: next },
      {
        onSuccess: () => notify('称呼已同步到服务器'),
        onError: (error) => notify(error.message || '同步失败', 'error'),
      },
    );
  };

  const addAlias = () => {
    const value = aliasInput.trim();
    if (!value) return;
    if (aliases.length >= MAX_ALIASES) {
      notify(`最多只能设置 ${MAX_ALIASES} 个称呼`, 'warning');
      return;
    }
    if (value.length > 64) {
      notify('单个称呼不能超过 64 个字符', 'warning');
      return;
    }
    if (aliases.includes(value)) {
      notify('该称呼已存在', 'info');
      return;
    }
    setAliasInput('');
    syncAliases([...aliases, value]);
  };

  const removeAlias = (alias: string) => {
    syncAliases(aliases.filter((item) => item !== alias));
  };

  const saveProfile = () => {
    if (profileText.length > MAX_PROFILE_LENGTH) {
      notify(`画像不能超过 ${MAX_PROFILE_LENGTH} 字符`, 'warning');
      return;
    }
    updateProfile.mutate(profileText, {
      onSuccess: () => notify('用户画像已保存'),
      onError: (error) => notify(error.message || '保存失败', 'error'),
    });
  };

  const handleRefreshProfile = () => {
    refreshProfile.mutate(undefined, {
      onSuccess: () => notify('已根据最近卡片重新生成画像'),
      onError: (error) => notify(error.message || '重新生成失败', 'error'),
    });
  };

  const handleVoiceFile = (file: File | undefined) => {
    if (!file) return;
    uploadVoiceprint.mutate(
      { file, threshold: voiceprint?.threshold ?? 60 },
      {
        onSuccess: (result) => notify(`声纹注册成功，质量 ${Math.round(result.quality * 100)}%`),
        onError: (error) => notify(error.message || '声纹注册失败', 'error'),
      },
    );
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const profileLength = profileText.length;

  return (
    <Box>
      <Stack spacing={2}>
        {/* 用户信息 */}
        <Card>
          <CardContent>
            <Stack direction="row" spacing={2} alignItems="center">
              <Avatar
                sx={{
                  width: 64,
                  height: 64,
                  bgcolor: 'primary.main',
                  color: 'primary.contrastText',
                  fontSize: 28,
                  fontWeight: 700,
                }}
                aria-label="用户头像"
              >
                {me?.display_name?.slice(0, 1) ?? <PersonIcon />}
              </Avatar>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="h5" component="h2">
                  {me?.display_name ?? '未命名用户'}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  时区：{me?.timezone ?? '—'}
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  setDisplayName(me?.display_name ?? '');
                  setEditOpen(true);
                }}
                aria-label="编辑用户资料"
              >
                编辑
              </Button>
            </Stack>

            <Divider sx={{ my: 2 }} />

            <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>
              姓名称呼
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              这些称呼会同步给 Server，作为 AI 提示词中识别「使用者」的依据（最多 {MAX_ALIASES} 个）。
            </Typography>
            {aliases.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                还没有设置称呼。
              </Typography>
            ) : (
              <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1, mb: 1.5 }}>
                {aliases.map((alias) => (
                  <Chip
                    key={alias}
                    label={alias}
                    onDelete={() => removeAlias(alias)}
                    deleteIcon={<DeleteOutlineIcon aria-label={`删除称呼 ${alias}`} />}
                    color="primary"
                    variant="outlined"
                  />
                ))}
              </Stack>
            )}
            <Stack direction="row" spacing={1} alignItems="flex-start">
              <TextField
                label="添加称呼"
                value={aliasInput}
                onChange={(event) => setAliasInput(event.target.value)}
                size="small"
                fullWidth
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    addAlias();
                  }
                }}
                slotProps={{ htmlInput: { maxLength: 64 } }}
              />
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={addAlias}
                disabled={updateMe.isPending || !aliasInput.trim()}
                aria-label="添加称呼"
              >
                添加
              </Button>
            </Stack>
          </CardContent>
        </Card>

        {/* 用户画像 */}
        <Card>
          <CardContent>
            <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
              <Typography component="h3" variant="subtitle1" sx={{ flex: 1 }}>
                用户画像
              </Typography>
              <Typography
                variant="caption"
                color={profileLength > MAX_PROFILE_LENGTH ? 'error' : 'text.secondary'}
              >
                {profileLength}/{MAX_PROFILE_LENGTH}
              </Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.25 }}>
              画像会作为所有 AI 任务的提示词，请保持简短；版本 v{profile?.version ?? '—'}
              {profile?.updated_at ? ` · 更新于 ${formatDateTime(profile.updated_at)}` : ''}
            </Typography>

            {profileQuery.isLoading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                <CircularProgress size={28} />
              </Box>
            ) : profileQuery.isError ? (
              <ErrorAlert error={profileQuery.error} onRetry={() => void profileQuery.refetch()} />
            ) : (
              <>
                <TextField
                  value={profileText}
                  onChange={(event) => setProfileText(event.target.value)}
                  multiline
                  minRows={5}
                  fullWidth
                  aria-label="编辑用户画像"
                  error={profileLength > MAX_PROFILE_LENGTH}
                  helperText={
                    profileLength > MAX_PROFILE_LENGTH
                      ? `超出 ${profileLength - MAX_PROFILE_LENGTH} 个字符`
                      : '例如：30 岁软件工程师，常住上海；关注 AI 与开源；偏好简短沟通。'
                  }
                />
                <Stack direction="row" spacing={1} sx={{ mt: 1.5, flexWrap: 'wrap', rowGap: 1 }}>
                  <Button
                    variant="contained"
                    onClick={saveProfile}
                    disabled={updateProfile.isPending || profileLength > MAX_PROFILE_LENGTH}
                    aria-label="保存用户画像"
                  >
                    {updateProfile.isPending ? '保存中…' : '保存'}
                  </Button>
                  <Button
                    variant="outlined"
                    startIcon={
                      refreshProfile.isPending ? (
                        <CircularProgress size={16} color="inherit" />
                      ) : (
                        <AutoAwesomeIcon />
                      )
                    }
                    onClick={handleRefreshProfile}
                    disabled={refreshProfile.isPending}
                    aria-label="重新生成用户画像"
                  >
                    {refreshProfile.isPending ? '生成中…' : '重新生成'}
                  </Button>
                </Stack>
              </>
            )}
          </CardContent>
        </Card>

        {/* 声纹 */}
        <Card>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <VerifiedUserIcon fontSize="small" color={voiceprint?.enrolled ? 'success' : 'disabled'} />
              <Typography component="h3" variant="subtitle1" sx={{ flex: 1 }}>
                声纹
              </Typography>
              <Chip
                size="small"
                label={voiceprint?.enrolled ? '已注册' : '未注册'}
                color={voiceprint?.enrolled ? 'success' : 'default'}
                variant="outlined"
              />
            </Stack>

            {voiceprint?.enrolled ? (
              <Stack spacing={0.5} sx={{ mb: 1.5 }}>
                <Typography variant="body2" color="text.secondary">
                  阈值 {voiceprint.threshold}
                  {typeof voiceprint.quality === 'number'
                    ? ` · 质量 ${Math.round(voiceprint.quality * 100)}%`
                    : ''}
                </Typography>
                {voiceprint.updated_at ? (
                  <Typography variant="caption" color="text.secondary">
                    更新于 {formatDateTime(voiceprint.updated_at)}
                  </Typography>
                ) : null}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                未注册声纹时，所有说话人都会被标记为「未知」，指令不会触发。请上传 10–30 秒干净人声。
              </Typography>
            )}

            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
              <Button
                component="label"
                variant="outlined"
                startIcon={
                  uploadVoiceprint.isPending ? (
                    <CircularProgress size={16} color="inherit" />
                  ) : (
                    <UploadFileIcon />
                  )
                }
                disabled={uploadVoiceprint.isPending}
                aria-label="上传声纹音频"
              >
                {uploadVoiceprint.isPending ? '上传中…' : voiceprint?.enrolled ? '重新注册' : '上传音频注册'}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="audio/*,.wav,.mp3,.m4a,.flac"
                  hidden
                  onChange={(event) => handleVoiceFile(event.target.files?.[0])}
                />
              </Button>
              {voiceprint?.enrolled ? (
                <Button
                  variant="text"
                  color="error"
                  onClick={() => setDeleteVoiceOpen(true)}
                  aria-label="删除声纹"
                >
                  删除声纹
                </Button>
              ) : null}
            </Stack>
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mt: 1.5 }}>
              <MicNoneIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
              <Typography variant="caption" color="text.secondary">
                支持 wav / mp3 / m4a / flac，建议安静环境下单独说话。
              </Typography>
            </Stack>
          </CardContent>
        </Card>

        {/* 配置入口 */}
        <Card>
          <List disablePadding>
            <ListItemButton onClick={() => navigate('/settings')} aria-label="服务器与 API Key 设置">
              <ListItemIcon>
                <KeyIcon color={config.apiKey ? 'success' : 'disabled'} />
              </ListItemIcon>
              <ListItemText
                primary="服务器与 API Key"
                secondary={
                  config.apiKey
                    ? `${config.serverUrl || '同源 /api/v1'} · 已配置 Key`
                    : '未配置，当前使用演示数据'
                }
              />
              <ChevronRightIcon fontSize="small" />
            </ListItemButton>
            <Divider />
            <ListItemButton onClick={() => navigate('/device')} aria-label="设备管理">
              <ListItemIcon>
                <BluetoothIcon />
              </ListItemIcon>
              <ListItemText primary="设备管理（蓝牙）" secondary="WiFi、重传、离线暂存与队列" />
              <ChevronRightIcon fontSize="small" />
            </ListItemButton>
            <Divider />
            <ListItemButton onClick={() => navigate('/settings')} aria-label="设置与审计记录">
              <ListItemIcon>
                <SettingsIcon />
              </ListItemIcon>
              <ListItemText primary="设置与审计记录" secondary="搜索工具、通知开关、钉钉自检、勘误审计" />
              <ChevronRightIcon fontSize="small" />
            </ListItemButton>
          </List>
        </Card>

        {!meQuery.isLoading && meQuery.isError ? (
          <ErrorAlert error={meQuery.error} onRetry={() => void meQuery.refetch()} />
        ) : null}

        {!voiceprintQuery.isLoading && voiceprintQuery.isError ? (
          <ErrorAlert error={voiceprintQuery.error} onRetry={() => void voiceprintQuery.refetch()} />
        ) : null}

        {!meQuery.isLoading && !me && !meQuery.isError ? (
          <EmptyState title="没有账户信息" description="请检查 API Key 或服务器地址。" />
        ) : null}
      </Stack>

      <Dialog open={editOpen} onClose={() => setEditOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>编辑资料</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              label="显示名称"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              fullWidth
              slotProps={{ htmlInput: { maxLength: 64 } }}
            />
            <TextField label="时区" value={me?.timezone ?? ''} fullWidth disabled />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setEditOpen(false)} color="inherit">
            取消
          </Button>
          <Button
            variant="contained"
            disabled={updateMe.isPending || !displayName.trim()}
            onClick={() =>
              updateMe.mutate(
                { display_name: displayName.trim() },
                {
                  onSuccess: () => {
                    notify('资料已保存');
                    setEditOpen(false);
                  },
                  onError: (error) => notify(error.message || '保存失败', 'error'),
                },
              )
            }
          >
            保存
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={deleteVoiceOpen}
        title="删除声纹？"
        description="删除后所有说话人识别结果会降级为「未知」，需要重新注册才能恢复。"
        confirmLabel="删除声纹"
        destructive
        loading={deleteVoiceprint.isPending}
        onCancel={() => setDeleteVoiceOpen(false)}
        onConfirm={() => {
          deleteVoiceprint.mutate(undefined, {
            onSuccess: () => {
              notify('声纹已删除');
              setDeleteVoiceOpen(false);
            },
            onError: (error) => notify(error.message || '删除失败', 'error'),
          });
        }}
      />
    </Box>
  );
}
