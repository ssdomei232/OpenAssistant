import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CardDetailPage } from '../pages/CardDetailPage';
import { api } from '../api/client';
import { resetDemoData } from '../api/demo';
import { renderWithProviders } from './utils';

const CARD_ID = 'card_demo_q3';

beforeEach(() => {
  resetDemoData();
  window.localStorage.clear();
});

describe('历史卡片详情', () => {
  it('渲染 ASR 原文与不同颜色的身份标签', async () => {
    renderWithProviders(<CardDetailPage />, {
      route: `/history/${CARD_ID}`,
      path: '/history/:id',
    });

    expect(
      await screen.findByRole('heading', { name: '和产品经理讨论 Q3 排期' }, { timeout: 5000 }),
    ).toBeInTheDocument();

    // 身份标签：至少各出现一次
    expect(screen.getAllByText('[使用者]').length).toBeGreaterThan(0);
    expect(screen.getAllByText('[其他人]').length).toBeGreaterThan(0);

    // ASR 原文按 turn 展示
    expect(
      screen.getByText('帮我记一下，明天下午三点和周磊开会，地点在三号会议室。'),
    ).toBeInTheDocument();
    expect(screen.getByText('好的，你们在哪个会议室？')).toBeInTheDocument();

    // 关联日程 / 待办
    expect(screen.getByText('和周磊开会')).toBeInTheDocument();
    expect(screen.getByText('给张伟发 Q3 排期表')).toBeInTheDocument();
  });

  it('勘误保存会调用 PATCH /cards/{id} 并带上字段与说明', async () => {
    const user = userEvent.setup();
    const updateSpy = vi.spyOn(api, 'updateCard');
    renderWithProviders(<CardDetailPage />, {
      route: `/history/${CARD_ID}`,
      path: '/history/:id',
    });

    await screen.findByRole('heading', { name: '和产品经理讨论 Q3 排期' }, { timeout: 5000 });

    await user.click(screen.getByRole('button', { name: '勘误' }));
    const titleField = await screen.findByRole('textbox', { name: '标题' });
    await user.clear(titleField);
    await user.type(titleField, '勘误后的标题');
    await user.type(screen.getByRole('textbox', { name: '勘误说明' }), '测试勘误说明');
    await user.click(screen.getByRole('button', { name: '保存勘误' }));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [id, patch] = updateSpy.mock.calls[0];
    expect(id).toBe(CARD_ID);
    expect(patch).toMatchObject({ title: '勘误后的标题', note: '测试勘误说明' });

    expect(await screen.findByText('勘误已保存，相关日程与画像将同步更新')).toBeInTheDocument();
  });

  it('删除需要确认，确认后调用 DELETE /cards/{id} 并说明级联清理', async () => {
    const user = userEvent.setup();
    const deleteSpy = vi.spyOn(api, 'deleteCard');
    renderWithProviders(<CardDetailPage />, {
      route: `/history/${CARD_ID}`,
      path: '/history/:id',
    });

    await screen.findByRole('heading', { name: '和产品经理讨论 Q3 排期' }, { timeout: 5000 });

    await user.click(screen.getByRole('button', { name: '删除卡片' }));
    expect(await screen.findByText(/级联清理/)).toBeInTheDocument();
    expect(screen.getByText(/用户画像标记为需要重新计算|标记为需要重新计算/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '确认删除' }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith(CARD_ID));
    expect(await screen.findByText('历史列表占位')).toBeInTheDocument();
  });
});
