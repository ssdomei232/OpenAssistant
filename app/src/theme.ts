import { createTheme, type Theme } from '@mui/material/styles';

/**
 * Material Design 3 风格主题 token。
 * 色彩参考 MD3 tonal palette：primary / primaryContainer / surface / surfaceVariant / outline。
 */
const light = {
  primary: '#146C60',
  onPrimary: '#FFFFFF',
  primaryContainer: '#A7F2E4',
  onPrimaryContainer: '#00201B',
  secondary: '#4A635D',
  onSecondary: '#FFFFFF',
  secondaryContainer: '#CCE8E0',
  onSecondaryContainer: '#06201B',
  surface: '#FBFDF9',
  surfaceDim: '#DCE5E0',
  surfaceContainer: '#EFF4F0',
  surfaceContainerHigh: '#E7EFEA',
  onSurface: '#191C1B',
  onSurfaceVariant: '#3F4946',
  outline: '#6F7975',
  outlineVariant: '#BEC9C4',
  error: '#BA1A1A',
  warning: '#7D5700',
  success: '#266B3D',
};

const dark = {
  primary: '#8BD5C8',
  onPrimary: '#003731',
  primaryContainer: '#005047',
  onPrimaryContainer: '#A7F2E4',
  secondary: '#B0CCC4',
  onSecondary: '#1B3530',
  secondaryContainer: '#334B45',
  onSecondaryContainer: '#CCE8E0',
  surface: '#0E1513',
  surfaceDim: '#0E1513',
  surfaceContainer: '#1A211F',
  surfaceContainerHigh: '#252B29',
  onSurface: '#E0E3E0',
  onSurfaceVariant: '#BEC9C4',
  outline: '#89938F',
  outlineVariant: '#3F4946',
  error: '#FFB4AB',
  warning: '#FFDEA0',
  success: '#9CD6A8',
};

const fontFamily = [
  'system-ui',
  '-apple-system',
  '"Segoe UI"',
  'Roboto',
  '"Noto Sans SC"',
  '"PingFang SC"',
  '"Microsoft YaHei"',
  'sans-serif',
].join(', ');

export function createAppTheme(mode: 'light' | 'dark'): Theme {
  const token = mode === 'dark' ? dark : light;
  return createTheme({
    palette: {
      mode,
      primary: { main: token.primary, contrastText: token.onPrimary },
      secondary: { main: token.secondary, contrastText: token.onSecondary },
      error: { main: token.error },
      warning: { main: token.warning },
      success: { main: token.success },
      background: { default: token.surface, paper: mode === 'dark' ? token.surfaceContainer : '#FFFFFF' },
      text: { primary: token.onSurface, secondary: token.onSurfaceVariant },
      divider: token.outlineVariant,
    },
    shape: { borderRadius: 16 },
    typography: {
      fontFamily,
      h1: { fontSize: '2rem', fontWeight: 700, letterSpacing: '-0.02em' },
      h2: { fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.015em' },
      h3: { fontSize: '1.5rem', fontWeight: 700 },
      h4: { fontSize: '1.375rem', fontWeight: 700, letterSpacing: '-0.01em' },
      h5: { fontSize: '1.25rem', fontWeight: 700 },
      h6: { fontSize: '1.075rem', fontWeight: 600 },
      subtitle1: { fontSize: '1rem', fontWeight: 600 },
      subtitle2: { fontSize: '0.875rem', fontWeight: 600 },
      body1: { fontSize: '0.975rem', lineHeight: 1.65 },
      body2: { fontSize: '0.875rem', lineHeight: 1.6 },
      button: { textTransform: 'none', fontWeight: 600, letterSpacing: 0 },
      caption: { fontSize: '0.75rem', lineHeight: 1.5 },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundColor: token.surface,
            color: token.onSurface,
            WebkitTapHighlightColor: 'transparent',
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: { backgroundImage: 'none' },
        },
      },
      MuiCard: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          root: {
            borderRadius: 20,
            border: `1px solid ${token.outlineVariant}`,
            backgroundColor: mode === 'dark' ? token.surfaceContainer : '#FFFFFF',
          },
        },
      },
      MuiCardContent: {
        styleOverrides: {
          root: {
            padding: 16,
            '&:last-child': { paddingBottom: 16 },
          },
        },
      },
      MuiAppBar: {
        defaultProps: { elevation: 0, color: 'inherit' },
        styleOverrides: {
          root: {
            backgroundColor: mode === 'dark' ? token.surfaceContainer : token.surface,
            color: token.onSurface,
            borderBottom: `1px solid ${token.outlineVariant}`,
            backdropFilter: 'saturate(140%) blur(8px)',
          },
        },
      },
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: { borderRadius: 999, paddingInline: 18, minHeight: 40 },
          containedPrimary: { color: token.onPrimary },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: { borderRadius: 8, fontWeight: 500 },
          sizeSmall: { height: 26, fontSize: '0.75rem' },
        },
      },
      MuiFab: {
        styleOverrides: {
          root: { borderRadius: 18, textTransform: 'none' },
        },
      },
      MuiDialog: {
        styleOverrides: {
          paper: { borderRadius: 28, padding: 4 },
        },
      },
      MuiListItemButton: {
        styleOverrides: {
          root: { borderRadius: 14 },
        },
      },
      MuiOutlinedInput: {
        styleOverrides: {
          root: { borderRadius: 14 },
        },
      },
      MuiBottomNavigation: {
        styleOverrides: {
          root: {
            backgroundColor: mode === 'dark' ? token.surfaceContainer : '#FBFDF9',
            borderTop: `1px solid ${token.outlineVariant}`,
          },
        },
      },
      MuiBottomNavigationAction: {
        styleOverrides: {
          root: {
            minWidth: 56,
            '&.Mui-selected': { color: token.primary },
          },
          label: { fontSize: '0.7rem', '&.Mui-selected': { fontSize: '0.72rem' } },
        },
      },
      MuiTooltip: {
        defaultProps: { arrow: true },
      },
      MuiSwitch: {
        defaultProps: { color: 'primary' },
      },
      MuiSkeleton: {
        styleOverrides: {
          root: { borderRadius: 12 },
        },
      },
      MuiAlert: {
        styleOverrides: {
          root: { borderRadius: 16 },
        },
      },
      MuiTab: {
        styleOverrides: {
          root: { textTransform: 'none', fontWeight: 600, minHeight: 44 },
        },
      },
    },
  });
}

/** 供非组件文件（如内联 sx）复用的颜色 token。 */
export const md3 = { light, dark };
