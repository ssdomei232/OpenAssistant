import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import { ApiError } from '../api/client';

export function ErrorAlert({
  error,
  onRetry,
  title = '加载失败',
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const message =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message === 'card_not_found'
          ? '没有找到这张卡片，可能已被删除。'
          : error.message
        : '发生未知错误，请稍后重试。';
  return (
    <Alert
      severity="error"
      role="alert"
      action={
        onRetry ? (
          <Button color="inherit" size="small" onClick={onRetry}>
            重试
          </Button>
        ) : undefined
      }
    >
      {title}：{message}
    </Alert>
  );
}
