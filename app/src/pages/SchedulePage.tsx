import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import EventBusyOutlinedIcon from '@mui/icons-material/EventBusyOutlined';
import PlaceOutlinedIcon from '@mui/icons-material/PlaceOutlined';
import {
  useCreateSchedule,
  useDeleteSchedule,
  useSchedule,
  useUpdateSchedule,
} from '../api/queries';
import type { ScheduleItem } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { LoadingList } from '../components/LoadingList';
import { PageFab } from '../components/PageFab';
import { SourceChip } from '../components/Badges';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useFeedback } from '../components/FeedbackProvider';
import {
  dayLabel,
  daysFromNowIso,
  formatTime,
  fromDateTimeLocal,
  groupByDay,
  toDateTimeLocal,
} from '../lib/format';

interface FormState {
  title: string;
  start: string;
  end: string;
  location: string;
  description: string;
  remind: string;
}

function defaultForm(): FormState {
  const start = new Date();
  start.setMinutes(start.getMinutes() < 30 ? 30 : 60, 0, 0);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  return {
    title: '',
    start: toDateTimeLocal(start.toISOString()),
    end: toDateTimeLocal(end.toISOString()),
    location: '',
    description: '',
    remind: '30',
  };
}

function itemToForm(item: ScheduleItem): FormState {
  const diffMinutes = item.remind_at
    ? Math.round((new Date(item.start_at).getTime() - new Date(item.remind_at).getTime()) / 60_000)
    : 0;
  const remindOptions = [0, 10, 30, 60];
  return {
    title: item.title,
    start: toDateTimeLocal(item.start_at),
    end: toDateTimeLocal(item.end_at),
    location: item.location ?? '',
    description: item.description ?? '',
    remind: String(remindOptions.includes(diffMinutes) ? diffMinutes : 0),
  };
}

export function SchedulePage() {
  const navigate = useNavigate();
  const { notify } = useFeedback();
  const rangeFrom = daysFromNowIso(-365);
  const rangeTo = daysFromNowIso(120, true);
  const scheduleQuery = useSchedule(rangeFrom, rangeTo);
  const createSchedule = useCreateSchedule();
  const updateSchedule = useUpdateSchedule();
  const deleteSchedule = useDeleteSchedule();

  const [tab, setTab] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduleItem | null>(null);
  const [form, setForm] = useState<FormState>(defaultForm);
  const [selected, setSelected] = useState<ScheduleItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduleItem | null>(null);

  const items = scheduleQuery.data?.items ?? [];
  const now = Date.now();
  const future = useMemo(
    () =>
      items
        .filter((item) => new Date(item.end_at).getTime() >= now)
        .sort((a, b) => a.start_at.localeCompare(b.start_at)),
    [items, now],
  );
  const past = useMemo(
    () =>
      items
        .filter((item) => new Date(item.end_at).getTime() < now)
        .sort((a, b) => b.start_at.localeCompare(a.start_at)),
    [items, now],
  );
  const visible = tab === 0 ? future : past;
  const groups = groupByDay(visible, (item) => item.start_at);

  const openCreate = () => {
    setEditing(null);
    setForm(defaultForm());
    setFormOpen(true);
  };

  const openEdit = (item: ScheduleItem) => {
    setSelected(null);
    setEditing(item);
    setForm(itemToForm(item));
    setFormOpen(true);
  };

  const submit = () => {
    if (!form.title.trim()) {
      notify('请填写日程标题', 'warning');
      return;
    }
    if (!form.start || !form.end) {
      notify('请填写开始与结束时间', 'warning');
      return;
    }
    const startIso = fromDateTimeLocal(form.start);
    const endIso = fromDateTimeLocal(form.end);
    if (new Date(endIso).getTime() <= new Date(startIso).getTime()) {
      notify('结束时间必须晚于开始时间', 'warning');
      return;
    }
    const payload = {
      title: form.title.trim(),
      start_at: startIso,
      end_at: endIso,
      location: form.location.trim() || undefined,
      description: form.description.trim() || undefined,
      remind_minutes_before: Number(form.remind) || 0,
    };
    if (editing) {
      updateSchedule.mutate(
        { id: editing.id, patch: payload },
        {
          onSuccess: () => {
            notify('日程已更新');
            setFormOpen(false);
          },
          onError: (error) => notify(error.message || '更新失败', 'error'),
        },
      );
    } else {
      createSchedule.mutate(payload, {
        onSuccess: () => {
          notify('日程已创建');
          setFormOpen(false);
        },
        onError: (error) => notify(error.message || '创建失败', 'error'),
      });
    }
  };

  const performDelete = () => {
    if (!deleteTarget) return;
    deleteSchedule.mutate(deleteTarget.id, {
      onSuccess: () => {
        notify('日程已删除');
        setDeleteTarget(null);
        setSelected(null);
      },
      onError: (error) => notify(error.message || '删除失败', 'error'),
    });
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="body2" color="text.secondary">
          来自卡片与指令的日程会自动同步到这里
        </Typography>
      </Stack>

      <Tabs
        value={tab}
        onChange={(_event, value: number) => setTab(value)}
        aria-label="未来与过去日程切换"
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
      >
        <Tab label={`未来（${future.length}）`} />
        <Tab label={`过去（${past.length}）`} />
      </Tabs>

      {scheduleQuery.isLoading ? <LoadingList count={3} /> : null}
      {scheduleQuery.isError ? (
        <ErrorAlert error={scheduleQuery.error} onRetry={() => void scheduleQuery.refetch()} />
      ) : null}

      {!scheduleQuery.isLoading && !scheduleQuery.isError && visible.length === 0 ? (
        <EmptyState
          title={tab === 0 ? '暂无未来日程' : '暂无历史日程'}
          description={
            tab === 0
              ? '说一句「帮我记一下明天下午三点开会」，日程会自动出现在这里，也可以手动新建。'
              : '过去的日程会按时间倒序展示。'
          }
          icon={<EventBusyOutlinedIcon />}
          actionLabel={tab === 0 ? '新建日程' : undefined}
          onAction={openCreate}
        />
      ) : null}

      {groups.map((group) => (
        <Box key={group.day} sx={{ mb: 2.5 }}>
          <Typography
            component="h2"
            variant="subtitle2"
            color="text.secondary"
            sx={{ mb: 1, px: 0.5 }}
          >
            {dayLabel(group.day)}
          </Typography>
          <Card>
            <Stack divider={<Divider flexItem />}>
              {group.items.map((item) => (
                <Box
                  key={item.id}
                  component="button"
                  type="button"
                  onClick={() => setSelected(item)}
                  aria-label={`查看日程：${item.title}，${dayLabel(item.start_at)} ${formatTime(
                    item.start_at,
                  )}`}
                  sx={{
                    display: 'flex',
                    width: '100%',
                    textAlign: 'left',
                    gap: 1.5,
                    p: 1.75,
                    bgcolor: 'transparent',
                    border: 'none',
                    color: 'inherit',
                    font: 'inherit',
                    cursor: 'pointer',
                    '&:hover': { bgcolor: 'action.hover' },
                  }}
                >
                  <Box sx={{ minWidth: 52, pt: 0.25 }}>
                    <Typography variant="body2" color="primary" sx={{ fontWeight: 700 }}>
                      {formatTime(item.start_at)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {formatTime(item.end_at)}
                    </Typography>
                  </Box>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body1" sx={{ fontWeight: 600 }}>
                      {item.title}
                    </Typography>
                    <Stack
                      direction="row"
                      spacing={0.75}
                      alignItems="center"
                      sx={{ mt: 0.5, flexWrap: 'wrap', rowGap: 0.5 }}
                    >
                      {item.location ? (
                        <Stack direction="row" spacing={0.25} alignItems="center">
                          <PlaceOutlinedIcon sx={{ fontSize: 15, color: 'text.secondary' }} />
                          <Typography variant="caption" color="text.secondary">
                            {item.location}
                          </Typography>
                        </Stack>
                      ) : null}
                      <SourceChip source={item.source} />
                      {item.status === 'cancelled' ? (
                        <Chip size="small" label="已取消" color="default" />
                      ) : null}
                    </Stack>
                  </Box>
                </Box>
              ))}
            </Stack>
          </Card>
        </Box>
      ))}

      <PageFab label="新建日程" onClick={openCreate} />

      {/* 日程详情 */}
      <Dialog open={Boolean(selected)} onClose={() => setSelected(null)} fullWidth maxWidth="xs">
        <DialogTitle>{selected?.title}</DialogTitle>
        <DialogContent>
          {selected ? (
            <Stack spacing={1.5} sx={{ pt: 0.5 }}>
              <Typography variant="body2" color="text.secondary">
                {dayLabel(selected.start_at)} {formatTime(selected.start_at)} –{' '}
                {formatTime(selected.end_at)}
              </Typography>
              {selected.location ? (
                <Stack direction="row" spacing={0.5} alignItems="center">
                  <PlaceOutlinedIcon fontSize="small" color="disabled" />
                  <Typography variant="body2">{selected.location}</Typography>
                </Stack>
              ) : null}
              {selected.description ? (
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                  {selected.description}
                </Typography>
              ) : null}
              <Stack direction="row" spacing={1} sx={{ pt: 0.5 }}>
                <SourceChip source={selected.source} />
                {selected.card_id ? (
                  <Button
                    size="small"
                    onClick={() => navigate(`/history/${selected.card_id}`)}
                    aria-label="查看来源卡片"
                  >
                    查看来源卡片
                  </Button>
                ) : null}
              </Stack>
            </Stack>
          ) : null}
        </DialogContent>
        <DialogActions sx={{ px: 2, pb: 2 }}>
          <IconButton
            onClick={() => selected && setDeleteTarget(selected)}
            aria-label="删除日程"
            color="error"
          >
            <DeleteOutlineIcon />
          </IconButton>
          <Box sx={{ flex: 1 }} />
          <Button onClick={() => setSelected(null)} color="inherit">
            关闭
          </Button>
          <Button
            variant="contained"
            startIcon={<EditOutlinedIcon />}
            onClick={() => selected && openEdit(selected)}
          >
            编辑
          </Button>
        </DialogActions>
      </Dialog>

      {/* 新建 / 编辑 */}
      <Dialog open={formOpen} onClose={() => setFormOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>{editing ? '编辑日程' : '新建日程'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              label="标题"
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              fullWidth
              required
              autoFocus
              slotProps={{ htmlInput: { maxLength: 120 } }}
            />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="开始时间"
                type="datetime-local"
                value={form.start}
                onChange={(event) => setForm({ ...form, start: event.target.value })}
                fullWidth
                required
                slotProps={{ inputLabel: { shrink: true } }}
              />
              <TextField
                label="结束时间"
                type="datetime-local"
                value={form.end}
                onChange={(event) => setForm({ ...form, end: event.target.value })}
                fullWidth
                required
                slotProps={{ inputLabel: { shrink: true } }}
              />
            </Stack>
            <TextField
              label="地点"
              value={form.location}
              onChange={(event) => setForm({ ...form, location: event.target.value })}
              fullWidth
              placeholder="例如：3 号会议室"
            />
            <TextField
              label="备注"
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              fullWidth
              multiline
              minRows={2}
            />
            <TextField
              select
              label="提醒"
              value={form.remind}
              onChange={(event) => setForm({ ...form, remind: event.target.value })}
              fullWidth
            >
              <MenuItem value="0">不提醒</MenuItem>
              <MenuItem value="10">提前 10 分钟</MenuItem>
              <MenuItem value="30">提前 30 分钟</MenuItem>
              <MenuItem value="60">提前 1 小时</MenuItem>
            </TextField>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setFormOpen(false)} color="inherit">
            取消
          </Button>
          <Button
            variant="contained"
            onClick={submit}
            disabled={createSchedule.isPending || updateSchedule.isPending}
          >
            保存
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除日程"
        description={`确定删除「${deleteTarget?.title ?? ''}」吗？来自卡片的日程删除后不会影响卡片本身。`}
        confirmLabel="删除"
        destructive
        loading={deleteSchedule.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={performDelete}
      />
    </Box>
  );
}
