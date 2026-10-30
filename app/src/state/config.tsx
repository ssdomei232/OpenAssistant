import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  DEFAULT_CONFIG,
  loadConfig,
  resetConfig,
  saveConfig,
  type AppConfig,
} from '../api/client';

interface ConfigContextValue {
  config: AppConfig;
  /** 未配置 API Key 或显式开启演示模式 */
  isDemo: boolean;
  updateConfig: (patch: Partial<AppConfig>) => AppConfig;
  reset: () => void;
}

const ConfigContext = createContext<ConfigContextValue | null>(null);

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig>(() => loadConfig());
  const queryClient = useQueryClient();

  const updateConfig = useCallback(
    (patch: Partial<AppConfig>) => {
      const next = saveConfig(patch);
      setConfig(next);
      // 服务器/密钥/演示模式变化后，之前的数据不再可信，直接清空缓存。
      void queryClient.clear();
      return next;
    },
    [queryClient],
  );

  const reset = useCallback(() => {
    const next = resetConfig();
    setConfig(next);
    void queryClient.clear();
  }, [queryClient]);

  const value = useMemo<ConfigContextValue>(
    () => ({
      config,
      isDemo: config.demoMode || config.apiKey.trim().length === 0,
      updateConfig,
      reset,
    }),
    [config, updateConfig, reset],
  );

  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useConfig(): ConfigContextValue {
  const ctx = useContext(ConfigContext);
  if (!ctx) {
    // 兜底：极端情况下（组件被单独渲染）返回只读默认值，避免整页崩溃。
    return {
      config: { ...DEFAULT_CONFIG, ...loadConfig() },
      isDemo: true,
      updateConfig: () => loadConfig(),
      reset: () => undefined,
    };
  }
  return ctx;
}
