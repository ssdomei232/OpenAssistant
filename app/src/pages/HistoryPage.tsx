import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardActionArea from '@mui/material/CardActionArea';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import ClearIcon from '@mui/icons-material/Clear';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import SearchIcon from '@mui/icons-material/Search';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import HistoryIcon from '@mui/icons-material/History';
import { useCards } from '../api/queries';
import type { CardSummary, CardType } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
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
          <HistoryCard key={card.id} card={card} onOpen={() => navigate(`/history/${card.id}`)} />
        ))}
      </Stack>
    </Box>
  );
}

function HistoryCard({ card, onOpen }: { card: CardSummary; onOpen: () => void }) {
  return (
    <Card>
      <CardActionArea onClick={onOpen} aria-label={`查看卡片详情：${card.title}`}>
        <CardContent>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
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
