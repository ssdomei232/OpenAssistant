import Chip from '@mui/material/Chip';
import type { Speaker } from '../api/types';

const LABELS: Record<Speaker, string> = {
  user: '[使用者]',
  other: '[其他人]',
  unknown: '[未知]',
};

const STYLES: Record<Speaker, { bgcolor: string; color: string }> = {
  user: { bgcolor: 'primary.main', color: 'primary.contrastText' },
  other: { bgcolor: 'secondary.main', color: 'secondary.contrastText' },
  unknown: { bgcolor: 'warning.main', color: '#ffffff' },
};

interface SpeakerChipProps {
  speaker: Speaker;
  label?: string;
  size?: 'small' | 'medium';
}

/** ASR 原文的身份标签：[使用者] / [其他人] / [未知]，用不同颜色区分。 */
export function SpeakerChip({ speaker, label, size = 'small' }: SpeakerChipProps) {
  return (
    <Chip
      size={size}
      label={label || LABELS[speaker]}
      aria-label={`说话人 ${label || LABELS[speaker]}`}
      sx={{ ...STYLES[speaker], fontWeight: 600, fontSize: '0.72rem', height: 24 }}
    />
  );
}
