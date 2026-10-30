import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardActionArea from '@mui/material/CardActionArea';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import { useMeetings } from '../api/queries';
import { EmptyState } from '../components/EmptyState';
import { ErrorAlert } from '../components/ErrorAlert';
import { LoadingList } from '../components/LoadingList';
import { SpeakerChip } from '../components/SpeakerChip';
import { formatDateTime, formatDuration } from '../lib/format';
import type { MeetingParticipant } from '../api/types';
import type { Speaker } from '../api/types';

export function participantSpeaker(label: string): Speaker {
  if (label.includes('使用者')) return 'user';
  if (label.includes('其他人')) return 'other';
  return 'unknown';
}

export function ParticipantChip({ participant }: { participant: MeetingParticipant }) {
  return <SpeakerChip speaker={participantSpeaker(participant.label)} label={participant.name} />;
}

export function MeetingsPage() {
  const navigate = useNavigate();
  const meetingsQuery = useMeetings({ limit: 50 });
  const meetings = meetingsQuery.data?.items ?? [];

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2, px: 0.5 }}>
        满足「时长足够且至少两个说话人」的对话会自动生成会议纪要，也可以在这里重新生成与编辑。
      </Typography>

      {meetingsQuery.isLoading ? <LoadingList count={3} /> : null}
      {meetingsQuery.isError ? (
        <ErrorAlert error={meetingsQuery.error} onRetry={() => void meetingsQuery.refetch()} />
      ) : null}

      {!meetingsQuery.isLoading && !meetingsQuery.isError && meetings.length === 0 ? (
        <EmptyState
          icon={<GroupsOutlinedIcon />}
          title="还没有检测到会议"
          description="当一段对话包含多人且持续足够长时，系统会自动整理议题、决议与行动项。"
        />
      ) : null}

      <Stack spacing={1.5}>
        {meetings.map((meeting) => {
          const durationSeconds =
            (new Date(meeting.ended_at).getTime() - new Date(meeting.started_at).getTime()) / 1000;
          return (
            <Card key={meeting.id}>
              <CardActionArea
                onClick={() => navigate(`/meetings/${meeting.id}`)}
                aria-label={`查看会议纪要：${meeting.title}`}
              >
                <CardContent>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
                    <GroupsOutlinedIcon fontSize="small" color="secondary" />
                    <Typography variant="caption" color="text.secondary">
                      {formatDateTime(meeting.started_at)} · {formatDuration(durationSeconds)}
                    </Typography>
                    <Box sx={{ flex: 1 }} />
                    <Chip
                      size="small"
                      label={meeting.status === 'ready' ? '纪要已生成' : '生成中'}
                      color={meeting.status === 'ready' ? 'success' : 'default'}
                      variant="outlined"
                    />
                  </Stack>

                  <Typography variant="h6" component="h3" sx={{ mb: 0.5 }}>
                    {meeting.title}
                  </Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                      mb: 1.25,
                    }}
                  >
                    {meeting.summary}
                  </Typography>

                  <Stack direction="row" spacing={0.75} sx={{ flexWrap: 'wrap', rowGap: 0.75 }}>
                    {meeting.participants.slice(0, 4).map((participant, index) => (
                      <ParticipantChip
                        key={`${participant.label}-${participant.name}-${index}`}
                        participant={participant}
                      />
                    ))}
                    <Chip
                      size="small"
                      icon={<TaskAltIcon />}
                      variant="outlined"
                      label={`${meeting.action_items.length} 个行动项`}
                    />
                  </Stack>
                </CardContent>
              </CardActionArea>
            </Card>
          );
        })}
      </Stack>
    </Box>
  );
}
