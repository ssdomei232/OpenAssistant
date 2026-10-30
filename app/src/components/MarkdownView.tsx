import Box from '@mui/material/Box';
import Divider from '@mui/material/Divider';
import Typography from '@mui/material/Typography';
import type { ReactNode } from 'react';

/**
 * 极简 Markdown 渲染：支持标题、列表、引用、分隔线、**粗体** 与 `行内代码`。
 * 会议纪要只需要这些语法，避免引入额外依赖。
 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const regex = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let lastIndex = 0;
  let index = 0;
  for (const match of text.matchAll(regex)) {
    const start = match.index ?? 0;
    if (start > lastIndex) nodes.push(text.slice(lastIndex, start));
    const token = match[0];
    const key = `${keyPrefix}-i${index}`;
    index += 1;
    if (token.startsWith('**')) {
      nodes.push(
        <Box component="strong" key={key} sx={{ fontWeight: 700 }}>
          {token.slice(2, -2)}
        </Box>,
      );
    } else {
      nodes.push(
        <Box
          component="code"
          key={key}
          sx={{
            px: 0.5,
            py: 0.15,
            borderRadius: 1,
            bgcolor: 'action.hover',
            fontSize: '0.85em',
          }}
        >
          {token.slice(1, -1)}
        </Box>,
      );
    }
    lastIndex = start + token.length;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

export function MarkdownView({ markdown }: { markdown: string }) {
  const lines = markdown.split(/\r?\n/);
  const blocks: ReactNode[] = [];
  let listBuffer: { ordered: boolean; text: string }[] = [];

  const flushList = () => {
    if (!listBuffer.length) return;
    const ordered = listBuffer[0].ordered;
    blocks.push(
      <Box
        component={ordered ? 'ol' : 'ul'}
        key={`list-${blocks.length}`}
        sx={{ pl: 3, my: 1, color: 'text.primary' }}
      >
        {listBuffer.map((item, index) => (
          <Typography component="li" variant="body2" key={index} sx={{ mb: 0.5, pl: 0.5 }}>
            {renderInline(item.text, `li-${blocks.length}-${index}`)}
          </Typography>
        ))}
      </Box>,
    );
    listBuffer = [];
  };

  lines.forEach((rawLine, lineIndex) => {
    const line = rawLine.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line.trim());
    const bullet = /^[-*]\s+(.*)$/.exec(line.trim());
    const ordered = /^\d+\.\s+(.*)$/.exec(line.trim());

    if (bullet || ordered) {
      if (listBuffer.length && listBuffer[0].ordered !== Boolean(ordered)) flushList();
      listBuffer.push({ ordered: Boolean(ordered), text: (bullet ?? ordered)?.[1] ?? '' });
      return;
    }
    flushList();

    if (!line.trim()) {
      return;
    }
    if (heading) {
      const level = heading[1].length;
      const variant = level === 1 ? 'h5' : level === 2 ? 'h6' : 'subtitle1';
      blocks.push(
        <Typography key={`h-${lineIndex}`} variant={variant} sx={{ mt: 2, mb: 0.75 }}>
          {renderInline(heading[2], `h-${lineIndex}`)}
        </Typography>,
      );
      return;
    }
    if (/^---+$/.test(line.trim())) {
      blocks.push(<Divider key={`hr-${lineIndex}`} sx={{ my: 2 }} />);
      return;
    }
    if (line.trim().startsWith('>')) {
      blocks.push(
        <Box
          key={`quote-${lineIndex}`}
          sx={{
            borderLeft: 3,
            borderColor: 'primary.main',
            pl: 1.5,
            my: 1,
            color: 'text.secondary',
          }}
        >
          <Typography variant="body2">{renderInline(line.trim().slice(1).trim(), `q-${lineIndex}`)}</Typography>
        </Box>,
      );
      return;
    }
    blocks.push(
      <Typography
        key={`p-${lineIndex}`}
        variant="body2"
        sx={{ my: 0.75, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
      >
        {renderInline(line, `p-${lineIndex}`)}
      </Typography>,
    );
  });
  flushList();

  return <Box sx={{ '& > *:first-of-type': { mt: 0 } }}>{blocks}</Box>;
}
