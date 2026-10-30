import type { ReactNode } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import InboxOutlinedIcon from '@mui/icons-material/InboxOutlined';

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
}

export function EmptyState({ title, description, icon, actionLabel, onAction }: EmptyStateProps) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      spacing={1}
      sx={{
        textAlign: 'center',
        py: 6,
        px: 3,
        border: '1px dashed',
        borderColor: 'divider',
        borderRadius: 5,
        color: 'text.secondary',
      }}
      role="status"
    >
      <Box sx={{ color: 'text.secondary', '& svg': { fontSize: 44 }, opacity: 0.8 }}>
        {icon ?? <InboxOutlinedIcon />}
      </Box>
      <Typography variant="subtitle1" color="text.primary">
        {title}
      </Typography>
      {description ? (
        <Typography variant="body2" sx={{ maxWidth: 360 }}>
          {description}
        </Typography>
      ) : null}
      {actionLabel && onAction ? (
        <Button onClick={onAction} variant="text" sx={{ mt: 1 }}>
          {actionLabel}
        </Button>
      ) : null}
    </Stack>
  );
}
