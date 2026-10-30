import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HomePage } from '../pages/HomePage';
import { api } from '../api/client';
import { resetDemoData } from '../api/demo';
import { renderWithProviders } from './utils';

beforeEach(() => {
  resetDemoData();
  window.localStorage.clear();
});

describe('首页待办交互', () => {
  it('勾选待办会调用 PATCH /todos/{id} 并移动到已完成', async () => {
    const user = userEvent.setup();
    const updateSpy = vi.spyOn(api, 'updateTodo');
    renderWithProviders(<HomePage />);

    expect(await screen.findByText('待办事项', {}, { timeout: 5000 })).toBeInTheDocument();

    const checkbox = await screen.findByRole(
      'checkbox',
      { name: '完成待办：给张伟发 Q3 排期表' },
      { timeout: 5000 },
    );
    await user.click(checkbox);

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith('todo_demo_q3', { status: 'done' }),
    );

    // 完成提示（Snackbar）
    expect(await screen.findByText('已完成：给张伟发 Q3 排期表')).toBeInTheDocument();

    // 未完成列表里不再出现该待办，已完成计数 +1
    await waitFor(() =>
      expect(
        screen.queryByRole('checkbox', { name: '完成待办：给张伟发 Q3 排期表' }),
      ).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByRole('button', { name: '展开已完成待办' }, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开已完成待办' })).toHaveTextContent(
      '查看已完成（2）',
    );
  });
});
