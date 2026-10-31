import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HistoryPage } from '../pages/HistoryPage';
import { api } from '../api/client';
import { resetDemoData } from '../api/demo';
import { renderWithProviders } from './utils';

beforeEach(() => {
  resetDemoData();
  window.localStorage.clear();
});

describe('历史记录：手动合并对话', () => {
  it('多选两张对话卡片后合并并重新总结', async () => {
    const user = userEvent.setup();
    const mergeSpy = vi.spyOn(api, 'mergeCards');
    renderWithProviders(<HistoryPage />);

    expect(await screen.findByText('和产品经理讨论 Q3 排期', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(await screen.findByText('约周三晚上打羽毛球')).toBeInTheDocument();

    // 进入多选模式（会议卡片不可选）
    await user.click(screen.getByRole('button', { name: '合并多个对话' }));
    const meetingCheckbox = await screen.findByRole('checkbox', {
      name: '选择合并：项目周会：Q3 排期评审',
    });
    expect(meetingCheckbox).toBeDisabled();

    await user.click(
      await screen.findByRole('checkbox', { name: '选择合并：和产品经理讨论 Q3 排期' }),
    );
    await user.click(await screen.findByRole('checkbox', { name: '选择合并：约周三晚上打羽毛球' }));
    expect(await screen.findByText('已选 2 个对话')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '合并并重新总结' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/合并成一张卡片并重新总结/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '合并并重新总结' }));

    await waitFor(() =>
      expect(mergeSpy).toHaveBeenCalledWith(
        ['card_demo_q3', 'card_demo_badminton'],
        undefined,
      ),
    );
    expect(await screen.findByText('已合并 2 个对话并重新总结')).toBeInTheDocument();

    // 合并后列表只剩时间最早的主卡片（其余已归档）
    await waitFor(() =>
      expect(screen.queryByText('和产品经理讨论 Q3 排期', { exact: false })).not.toBeInTheDocument(),
    );
    expect(await screen.findByText('约周三晚上打羽毛球（已合并）')).toBeInTheDocument();
  });
});
