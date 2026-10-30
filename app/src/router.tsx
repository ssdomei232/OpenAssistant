import { createBrowserRouter } from 'react-router-dom';
import { Layout } from './components/Layout';
import { HomePage } from './pages/HomePage';
import { SchedulePage } from './pages/SchedulePage';
import { HistoryPage } from './pages/HistoryPage';
import { CardDetailPage } from './pages/CardDetailPage';
import { MeetingsPage } from './pages/MeetingsPage';
import { MeetingDetailPage } from './pages/MeetingDetailPage';
import { ProfilePage } from './pages/ProfilePage';
import { SettingsPage } from './pages/SettingsPage';
import { DevicePage } from './pages/DevicePage';
import { EmptyState } from './components/EmptyState';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'schedule', element: <SchedulePage /> },
      { path: 'history', element: <HistoryPage /> },
      { path: 'history/:id', element: <CardDetailPage /> },
      { path: 'meetings', element: <MeetingsPage /> },
      { path: 'meetings/:id', element: <MeetingDetailPage /> },
      { path: 'me', element: <ProfilePage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'device', element: <DevicePage /> },
      {
        path: '*',
        element: <EmptyState title="页面不存在" description="请从底部导航返回首页。" />,
      },
    ],
  },
]);
