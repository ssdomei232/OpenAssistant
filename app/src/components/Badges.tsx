import Chip from '@mui/material/Chip';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import TvOutlinedIcon from '@mui/icons-material/TvOutlined';
import LabelOutlinedIcon from '@mui/icons-material/LabelOutlined';
import type { CardType } from '../api/types';

export const CARD_TYPE_LABEL: Record<CardType, string> = {
  conversation: '对话',
  meeting: '会议',
  media: '流媒体',
  other: '其他',
};

const ICONS: Record<CardType, typeof ChatBubbleOutlineIcon> = {
  conversation: ChatBubbleOutlineIcon,
  meeting: GroupsOutlinedIcon,
  media: TvOutlinedIcon,
  other: LabelOutlinedIcon,
};

const COLORS: Record<CardType, 'primary' | 'secondary' | 'warning' | 'default'> = {
  conversation: 'primary',
  meeting: 'secondary',
  media: 'warning',
  other: 'default',
};

export function TypeBadge({ type, size = 'small' }: { type: CardType; size?: 'small' | 'medium' }) {
  const Icon = ICONS[type] ?? LabelOutlinedIcon;
  return (
    <Chip
      size={size}
      variant="outlined"
      color={COLORS[type] ?? 'default'}
      icon={<Icon />}
      label={CARD_TYPE_LABEL[type] ?? type}
    />
  );
}

export function SourceChip({ source }: { source: string }) {
  const map: Record<string, { label: string; color: 'primary' | 'secondary' | 'default' }> = {
    card: { label: '来自卡片', color: 'primary' },
    command: { label: '来自指令', color: 'secondary' },
    manual: { label: '手动创建', color: 'default' },
  };
  const item = map[source] ?? { label: source, color: 'default' as const };
  return <Chip size="small" variant="outlined" color={item.color} label={item.label} />;
}
