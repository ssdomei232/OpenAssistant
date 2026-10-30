import { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import AppBar from '@mui/material/AppBar';
import BottomNavigation from '@mui/material/BottomNavigation';
import BottomNavigationAction from '@mui/material/BottomNavigationAction';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Divider from '@mui/material/Divider';
import Drawer from '@mui/material/Drawer';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Toolbar from '@mui/material/Toolbar';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import BluetoothIcon from '@mui/icons-material/Bluetooth';
import CalendarMonthIcon from '@mui/icons-material/CalendarMonth';
import CalendarMonthOutlinedIcon from '@mui/icons-material/CalendarMonthOutlined';
import GraphicEqIcon from '@mui/icons-material/GraphicEq';
import GroupsIcon from '@mui/icons-material/Groups';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import HistoryIcon from '@mui/icons-material/History';
import HistoryOutlinedIcon from '@mui/icons-material/HistoryOutlined';
import HomeIcon from '@mui/icons-material/Home';
import HomeOutlinedIcon from '@mui/icons-material/HomeOutlined';
import PersonIcon from '@mui/icons-material/Person';
import PersonOutlineIcon from '@mui/icons-material/PersonOutline';
import RefreshIcon from '@mui/icons-material/Refresh';
import SettingsIcon from '@mui/icons-material/Settings';
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined';
import { useFeedback } from './FeedbackProvider';
import { useConfig } from '../state/config';

interface NavItem {
  label: string;
  to: string;
  icon: typeof HomeIcon;
  activeIcon: typeof HomeIcon;
}

const primaryNav: NavItem[] = [
  { label: '首页', to: '/', icon: HomeOutlinedIcon, activeIcon: HomeIcon },
  { label: '日程', to: '/schedule', icon: CalendarMonthOutlinedIcon, activeIcon: CalendarMonthIcon },
  { label: '历史', to: '/history', icon: HistoryOutlinedIcon, activeIcon: HistoryIcon },
  { label: '会议', to: '/meetings', icon: GroupsOutlinedIcon, activeIcon: GroupsIcon },
  { label: '我的', to: '/me', icon: PersonOutlineIcon, activeIcon: PersonIcon },
];

const secondaryNav: NavItem[] = [
  { label: '设置', to: '/settings', icon: SettingsOutlinedIcon, activeIcon: SettingsIcon },
  { label: '设备管理', to: '/device', icon: BluetoothIcon, activeIcon: BluetoothIcon },
];

const TITLES: Record<string, string> = {
  '/': '首页',
  '/schedule': '日程',
  '/history': '历史记录',
  '/meetings': '会议纪要',
  '/me': '我的',
  '/settings': '设置',
  '/device': '设备管理',
};

function resolveTitle(pathname: string): string {
  if (/^\/history\/[^/]+$/.test(pathname)) return '卡片详情';
  if (/^\/meetings\/[^/]+$/.test(pathname)) return '会议详情';
  return TITLES[pathname] ?? 'OpenAssistant';
}

function isActive(pathname: string, to: string): boolean {
  return to === '/' ? pathname === '/' : pathname === to || pathname.startsWith(`${to}/`);
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const renderItem = (item: NavItem) => {
    const selected = isActive(pathname, item.to);
    const Icon = selected ? item.activeIcon : item.icon;
    return (
      <ListItemButton
        key={item.to}
        selected={selected}
        onClick={() => {
          navigate(item.to);
          onNavigate?.();
        }}
        aria-current={selected ? 'page' : undefined}
        sx={{ mb: 0.5, minHeight: 48 }}
      >
        <ListItemIcon sx={{ minWidth: 40 }}>
          <Icon color={selected ? 'primary' : 'inherit'} />
        </ListItemIcon>
        <ListItemText
          primary={item.label}
          slotProps={{ primary: { fontWeight: selected ? 700 : 500 } }}
        />
      </ListItemButton>
    );
  };

  return (
    <List sx={{ px: 1.5, py: 1 }}>
      {primaryNav.map(renderItem)}
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="overline" sx={{ px: 2, color: 'text.secondary' }}>
        管理
      </Typography>
      {secondaryNav.map(renderItem)}
    </List>
  );
}

function Brand() {
  return (
    <Stack direction="row" spacing={1.25} alignItems="center" sx={{ px: 2.5, py: 2 }}>
      <Box
        sx={{
          width: 40,
          height: 40,
          borderRadius: 3,
          display: 'grid',
          placeItems: 'center',
          bgcolor: 'primary.main',
          color: 'primary.contrastText',
        }}
      >
        <GraphicEqIcon />
      </Box>
      <Box>
        <Typography variant="subtitle1" component="div" sx={{ lineHeight: 1.2 }}>
          OpenAssistant
        </Typography>
        <Typography variant="caption" color="text.secondary">
          个人语音助手
        </Typography>
      </Box>
    </Stack>
  );
}

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { notify } = useFeedback();
  const { isDemo } = useConfig();
  const [refreshing, setRefreshing] = useState(false);

  const title = resolveTitle(location.pathname);
  const isPrimaryRoute = primaryNav.some((item) => isActive(location.pathname, item.to));
  const showBack =
    !isPrimaryRoute ||
    /^\/history\/[^/]+$/.test(location.pathname) ||
    /^\/meetings\/[^/]+$/.test(location.pathname);
  const activeIndex = primaryNav.findIndex((item) => isActive(location.pathname, item.to));

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await queryClient.refetchQueries({ type: 'active' });
      notify('已刷新最新数据');
    } catch {
      notify('刷新失败，请稍后重试', 'error');
    } finally {
      setRefreshing(false);
    }
  };

  const handleBack = () => {
    if (window.history.length > 1) navigate(-1);
    else navigate('/');
  };

  return (
    <Box sx={{ display: 'flex', minHeight: '100dvh', bgcolor: 'background.default' }}>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>

      <Drawer
        variant="permanent"
        open
        sx={{
          display: { xs: 'none', md: 'block' },
          width: 256,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width: 256,
            boxSizing: 'border-box',
            borderRight: '1px solid',
            borderColor: 'divider',
            bgcolor: 'background.default',
          },
        }}
      >
        <Brand />
        <Divider />
        <NavList />
        <Box sx={{ mt: 'auto', p: 2 }}>
          <Typography variant="caption" color="text.secondary">
            {isDemo ? '演示模式 · 本地 fixtures' : '已连接服务器'}
          </Typography>
        </Box>
      </Drawer>

      <Box sx={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <AppBar position="sticky" sx={{ pt: 'var(--safe-top)' }}>
          <Toolbar sx={{ gap: 1, minHeight: { xs: 56, md: 64 } }}>
            {showBack ? (
              <IconButton edge="start" onClick={handleBack} aria-label="返回上一页">
                <ArrowBackIcon />
              </IconButton>
            ) : null}
            <Typography
              variant="h6"
              component="h1"
              sx={{ flex: 1, fontWeight: 700, letterSpacing: '-0.01em' }}
            >
              {title}
            </Typography>
            <Tooltip title="刷新">
              <span>
                <IconButton
                  onClick={handleRefresh}
                  disabled={refreshing}
                  aria-label="刷新数据"
                  color="default"
                >
                  {refreshing ? <CircularProgress size={20} /> : <RefreshIcon />}
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="设置">
              <IconButton onClick={() => navigate('/settings')} aria-label="打开设置">
                <SettingsIcon />
              </IconButton>
            </Tooltip>
          </Toolbar>
        </AppBar>

        {isDemo ? (
          <Box
            role="status"
            sx={{
              bgcolor: 'primary.main',
              color: 'primary.contrastText',
              px: 2,
              py: 0.75,
            }}
          >
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              justifyContent="center"
              sx={{ maxWidth: 960, mx: 'auto' }}
            >
              <Typography variant="caption" sx={{ fontWeight: 600 }}>
                演示数据 · 未连接服务器
              </Typography>
              <Typography variant="caption" sx={{ opacity: 0.9, display: { xs: 'none', sm: 'block' } }}>
                在设置中填写服务器地址与 API Key 即可切换为真实数据
              </Typography>
              <Box
                component="button"
                type="button"
                onClick={() => navigate('/settings')}
                sx={{
                  border: '1px solid currentColor',
                  bgcolor: 'transparent',
                  color: 'inherit',
                  borderRadius: 999,
                  px: 1.25,
                  py: 0.25,
                  ml: 'auto',
                  fontSize: '0.72rem',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                去设置
              </Box>
            </Stack>
          </Box>
        ) : null}

        <Box
          component="main"
          id="main"
          className="app-shell"
          sx={{
            flex: 1,
            width: '100%',
            maxWidth: 960,
            mx: 'auto',
            pt: 2,
            pb: { xs: 'calc(88px + var(--safe-bottom))', md: 6 },
          }}
        >
          <Outlet />
        </Box>

        <Paper
          component="nav"
          aria-label="主导航"
          elevation={0}
          sx={{
            position: 'fixed',
            bottom: 0,
            left: 0,
            right: 0,
            display: { xs: 'block', md: 'none' },
            pb: 'var(--safe-bottom)',
            zIndex: (theme) => theme.zIndex.appBar,
          }}
        >
          <BottomNavigation
            showLabels
            value={activeIndex}
            onChange={(_event, value: number) => navigate(primaryNav[value].to)}
            sx={{ height: 64 }}
          >
            {primaryNav.map((item) => {
              const Icon = item.icon;
              return (
                <BottomNavigationAction
                  key={item.to}
                  label={item.label}
                  icon={<Icon />}
                  aria-label={item.label}
                />
              );
            })}
          </BottomNavigation>
        </Paper>
      </Box>
    </Box>
  );
}
