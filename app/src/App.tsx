import { useEffect, useState } from 'react';
import { RouterProvider } from 'react-router-dom';
import CssBaseline from '@mui/material/CssBaseline';
import { ThemeProvider, useMediaQuery } from '@mui/material';
import { router } from './router';
import { createAppTheme } from './theme';

export function App() {
  const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
  const [mode, setMode] = useState<'light' | 'dark'>(prefersDark ? 'dark' : 'light');

  // 跟随系统深浅色变化
  useEffect(() => {
    setMode(prefersDark ? 'dark' : 'light');
  }, [prefersDark]);

  return (
    <ThemeProvider theme={createAppTheme(mode)}>
      <CssBaseline />
      <RouterProvider router={router} />
    </ThemeProvider>
  );
}
