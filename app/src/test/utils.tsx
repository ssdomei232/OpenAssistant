import type { ReactElement, ReactNode } from 'react';
import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider } from '@mui/material/styles';
import { ConfigProvider } from '../state/config';
import { FeedbackProvider } from '../components/FeedbackProvider';
import { createAppTheme } from '../theme';

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
}

interface RenderOptions {
  route?: string;
  path?: string;
  wrap?: (children: ReactNode) => ReactNode;
}

export function renderWithProviders(
  ui: ReactElement,
  { route = '/', path, wrap }: RenderOptions = {},
): RenderResult & { queryClient: QueryClient } {
  const queryClient = createTestQueryClient();
  const theme = createAppTheme('light');

  const content = path ? (
    <Routes>
      <Route path={path} element={ui} />
      <Route path="/history" element={<div>历史列表占位</div>} />
    </Routes>
  ) : (
    ui
  );

  const result = render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <ConfigProvider>
          <FeedbackProvider>
            <MemoryRouter initialEntries={[route]}>
              {wrap ? wrap(content) : content}
            </MemoryRouter>
          </FeedbackProvider>
        </ConfigProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { ...result, queryClient };
}
