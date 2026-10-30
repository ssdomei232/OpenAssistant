import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import MenuItem from '@mui/material/MenuItem';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import AudioFileIcon from '@mui/icons-material/AudioFile';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import DownloadIcon from '@mui/icons-material/Download';
import EditNoteIcon from '@mui/icons-material/EditNote';
import EventIcon from '@mui/icons-material/Event';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import HistoryEduIcon from '@mui/icons-material/HistoryEdu';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import { api, downloadAudio, downloadText } from '../api/client';
import { useCard, useDeleteCard, useUpdateCard } from '../api/queries';
import type { CardPatch, CardType, TranscriptTurn } from '../api/types';
import { SpeakerChip } from '../components/SpeakerChip';
import { SourceChip, TypeBadge, CARD_TYPE_LABEL } from '../components/Badges';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { useFeedback } from '../components/FeedbackProvider';
import {
  formatDateTime,
  formatDuration,
  formatSecondsClock,
  formatShortDateTime,
} from '../lib/format';

function correctionValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.join('、');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

const FIELD_LABEL: Record<string, string> = {
  title: '标题',
  summary: '摘要',
  key_points: '关键信息',
  type: '类型',
};

function TranscriptLine({ turn }: { turn: TranscriptTurn }) {
  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: 3,
        bgcolor: turn.speaker === 'user' ? 'action.hover' : 'transparent',
        border: '1px solid',
        borderColor: turn.speaker === 'user' ? 'primary.main' : 'divider',
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <SpeakerChip speaker={turn.speaker} label={turn.speaker_label} />
        <Typography variant="caption" color="text.secondary">
          {formatSecondsClock(turn.start)} – {formatSecondsClock(turn.end)}
        </Typography>
      </Stack>
      <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
        {turn.text}
      </Typography>
    </Box>
  );
}

export function CardDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { notify } = useFeedback();
  const cardQuery = useCard(id);
  const updateCard = useUpdateCard();
  const deleteCard = useDeleteCard();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [form, setForm] = useState({
    title: '',
    summary: '',
    keyPoints: '',
    type: 'conversation' as CardType,
    note: '',
  });
  const [downloading, setDownloading] = useState(false);
  const [exporting, setExporting] = useState(false);

  const card = cardQuery.data;

  useEffect(() => {
    if (!card) return;
    setForm({
      title: card.title,
      summary: card.summary,
      keyPoints: card.key_points.join('\n'),
      type: card.type,
      note: '',
    });
  }, [card]);

  const patch = useMemo<CardPatch | null>(() => {
    if (!card) return null;
    const next: CardPatch = {};
    if (form.title.trim() !== card.title) next.title = form.title.trim();
    if (form.summary.trim() !== card.summary) next.summary = form.summary.trim();
    const keyPoints = form.keyPoints
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    if (JSON.stringify(keyPoints) !== JSON.stringify(card.key_points)) next.key_points = keyPoints;
    if (form.type !== card.type) next.type = form.type;
    if (form.note.trim()) next.note = form.note.trim();
    return next;
  }, [card, form]);

  const handleDownloadAudio = async () => {
    if (!card?.audio_url) {
      notify('该卡片没有可用的原始录音', 'warning');
      return;
    }
    setDownloading(true);
    try {
      await downloadAudio(card.audio_url, `${card.title || 'recording'}.wav`);
      notify('已开始下载原始录音');
    } catch (error) {
      notify(error instanceof Error ? error.message : '下载失败', 'error');
    } finally {
      setDownloading(false);
    }
  };

  const handleExportTranscript = async () => {
    if (!id) return;
    setExporting(true);
    try {
      const text = await api.getTranscriptText(id);
      downloadText(text, `${card?.title || 'transcript'}.txt`);
      notify('已导出 ASR 原文');
    } catch (error) {
      notify(error instanceof Error ? error.message : '导出失败', 'error');
    } finally {
      setExporting(false);
    }
  };

  const submitCorrection = () => {
    if (!id || !patch) return;
    const changed = Object.keys(patch).filter((key) => key !== 'note');
    if (changed.length === 0) {
      notify('内容没有变化', 'info');
      return;
    }
    updateCard.mutate(
      { id, patch },
      {
        onSuccess: () => {
          notify('勘误已保存，相关日程与画像将同步更新');
          setEditOpen(false);
        },
        onError: (error) => notify(error.message || '保存失败', 'error'),
      },
    );
  };

  const performDelete = () => {
    if (!id) return;
    deleteCard.mutate(id, {
      onSuccess: (result) => {
        notify(
          `卡片已删除，级联清理日程 ${result.cascaded.schedule} 项、待办 ${result.cascaded.todos} 项、会议 ${result.cascaded.meetings} 项`,
          'success',
        );
        setDeleteOpen(false);
        navigate('/history');
      },
      onError: (error) => notify(error.message || '删除失败', 'error'),
    });
  };

  if (cardQuery.isLoading) {
    return (
      <Stack spacing={2}>
        <Skeleton variant="rounded" height={150} />
        <Skeleton variant="rounded" height={200} />
        <Skeleton variant="rounded" height={280} />
      </Stack>
    );
  }

  if (cardQuery.isError || !card) {
    return (
      <Stack spacing={2}>
        <ErrorAlert
          error={cardQuery.error ?? new Error('card_not_found')}
          onRetry={() => void cardQuery.refetch()}
          title="无法加载卡片"
        />
        <Button onClick={() => navigate('/history')} variant="outlined">
          返回历史记录
        </Button>
      </Stack>
    );
  }

  const totalTurns = card.labels.user_turns + card.labels.other_turns;

  return (
    <Box>
      <Stack spacing={2}>
        {/* 头部 */}
        <Card>
          <CardContent>
            <Stack
              direction="row"
              spacing={0.75}
              sx={{ mb: 1, flexWrap: 'wrap', rowGap: 0.75 }}
            >
              <TypeBadge type={card.type} />
              {card.meeting_id ? (
                <Chip
                  size="small"
                  icon={<GroupsOutlinedIcon />}
                  label="关联会议纪要"
                  color="secondary"
                  variant="outlined"
                  onClick={() => navigate(`/meetings/${card.meeting_id}`)}
                />
              ) : null}
              {card.has_correction ? (
                <Chip size="small" label="已勘误" color="warning" variant="outlined" />
              ) : null}
            </Stack>
            <Typography variant="h5" component="h2" sx={{ mb: 1 }}>
              {card.title}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {formatDateTime(card.occurred_at)} · 时长 {formatDuration(card.duration_s)}
            </Typography>

            <Stack direction="row" spacing={1} sx={{ mt: 2, flexWrap: 'wrap', rowGap: 1 }}>
              <Button
                variant="outlined"
                startIcon={<DownloadIcon />}
                onClick={handleDownloadAudio}
                disabled={downloading || !card.audio_available}
                aria-label="下载原始录音"
              >
                {downloading ? '下载中…' : '原始录音'}
              </Button>
              <Button
                variant="outlined"
                startIcon={<AudioFileIcon />}
                onClick={handleExportTranscript}
                disabled={exporting}
                aria-label="导出 ASR 原文"
              >
                导出原文
              </Button>
              <Button
                variant="outlined"
                startIcon={<EditNoteIcon />}
                onClick={() => setEditOpen(true)}
                aria-label="勘误"
              >
                勘误
              </Button>
              <Button
                variant="outlined"
                color="error"
                startIcon={<DeleteOutlineIcon />}
                onClick={() => setDeleteOpen(true)}
                aria-label="删除卡片"
              >
                删除
              </Button>
            </Stack>
          </CardContent>
        </Card>

        {/* 摘要 */}
        <Card>
          <CardContent>
            <Typography component="h3" variant="subtitle1" sx={{ mb: 1 }}>
              总结
            </Typography>
            <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
              {card.summary}
            </Typography>
            {card.key_points.length > 0 ? (
              <>
                <Divider sx={{ my: 1.75 }} />
                <Typography component="h3" variant="subtitle1" sx={{ mb: 1 }}>
                  关键信息
                </Typography>
                <List dense disablePadding>
                  {card.key_points.map((point, index) => (
                    <ListItem key={`${point}-${index}`} disableGutters sx={{ alignItems: 'flex-start' }}>
                      <ListItemIcon sx={{ minWidth: 30, mt: 0.25 }}>
                        <CheckCircleOutlineIcon fontSize="small" color="primary" />
                      </ListItemIcon>
                      <ListItemText
                        slotProps={{ primary: { variant: 'body2' } }}
                        primary={point}
                      />
                    </ListItem>
                  ))}
                </List>
              </>
            ) : null}
          </CardContent>
        </Card>

        {/* 人物与身份标签 */}
        <Card>
          <CardContent>
            <Stack direction="row" alignItems="center" sx={{ mb: 1.25 }}>
              <Typography component="h3" variant="subtitle1" sx={{ flex: 1 }}>
                涉及人物与身份
              </Typography>
              <Chip size="small" label={`共 ${totalTurns} 轮发言`} variant="outlined" />
            </Stack>
            {card.people.length > 0 ? (
              <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1, mb: 1.5 }}>
                {card.people.map((person) => (
                  <Chip key={person} label={person} variant="outlined" />
                ))}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                未识别到明确的人物名称。
              </Typography>
            )}
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
              <Chip
                label={`[使用者] ${card.labels.user_turns} 轮`}
                sx={{ bgcolor: 'primary.main', color: 'primary.contrastText', fontWeight: 600 }}
              />
              <Chip
                label={`[其他人] ${card.labels.other_turns} 轮`}
                sx={{ bgcolor: 'secondary.main', color: 'secondary.contrastText', fontWeight: 600 }}
              />
              <Chip
                label={card.audio_available ? '原始录音可用' : '原始录音不可用'}
                variant="outlined"
                color={card.audio_available ? 'success' : 'default'}
              />
            </Stack>
          </CardContent>
        </Card>

        {/* ASR 原文 */}
        <Card>
          <CardContent>
            <Stack direction="row" alignItems="center" sx={{ mb: 1.5 }}>
              <Typography component="h3" variant="subtitle1" sx={{ flex: 1 }}>
                ASR 原文（按轮次）
              </Typography>
              <Typography variant="caption" color="text.secondary">
                身份标签决定是否触发指令
              </Typography>
            </Stack>
            {card.transcript.length === 0 ? (
              <EmptyState title="没有转写文本" description="该片段可能尚未完成 ASR 处理。" />
            ) : (
              <Stack spacing={1.25}>
                {card.transcript.map((turn, index) => (
                  <TranscriptLine key={`${turn.start}-${index}`} turn={turn} />
                ))}
              </Stack>
            )}
          </CardContent>
        </Card>

        {/* 关联日程 / 待办 / 指令 */}
        {card.schedule_items.length > 0 ||
        card.todos.length > 0 ||
        card.commands.length > 0 ? (
          <Card>
            <CardContent>
              <Typography component="h3" variant="subtitle1" sx={{ mb: 1 }}>
                关联内容
              </Typography>
              <Stack spacing={0.5}>
                {card.schedule_items.map((item) => (
                  <Stack
                    key={item.id}
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    sx={{ py: 0.75 }}
                  >
                    <EventIcon fontSize="small" color="primary" />
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {item.title}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {formatShortDateTime(item.start_at)}
                        {item.location ? ` · ${item.location}` : ''}
                      </Typography>
                    </Box>
                    <SourceChip source={item.source} />
                  </Stack>
                ))}
                {card.todos.map((todo) => (
                  <Stack
                    key={todo.id}
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    sx={{ py: 0.75 }}
                  >
                    <TaskAltIcon fontSize="small" color="secondary" />
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {todo.title}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {todo.due_at ? `截止 ${formatShortDateTime(todo.due_at)}` : '无截止时间'}
                      </Typography>
                    </Box>
                    <Chip
                      size="small"
                      label={todo.status === 'done' ? '已完成' : '待办'}
                      variant="outlined"
                    />
                  </Stack>
                ))}
                {card.commands.map((command) => (
                  <Box key={command.id} sx={{ py: 0.75 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      指令：{command.text}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {command.result}
                    </Typography>
                  </Box>
                ))}
              </Stack>
            </CardContent>
          </Card>
        ) : null}

        {/* 勘误历史 */}
        <Card>
          <CardContent>
            <Typography component="h3" variant="subtitle1" sx={{ mb: 1 }}>
              勘误历史
            </Typography>
            {card.corrections.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                暂无勘误记录。
              </Typography>
            ) : (
              <Stack spacing={1.5} divider={<Divider flexItem />}>
                {card.corrections.map((correction) => (
                  <Box key={correction.id}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip
                        size="small"
                        icon={<HistoryEduIcon />}
                        label={FIELD_LABEL[correction.field] ?? correction.field}
                        variant="outlined"
                      />
                      <Typography variant="caption" color="text.secondary">
                        {formatDateTime(correction.at)}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary" sx={{ textDecoration: 'line-through' }}>
                      {correctionValue(correction.before)}
                    </Typography>
                    <Typography variant="body2">{correctionValue(correction.after)}</Typography>
                    {correction.note ? (
                      <Typography variant="caption" color="text.secondary">
                        说明：{correction.note}
                      </Typography>
                    ) : null}
                  </Box>
                ))}
              </Stack>
            )}
          </CardContent>
        </Card>
      </Stack>

      {/* 勘误对话框 */}
      <Dialog open={editOpen} onClose={() => setEditOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>勘误卡片</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              label="标题"
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              fullWidth
              required
            />
            <TextField
              label="摘要"
              value={form.summary}
              onChange={(event) => setForm({ ...form, summary: event.target.value })}
              fullWidth
              multiline
              minRows={3}
            />
            <TextField
              label="关键信息（每行一条）"
              value={form.keyPoints}
              onChange={(event) => setForm({ ...form, keyPoints: event.target.value })}
              fullWidth
              multiline
              minRows={3}
            />
            <TextField
              select
              label="类型"
              value={form.type}
              onChange={(event) => setForm({ ...form, type: event.target.value as CardType })}
              fullWidth
              helperText="如果是电视/视频声音，请改为「流媒体」以免影响日程与画像。"
            >
              {(Object.keys(CARD_TYPE_LABEL) as CardType[]).map((type) => (
                <MenuItem key={type} value={type}>
                  {CARD_TYPE_LABEL[type]}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="勘误说明"
              value={form.note}
              onChange={(event) => setForm({ ...form, note: event.target.value })}
              fullWidth
              placeholder="例如：其实是电视节目声音"
              helperText="说明会写入勘误历史，便于回溯。"
            />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setEditOpen(false)} color="inherit">
            取消
          </Button>
          <Button
            variant="contained"
            onClick={submitCorrection}
            disabled={updateCard.isPending}
            aria-label="保存勘误"
          >
            保存勘误
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={deleteOpen}
        title="删除这张卡片？"
        description={`删除后将级联清理由该卡片派生的日程、待办与会议纪要，并把用户画像标记为需要重新计算。该操作会写入审计记录，可在设置的「审计记录」中追溯。\n\n此操作不可撤销。`}
        confirmLabel="确认删除"
        destructive
        loading={deleteCard.isPending}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={performDelete}
      />
    </Box>
  );
}
