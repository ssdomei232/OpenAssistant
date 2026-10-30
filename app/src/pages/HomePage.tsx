import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import AccessTimeIcon from '@mui/icons-material/AccessTime';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EventIcon from '@mui/icons-material/Event';
import NotificationsActiveOutlinedIcon from '@mui/icons-material/NotificationsActiveOutlined';
import PlaylistAddCheckIcon from '@mui/icons-material/PlaylistAddCheck';
import ReplayIcon from '@mui/icons-material/Replay';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import {
  useCommands,
  useCreateTodo,
  useDeleteTodo,
  useMe,
  useRetryCommand,
  useTodos,
  useUpdateTodo,
  useSchedule,
} from '../api/queries';
import type { Command, Todo } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { LoadingList } from '../components/LoadingList';
import { PageFab } from '../components/PageFab';
import { Section } from '../components/Section';
import { SourceChip } from '../components/Badges';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useFeedback } from '../components/FeedbackProvider';
import {
  endOfTodayIso,
  formatShortDateTime,
  formatTime,
  fromDateTimeLocal,
  isPast,
  relativeLabel,
  startOfTodayIso,
} from '../lib/format';
import { isToday } from 'date-fns';

function dueLabel(todo: Todo): string | null {
  if (!todo.due_at) return null;
  const date = new Date(todo.due_at);
  return isToday(date) ? `今天 ${formatTime(date)}` : formatShortDateTime(date);
}

const COMMAND_STATUS: Record<string, { label: string; color: 'success' | 'error' | 'default' }> = {
  done: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  ignored: { label: '已忽略', color: 'default' },
};

function CommandNotification({
  command,
  onRetry,
  retrying,
}: {
  command: Command;
  onRetry: () => void;
  retrying: boolean;
}) {
  const status = COMMAND_STATUS[command.status] ?? COMMAND_STATUS.ignored;
  return (
    <Card
      sx={{
        mb: 1.25,
        borderLeft: 4,
        borderLeftColor: command.status === 'failed' ? 'error.main' : 'primary.main',
      }}
    >
      <CardContent sx={{ py: 1.5 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
          <NotificationsActiveOutlinedIcon fontSize="small" color="primary" />
          <Typography variant="body2" sx={{ fontWeight: 600, flex: 1 }}>
            {command.text}
          </Typography>
          <Chip size="small" label={status.label} color={status.color} variant="outlined" />
        </Stack>
        {command.result ? (
          <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap' }}>
            {command.result}
          </Typography>
        ) : null}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
          <Typography variant="caption" color="text.secondary">
            {relativeLabel(command.created_at)}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            · {command.pushed ? '已推送到钉钉' : '未推送'}
          </Typography>
          <Box sx={{ flex: 1 }} />
          {command.status === 'failed' ? (
            <Button
              size="small"
              startIcon={<ReplayIcon />}
              onClick={onRetry}
              disabled={retrying}
              aria-label={`重试指令：${command.text}`}
            >
              重试
            </Button>
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

export function HomePage() {
  const navigate = useNavigate();
  const { notify } = useFeedback();
  const from = startOfTodayIso();
  const to = endOfTodayIso();

  const meQuery = useMe();
  const todosQuery = useTodos({ status: 'all' });
  const scheduleQuery = useSchedule(from, to);
  const commandsQuery = useCommands({ limit: 5 });
  const updateTodo = useUpdateTodo();
  const deleteTodo = useDeleteTodo();
  const createTodo = useCreateTodo();
  const retryCommand = useRetryCommand();

  const [createOpen, setCreateOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [due, setDue] = useState('');
  const [showDone, setShowDone] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Todo | null>(null);

  const openCreateTodo = () => {
    setTitle('');
    setDue('');
    setCreateOpen(true);
  };

  const todos = todosQuery.data?.items ?? [];
  const pending = useMemo(() => todos.filter((todo) => todo.status === 'pending'), [todos]);
  const done = useMemo(() => todos.filter((todo) => todo.status === 'done'), [todos]);
  const scheduleItems = scheduleQuery.data?.items ?? [];
  const commands = commandsQuery.data?.items ?? [];
  const loading = todosQuery.isLoading || scheduleQuery.isLoading || commandsQuery.isLoading;

  const toggleTodo = (todo: Todo) => {
    const nextStatus = todo.status === 'pending' ? 'done' : 'pending';
    updateTodo.mutate(
      { id: todo.id, patch: { status: nextStatus } },
      {
        onSuccess: () =>
          notify(nextStatus === 'done' ? `已完成：${todo.title}` : `已恢复：${todo.title}`),
        onError: (error) => notify(error.message || '更新待办失败', 'error'),
      },
    );
  };

  const submitTodo = () => {
    const trimmed = title.trim();
    if (!trimmed) {
      notify('请输入待办内容', 'warning');
      return;
    }
    createTodo.mutate(
      { title: trimmed, due_at: due ? fromDateTimeLocal(due) : undefined },
      {
        onSuccess: () => {
          notify('已添加待办');
          setCreateOpen(false);
          setTitle('');
          setDue('');
        },
        onError: (error) => notify(error.message || '添加失败', 'error'),
      },
    );
  };

  const performDelete = () => {
    if (!deleteTarget) return;
    deleteTodo.mutate(deleteTarget.id, {
      onSuccess: () => {
        notify('已删除待办');
        setDeleteTarget(null);
      },
      onError: (error) => notify(error.message || '删除失败', 'error'),
    });
  };

  const firstName = meQuery.data?.display_name ?? '你';
  const todayText = new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date());

  return (
    <Box>
      <Box sx={{ px: 0.5, mb: 2 }}>
        <Typography variant="h4" component="p" sx={{ mb: 0.25 }}>
          你好，{firstName}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {todayText} · 今天有 {pending.length + scheduleItems.length} 项需要关注
        </Typography>
      </Box>

      {loading ? (
        <LoadingList count={4} />
      ) : (
        <>
          {scheduleQuery.isError ? (
            <Box sx={{ mb: 2 }}>
              <ErrorAlert error={scheduleQuery.error} onRetry={() => void scheduleQuery.refetch()} />
            </Box>
          ) : null}

          <Section
            title="今日日程提醒"
            icon={<EventIcon fontSize="small" />}
            count={scheduleItems.length}
            action={
              <Button size="small" onClick={() => navigate('/schedule')} aria-label="查看全部日程">
                全部日程
              </Button>
            }
          >
            {scheduleItems.length === 0 ? (
              <EmptyState
                title="今天没有日程"
                description="说完「帮我记一下…」后，自动提取的日程会出现在这里。"
              />
            ) : (
              <Card>
                <Stack divider={<Divider flexItem />}>
                  {scheduleItems.slice(0, 4).map((item) => (
                    <Stack
                      key={item.id}
                      direction="row"
                      spacing={1.5}
                      alignItems="flex-start"
                      sx={{ p: 1.75 }}
                    >
                      <Box
                        sx={{
                          minWidth: 56,
                          textAlign: 'center',
                          fontWeight: 700,
                          color: 'primary.main',
                          fontSize: '0.95rem',
                          pt: 0.25,
                        }}
                      >
                        {formatTime(item.start_at)}
                      </Box>
                      <Box sx={{ minWidth: 0 }}>
                        <Typography variant="body1" sx={{ fontWeight: 600 }}>
                          {item.title}
                        </Typography>
                        <Stack direction="row" spacing={0.75} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
                          {item.location ? (
                            <Typography variant="caption" color="text.secondary">
                              {item.location}
                            </Typography>
                          ) : null}
                          <SourceChip source={item.source} />
                          {isPast(item.end_at) ? (
                            <Chip size="small" label="已结束" variant="outlined" />
                          ) : null}
                        </Stack>
                      </Box>
                    </Stack>
                  ))}
                </Stack>
              </Card>
            )}
          </Section>

          <Section
            title="待办事项"
            icon={<PlaylistAddCheckIcon fontSize="small" />}
            count={pending.length}
            action={
              <Button
                size="small"
                startIcon={<TaskAltIcon />}
                onClick={openCreateTodo}
                aria-label="新建待办"
              >
                新建
              </Button>
            }
          >
            {todosQuery.isError ? (
              <ErrorAlert error={todosQuery.error} onRetry={() => void todosQuery.refetch()} />
            ) : pending.length === 0 ? (
              <EmptyState
                title="没有待办事项"
                description="可以从卡片生成，也可以点「新建待办」手动添加。"
                actionLabel="新建待办"
                onAction={openCreateTodo}
              />
            ) : (
              <Stack spacing={1}>
                {pending.map((todo) => {
                  const overdue = todo.due_at ? isPast(todo.due_at) : false;
                  return (
                    <Card key={todo.id}>
                      <Stack direction="row" alignItems="flex-start" sx={{ p: 0.75, pr: 0.5 }}>
                        <Checkbox
                          checked={false}
                          onChange={() => toggleTodo(todo)}
                          slotProps={{
                            input: { 'aria-label': `完成待办：${todo.title}` },
                          }}
                          color="primary"
                          sx={{ mt: -0.25 }}
                        />
                        <Box sx={{ flex: 1, minWidth: 0, py: 1 }}>
                          <Typography variant="body1" sx={{ fontWeight: 500 }}>
                            {todo.title}
                          </Typography>
                          <Stack
                            direction="row"
                            spacing={0.75}
                            alignItems="center"
                            sx={{ mt: 0.5, flexWrap: 'wrap', rowGap: 0.5 }}
                          >
                            {todo.due_at ? (
                              <Chip
                                size="small"
                                icon={<AccessTimeIcon />}
                                label={dueLabel(todo)}
                                color={overdue ? 'error' : 'default'}
                                variant={overdue ? 'filled' : 'outlined'}
                              />
                            ) : null}
                            {todo.priority === 'high' ? (
                              <Chip size="small" label="高优先级" color="error" variant="outlined" />
                            ) : null}
                            <SourceChip source={todo.source} />
                          </Stack>
                        </Box>
                        <IconButton
                          onClick={() => setDeleteTarget(todo)}
                          aria-label={`删除待办：${todo.title}`}
                          size="small"
                          sx={{ mt: 0.75 }}
                        >
                          <DeleteOutlineIcon fontSize="small" />
                        </IconButton>
                      </Stack>
                    </Card>
                  );
                })}
              </Stack>
            )}

            {done.length > 0 ? (
              <Box sx={{ mt: 1 }}>
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => setShowDone((value) => !value)}
                  aria-expanded={showDone}
                  aria-label={showDone ? '收起已完成待办' : '展开已完成待办'}
                >
                  {showDone ? '收起' : `查看已完成（${done.length}）`}
                </Button>
                {showDone ? (
                  <Stack spacing={0.5} sx={{ mt: 1 }}>
                    {done.map((todo) => (
                      <Stack
                        key={todo.id}
                        direction="row"
                        alignItems="center"
                        spacing={0.5}
                        sx={{ px: 1 }}
                      >
                        <Checkbox
                          checked
                          onChange={() => toggleTodo(todo)}
                          slotProps={{ input: { 'aria-label': `恢复待办：${todo.title}` } }}
                          size="small"
                        />
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ textDecoration: 'line-through', flex: 1 }}
                        >
                          {todo.title}
                        </Typography>
                        <IconButton
                          size="small"
                          onClick={() => setDeleteTarget(todo)}
                          aria-label={`删除待办：${todo.title}`}
                        >
                          <DeleteOutlineIcon fontSize="small" />
                        </IconButton>
                      </Stack>
                    ))}
                  </Stack>
                ) : null}
              </Box>
            ) : null}
          </Section>

          <Section
            title="指令结果通知"
            icon={<NotificationsActiveOutlinedIcon fontSize="small" />}
            count={commands.filter((command) => command.status !== 'ignored').length}
          >
            {commandsQuery.isError ? (
              <ErrorAlert error={commandsQuery.error} onRetry={() => void commandsQuery.refetch()} />
            ) : commands.length === 0 ? (
              <EmptyState
                title="暂无指令结果"
                description="对设备说出「帮我…」类指令后，结果会通过钉钉推送并显示在这里。"
              />
            ) : (
              commands.map((command) => (
                <CommandNotification
                  key={command.id}
                  command={command}
                  retrying={retryCommand.isPending}
                  onRetry={() =>
                    retryCommand.mutate(command.id, {
                      onSuccess: () => notify('已重新执行指令'),
                      onError: (error) => notify(error.message || '重试失败', 'error'),
                    })
                  }
                />
              ))
            )}
          </Section>
        </>
      )}

      <PageFab label="快速记录" onClick={openCreateTodo} icon={<CheckCircleOutlineIcon sx={{ mr: 1 }} />} />

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>新建待办</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              autoFocus
              label="待办内容"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              fullWidth
              required
              slotProps={{ htmlInput: { maxLength: 200 } }}
              placeholder="例如：给张伟发 Q3 排期表"
            />
            <TextField
              label="截止时间（可选）"
              type="datetime-local"
              value={due}
              onChange={(event) => setDue(event.target.value)}
              fullWidth
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setCreateOpen(false)} color="inherit">
            取消
          </Button>
          <Button variant="contained" onClick={submitTodo} disabled={createTodo.isPending}>
            保存
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除待办"
        description={`确定删除「${deleteTarget?.title ?? ''}」吗？此操作不可撤销。`}
        confirmLabel="删除"
        destructive
        loading={deleteTodo.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={performDelete}
      />
    </Box>
  );
}
