import type { ReactNode } from 'react';
import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

interface SectionProps {
  title: string;
  icon?: ReactNode;
  count?: number;
  action?: ReactNode;
  id?: string;
  children: ReactNode;
}

/** 页面分区标题 + 内容，使用 h2 语义，保证屏幕阅读器可导航。 */
export function Section({ title, icon, count, action, id, children }: SectionProps) {
  return (
    <Box component="section" aria-labelledby={id} sx={{ mb: 3.5 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ mb: 1.25, px: 0.25, minHeight: 32 }}
      >
        {icon ? <Box sx={{ color: 'primary.main', display: 'flex' }}>{icon}</Box> : null}
        <Typography id={id} component="h2" variant="h6">
          {title}
        </Typography>
        {typeof count === 'number' ? (
          <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600 }}>
            {count}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        {action}
      </Stack>
      {children}
    </Box>
  );
}
