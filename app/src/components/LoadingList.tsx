import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';

/** 列表加载态：与卡片列表结构一致，避免布局抖动。 */
export function LoadingList({ count = 3 }: { count?: number }) {
  return (
    <Stack spacing={1.5} aria-busy="true" aria-label="正在加载">
      {Array.from({ length: count }).map((_, index) => (
        <Card key={index}>
          <CardContent>
            <Skeleton variant="text" width="55%" height={28} />
            <Skeleton variant="text" width="100%" />
            <Skeleton variant="text" width="88%" />
            <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
              <Skeleton variant="rounded" width={56} height={24} />
              <Skeleton variant="rounded" width={72} height={24} />
              <Skeleton variant="rounded" width={48} height={24} />
            </Stack>
          </CardContent>
        </Card>
      ))}
    </Stack>
  );
}
