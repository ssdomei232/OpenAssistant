import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from './App';
import { ConfigProvider } from './state/config';
import { FeedbackProvider } from './components/FeedbackProvider';
import './styles/global.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const container = document.getElementById('root');
if (!container) throw new Error('缺少 #root 挂载节点');

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider>
        <FeedbackProvider>
          <App />
        </FeedbackProvider>
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
);

// PWA：生产环境注册 Service Worker（开发环境避免缓存干扰）。
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // 注册失败不影响正常使用
    });
  });
}
