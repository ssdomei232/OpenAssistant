import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardActionArea from '@mui/material/CardActionArea';
import CardContent from '@mui/material/CardContent';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ClearIcon from '@mui/icons-material/Clear';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import MergeTypeIcon from '@mui/icons-material/MergeType';
import SearchIcon from '@mui/icons-material/Search';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import HistoryIcon from '@mui/icons-material/History';
import { useCards, useMergeCards } from '../api/queries';
import type { CardSummary, CardType } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { useFeedback } from '../components/FeedbackProvider';
import { LoadingList } from '../components/LoadingList';
import { TypeBadge } from '../components/Badges';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { daysFromNowIso, formatDateTime, formatDuration } from '../lib/format';

const TYPE_FILTERS: { label: string; value: CardType | 'all' }[] = [
  { label: '全部', value: 'all' },
  { label: '对话', value: 'conversation' },
  { label: '会议', value: 'meeting' },
  { label: '流媒体', value: 'media' },
  { label: '其他', value: 'other' },
];

const RANGE_FILTERS: { label: string; days?: number }[] = [
  { label: '全部时间' },
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
];

export function HistoryPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [type, setType] = useState<CardType | 'all'>('all');
  const [rangeDays, setRangeDays] = useState<number | undefined>(undefined);
  const debouncedKeyword = useDebouncedValue(keyword, 300);

  const query = useMemo(
    () => ({
      q: debouncedKeyword.trim() || undefined,
      type: type === 'all' ? undefined : type,
      from: rangeDays ? daysFromNowIso(-rangeDays) : undefined,
      limit: 50,
    }),
    [debouncedKeyword, type, rangeDays],
  );

  const cardsQuery = useCards(query);
  const cards = cardsQuery.data?.items ?? [];
  const { notify } = useFeedback();
  const mergeCards = useMergeCards();
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const isSelectable = (card: CardSummary) => !card.meeting_id && card.type !== 'meeting';
  const selectableCards = cards.filter(isSelectable);

  const startSelecting = () => {
    setSelectedIds([]);
    setSelecting(true);
  };

  const cancelSelecting = () => {
    setSelecting(false);
    setSelectedIds([]);
    setConfirmOpen(false);
  };

  const toggleSelected = (cardId: string) => {
    setSelectedIds((current) =>
      current.includes(cardId) ? current.filter((id) => id !== cardId) : [...current, cardId],
    );
  };

  const mergeErrorMessage = (error: unknown): string => {
    const message = error instanceof Error ? error.message : '';
    if (message.includes('cannot_merge_meeting_cards')) return '会议卡片由会议纪要管理，不能参与合并。';
    if (message.includes('card_deleted')) return '选中的卡片中有已删除的，请刷新后重试。';
    if (message.includes('card_not_found')) return '卡片不存在或已被删除，请刷新后重试。';
    if (message.includes('merge_rejected')) return '这些内容没有可合并的有效信息。';
    if (message.includes('too_few_cards')) return '至少选择两张对话卡片。';
    return message || '合并失败，请稍后重试。';
  };

  const confirmMerge = () => {
    mergeCards.mutate(
      { cardIds: selectedIds },
      {
        onSuccess: (data) => {
          setConfirmOpen(false);
          cancelSelecting();
          notify(`已合并 ${data.merged_card_ids.length + 1} 个对话并重新总结`);
          navigate(`/history/${data.id}`);
        },
        onError: (error) => {
          setConfirmOpen(false);
          notify(mergeErrorMessage(error), 'error');
        },
      },
    );
  };

  return (
    <Box>
      <TextField
        value={keyword}
        onChange={(event) => setKeyword(event.target.value)}
        placeholder="搜索标题、摘要、人物…"
        fullWidth
        aria-label="搜索历史记录"
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
            endAdornment: keyword ? (
              <InputAdornment position="end">
                <IconButton
                  size="small"
                  onClick={() => setKeyword('')}
                  aria-label="清空搜索关键词"
                  edge="end"
                >
                  <ClearIcon fontSize="small" />
                </IconButton>
              </InputAdornment>
            ) : undefined,
          },
        }}
        sx={{ mb: 1.5 }}
      />

      <Stack
        direction="row"
        spacing={1}
        sx={{ mb: 1, overflowX: 'auto', pb: 0.5, scrollbarWidth: 'none' }}
        role="group"
        aria-label="按类型筛选"
      >
        {TYPE_FILTERS.map((filter) => (
          <Chip
            key={filter.value}
            label={filter.label}
            clickable
            color={type === filter.value ? 'primary' : 'default'}
            variant={type === filter.value ? 'filled' : 'outlined'}
            onClick={() => setType(filter.value)}
            aria-pressed={type === filter.value}
          />
        ))}
      </Stack>

      <Stack
        direction="row"
        spacing={1}
        sx={{ mb: 2, overflowX: 'auto', pb: 0.5, scrollbarWidth: 'none' }}
        role="group"
        aria-label="按时间筛选"
      >
        {RANGE_FILTERS.map((filter) => (
          <Chip
            key={filter.label}
            label={filter.label}
            size="small"
            clickable
            color={rangeDays === filter.days ? 'secondary' : 'default'}
            variant={rangeDays === filter.days ? 'filled' : 'outlined'}
            onClick={() => setRangeDays(filter.days)}
            aria-pressed={rangeDays === filter.days}
          />
        ))}
      </Stack>

      <Box sx={{ mb: 2 }}>
        {selecting ? (
          <Typography variant="body2" color="text.secondary">
            勾选至少两张对话卡片，合并后会统一重新总结；会议卡片不参与合并。
          </Typography>
        ) : selectableCards.length >= 2 ? (
          <Button
            size="small"
            variant="outlined"
            startIcon={<MergeTypeIcon />}
            onClick={startSelecting}
          >
            合并多个对话
          </Button>
        ) : null}
      </Box>

      {!cardsQuery.isLoading && !cardsQuery.isError ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.25 }}>
          共 {cards.length} 条记录
          {debouncedKeyword ? ` · 关键词「${debouncedKeyword}」` : ''}
        </Typography>
      ) : null}

      {cardsQuery.isLoading ? <LoadingList count={4} /> : null}
      {cardsQuery.isError ? (
        <ErrorAlert error={cardsQuery.error} onRetry={() => void cardsQuery.refetch()} />
      ) : null}

      {!cardsQuery.isLoading && !cardsQuery.isError && cards.length === 0 ? (
        <EmptyState
          icon={<HistoryIcon />}
          title={debouncedKeyword ? '没有匹配的记录' : '还没有历史记录'}
          description={
            debouncedKeyword
              ? '试试更换关键词，或清除时间范围筛选。'
              : '设备上传的对话会在处理完成后生成总结卡片。'
          }
        />
      ) : null}

      <Stack spacing={1.5}>
        {cards.map((card) => (
          <HistoryCard
            key={card.id}
            card={card}
            onOpen={() => navigate(`/history/${card.id}`)}
            selectionMode={selecting}
            selected={selectedIds.includes(card.id)}
            selectable={isSelectable(card)}
            onToggle={() => toggleSelected(card.id)}
          />
        ))}
      </Stack>

      {selecting ? (
        <Paper
          elevation={8}
          sx={{
            position: 'fixed',
            zIndex: (theme) => theme.zIndex.speedDial,
            bottom: { xs: 'calc(84px + var(--safe-bottom))', md: 24 },
            left: '50%',
            transform: 'translateX(-50%)',
            width: { xs: 'calc(100% - 32px)', md: 560 },
            borderRadius: 3,
            px: 2,
            py: 1.25,
            display: 'flex',
            alignItems: 'center',
            gap: 1,
          }}
          role="region"
          aria-label="合并对话操作栏"
        >
          <Typography variant="body2" sx={{ flex: 1 }}>
            已选 {selectedIds.length} 个对话
          </Typography>
          <Button onClick={cancelSelecting} color="inherit" disabled={mergeCards.isPending}>
            取消
          </Button>
          <Button
            variant="contained"
            startIcon={<MergeTypeIcon />}
            disabled={selectedIds.length < 2 || mergeCards.isPending}
            onClick={() => setConfirmOpen(true)}
          >
            合并并重新总结
          </Button>
        </Paper>
      ) : null}

      <ConfirmDialog
        open={confirmOpen}
        title="合并对话并重新总结"
        description={`将把选中的 ${selectedIds.length} 个对话合并成一张卡片并重新总结，其余卡片会被归档（软删除）。已产生的日程/待办会保留并挂到合并后的卡片上。`}
        confirmLabel="合并并重新总结"
        loading={mergeCards.isPending}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={confirmMerge}
      />
    </Box>
  );
}

interface HistoryCardProps {
  card: CardSummary;
  onOpen: () => void;
  selectionMode?: boolean;
  selected?: boolean;
  selectable?: boolean;
  onToggle?: () => void;
}

function HistoryCard({
  card,
  onOpen,
  selectionMode = false,
  selected = false,
  selectable = true,
  onToggle,
}: HistoryCardProps) {
  const handleActivate = selectionMode ? (selectable ? onToggle : undefined) : onOpen;
  const ariaLabel = selectionMode
    ? selectable
      ? `选择合并：${card.title}`
      : `会议卡片不可合并：${card.title}`
    : `查看卡片详情：${card.title}`;

  return (
    <Card
      sx={
        selected
          ? { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: '-2px' }
          : undefined
      }
    >
      <CardActionArea
        onClick={handleActivate}
        disabled={selectionMode && !selectable}
        aria-label={ariaLabel}
      >
        <CardContent>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
            {selectionMode ? (
              <Tooltip title={selectable ? '' : '会议卡片由会议纪要管理，不能参与合并'}>
                <span>
                  <Checkbox
                    checked={selected}
                    disabled={!selectable}
                    onChange={onToggle}
                    onClick={(event) => event.stopPropagation()}
                    tabIndex={-1}
                    sx={{ p: 0, mr: 0.5 }}
                    slotProps={{ input: { 'aria-label': `选择合并：${card.title}` } }}
                  />
                </span>
              </Tooltip>
            ) : null}
            <TypeBadge type={card.type} />
            {card.meeting_id ? (
              <Chip
                size="small"
                icon={<GroupsOutlinedIcon />}
                label="会议纪要"
                color="secondary"
                variant="outlined"
              />
            ) : null}
            {card.has_correction ? (
              <Chip
                size="small"
                icon={<ErrorOutlineIcon />}
                label="已勘误"
                color="warning"
                variant="outlined"
              />
            ) : null}
            <Box sx={{ flex: 1 }} />
            <Typography variant="caption" color="text.secondary">
              {formatDateTime(card.occurred_at)}
            </Typography>
          </Stack>

          <Typography variant="h6" component="h3" sx={{ mb: 0.5 }}>
            {card.title}
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              mb: 1,
            }}
          >
            {card.summary}
          </Typography>

          <Stack direction="row" spacing={0.75} sx={{ flexWrap: 'wrap', rowGap: 0.75 }}>
            {card.people.slice(0, 3).map((person) => (
              <Chip key={person} size="small" variant="outlined" label={person} />
            ))}
            {card.people.length > 3 ? (
              <Chip size="small" variant="outlined" label={`+${card.people.length - 3}`} />
            ) : null}
            <Chip
              size="small"
              label={`[使用者] ${card.labels.user_turns}`}
              sx={{ bgcolor: 'primary.main', color: 'primary.contrastText', fontWeight: 600 }}
            />
            <Chip
              size="small"
              label={`[其他人] ${card.labels.other_turns}`}
              sx={{ bgcolor: 'secondary.main', color: 'secondary.contrastText', fontWeight: 600 }}
            />
            <Chip size="small" variant="outlined" label={formatDuration(card.duration_s)} />
          </Stack>
        </CardContent>
      </CardActionArea>
    </Card>
  );
}
