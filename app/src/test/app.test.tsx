import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { App } from '../App';
import { ConfigProvider } from '../state/config';
import { FeedbackProvider } from '../components/FeedbackProvider';
import { createTestQueryClient } from './utils';

describe('应用外壳', () => {
  it('渲染顶部栏、演示数据提示与底部导航，并加载首页待办', async () => {
    window.localStorage.clear();
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ConfigProvider>
          <FeedbackProvider>
            <App />
          </FeedbackProvider>
        </ConfigProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('演示数据 · 未连接服务器')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('首页');
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
    expect(await screen.findByText(/你好，小明/, {}, { timeout: 5000 })).toBeInTheDocument();
    expect(await screen.findByText('给张伟发 Q3 排期表', {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
