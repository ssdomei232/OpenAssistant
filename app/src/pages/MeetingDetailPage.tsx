import { useState } from 'react';
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
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import GavelIcon from '@mui/icons-material/Gavel';
import TopicOutlinedIcon from '@mui/icons-material/TopicOutlined';
import {
  useDeleteMeeting,
  useMeeting,
  useRegenerateMeeting,
  useUpdateMeeting,
} from '../api/queries';
import type { MeetingPatch } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { MarkdownView } from '../components/MarkdownView';
import { useFeedback } from '../components/FeedbackProvider';
import { formatDateTime, formatShortDateTime } from '../lib/format';
import { ParticipantChip } from './MeetingsPage';

export function MeetingDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { notify } = useFeedback();
  const meetingQuery = useMeeting(id);
  const regenerate = useRegenerateMeeting();
  const updateMeeting = useUpdateMeeting();
  const deleteMeeting = useDeleteMeeting();

  const meeting = meetingQuery.data;
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [form, setForm] = useState({ title: '', summary: '', minutes: '' });

  const openEdit = () => {
    if (!meeting) return;
    setForm({
      title: meeting.title,
      summary: meeting.summary,
      minutes: meeting.minutes ?? '',
    });
    setEditOpen(true);
  };

  const submitEdit = () => {
    if (!id || !meeting) return;
    const patch: MeetingPatch = {};
    if (form.title.trim() && form.title.trim() !== meeting.title) patch.title = form.title.trim();
    if (form.summary.trim() !== meeting.summary) patch.summary = form.summary.trim();
    if (form.minutes !== (meeting.minutes ?? '')) patch.minutes = form.minutes;
    if (Object.keys(patch).length === 0) {
      notify('内容没有变化', 'info');
      return;
    }
    updateMeeting.mutate(
      { id, patch },
      {
        onSuccess: () => {
          notify('纪要已保存');
          setEditOpen(false);
        },
        onError: (error) => notify(error.message || '保存失败', 'error'),
      },
    );
  };

  const handleRegenerate = () => {
    if (!id) return;
    regenerate.mutate(id, {
      onSuccess: () => notify('已重新生成会议纪要'),
      onError: (error) => notify(error.message || '重新生成失败', 'error'),
    });
  };

  const performDelete = () => {
    if (!id) return;
    deleteMeeting.mutate(id, {
      onSuccess: () => {
        notify('会议纪要已删除');
        navigate('/meetings');
      },
      onError: (error) => notify(error.message || '删除失败', 'error'),
    });
  };

  if (meetingQuery.isLoading) {
    return (
      <Stack spacing={2}>
        <Skeleton variant="rounded" height={160} />
        <Skeleton variant="rounded" height={320} />
      </Stack>
    );
  }

  if (meetingQuery.isError || !meeting) {
    return (
      <Stack spacing={2}>
        <ErrorAlert
          error={meetingQuery.error ?? new Error('meeting_not_found')}
          onRetry={() => void meetingQuery.refetch()}
          title="无法加载会议纪要"
        />
        <Button variant="outlined" onClick={() => navigate('/meetings')}>
          返回会议列表
        </Button>
      </Stack>
    );
  }

  return (
    <Box>
      <Stack spacing={2}>
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <Chip
                size="small"
                icon={<GroupsOutlinedIcon />}
                label="会议"
                color="secondary"
                variant="outlined"
              />
              <Chip
                size="small"
                label={meeting.status === 'ready' ? '纪要已生成' : '生成中'}
                color={meeting.status === 'ready' ? 'success' : 'default'}
                variant="outlined"
              />
            </Stack>
            <Typography variant="h5" component="h2" sx={{ mb: 1 }}>
              {meeting.title}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {formatDateTime(meeting.started_at)} – {formatShortDateTime(meeting.ended_at)}
            </Typography>

            <Stack direction="row" spacing={0.75} sx={{ mt: 1.5, flexWrap: 'wrap', rowGap: 0.75 }}>
              {meeting.participants.map((participant, index) => (
                <ParticipantChip
                  key={`${participant.label}-${participant.name}-${index}`}
                  participant={participant}
                />
              ))}
            </Stack>

            <Divider sx={{ my: 2 }} />

            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
              <Button
                variant="contained"
                startIcon={<AutoAwesomeIcon />}
                onClick={handleRegenerate}
                disabled={regenerate.isPending}
                aria-label="重新生成会议纪要"
              >
                {regenerate.isPending ? '生成中…' : '重新生成'}
              </Button>
              <Button
                variant="outlined"
                startIcon={<EditOutlinedIcon />}
                onClick={openEdit}
                aria-label="编辑会议纪要"
              >
                编辑
              </Button>
              <Button
                variant="outlined"
                color="error"
                startIcon={<DeleteOutlineIcon />}
                onClick={() => setDeleteOpen(true)}
                aria-label="删除会议纪要"
              >
                删除
              </Button>
            </Stack>
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Typography component="h3" variant="subtitle1" sx={{ mb: 1 }}>
              会议摘要
            </Typography>
            <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
              {meeting.summary}
            </Typography>
          </CardContent>
        </Card>

        {meeting.topics && meeting.topics.length > 0 ? (
          <Card>
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <TopicOutlinedIcon fontSize="small" color="primary" />
                <Typography component="h3" variant="subtitle1">
                  议题
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
                {meeting.topics.map((topic) => (
                  <Chip key={topic} label={topic} variant="outlined" />
                ))}
              </Stack>
            </CardContent>
          </Card>
        ) : null}

        {meeting.decisions && meeting.decisions.length > 0 ? (
          <Card>
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <GavelIcon fontSize="small" color="primary" />
                <Typography component="h3" variant="subtitle1">
                  决议
                </Typography>
              </Stack>
              <List dense disablePadding>
                {meeting.decisions.map((decision) => (
                  <ListItem key={decision} disableGutters sx={{ alignItems: 'flex-start' }}>
                    <ListItemIcon sx={{ minWidth: 28, mt: 0.25 }}>
                      <GavelIcon fontSize="small" color="secondary" />
                    </ListItemIcon>
                    <ListItemText slotProps={{ primary: { variant: 'body2' } }} primary={decision} />
                  </ListItem>
                ))}
              </List>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <TaskAltIcon fontSize="small" color="primary" />
              <Typography component="h3" variant="subtitle1">
                行动项（{meeting.action_items.length}）
              </Typography>
            </Stack>
            {meeting.action_items.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                没有识别到行动项。
              </Typography>
            ) : (
              <List dense disablePadding>
                {meeting.action_items.map((item, index) => (
                  <ListItem
                    key={`${item.text}-${index}`}
                    disableGutters
                    sx={{ alignItems: 'flex-start' }}
                  >
                    <ListItemIcon sx={{ minWidth: 30, mt: 0.25 }}>
                      <TaskAltIcon fontSize="small" color="success" />
                    </ListItemIcon>
                    <ListItemText
                      primary={item.text}
                      slotProps={{ primary: { variant: 'body2' } }}
                      secondary={[
                        item.owner ? `负责人：${item.owner}` : '负责人待定',
                        item.due_at ? `截止：${formatShortDateTime(item.due_at)}` : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Typography component="h3" variant="subtitle1" sx={{ mb: 1 }}>
              Markdown 纪要
            </Typography>
            {meeting.minutes ? (
              <MarkdownView markdown={meeting.minutes} />
            ) : (
              <EmptyState title="暂无纪要文本" description="可以点击「重新生成」让 AI 整理一份完整纪要。" />
            )}
          </CardContent>
        </Card>

        {meeting.card_ids.length > 0 ? (
          <Card>
            <CardContent>
              <Typography component="h3" variant="subtitle1" sx={{ mb: 1 }}>
                来源卡片
              </Typography>
              <Stack spacing={1}>
                {meeting.card_ids.map((cardId) => (
                  <Button
                    key={cardId}
                    variant="outlined"
                    onClick={() => navigate(`/history/${cardId}`)}
                    sx={{ justifyContent: 'flex-start' }}
                    aria-label={`查看来源卡片 ${cardId}`}
                  >
                    查看卡片 {cardId}
                  </Button>
                ))}
              </Stack>
            </CardContent>
          </Card>
        ) : null}
      </Stack>

      <Dialog open={editOpen} onClose={() => setEditOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>编辑会议纪要</DialogTitle>
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
              minRows={2}
            />
            <TextField
              label="Markdown 纪要"
              value={form.minutes}
              onChange={(event) => setForm({ ...form, minutes: event.target.value })}
              fullWidth
              multiline
              minRows={8}
              slotProps={{ htmlInput: { style: { fontFamily: 'monospace', fontSize: 13 } } }}
            />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setEditOpen(false)} color="inherit">
            取消
          </Button>
          <Button variant="contained" onClick={submitEdit} disabled={updateMeeting.isPending}>
            保存
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={deleteOpen}
        title="删除会议纪要？"
        description="删除后会议纪要会被软删除，但不会删除来源卡片。此操作会写入审计记录。"
        confirmLabel="删除纪要"
        destructive
        loading={deleteMeeting.isPending}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={performDelete}
      />
    </Box>
  );
}
