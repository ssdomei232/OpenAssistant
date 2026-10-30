import type { ReactNode } from 'react';
import Fab from '@mui/material/Fab';
import AddIcon from '@mui/icons-material/Add';

interface PageFabProps {
  label: string;
  onClick: () => void;
  icon?: ReactNode;
}

/** 移动端优先的悬浮操作按钮：位于底部导航之上，桌面端右下角。 */
export function PageFab({ label, onClick, icon }: PageFabProps) {
  return (
    <Fab
      variant="extended"
      color="primary"
      onClick={onClick}
      aria-label={label}
      sx={{
        position: 'fixed',
        zIndex: (theme) => theme.zIndex.speedDial,
        bottom: { xs: 'calc(84px + var(--safe-bottom))', md: 32 },
        right: { xs: 16, md: 32 },
        fontWeight: 600,
      }}
    >
      {icon ?? <AddIcon sx={{ mr: 1 }} />}
      {label}
    </Fab>
  );
}
