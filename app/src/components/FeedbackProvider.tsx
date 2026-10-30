import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import Snackbar from '@mui/material/Snackbar';

export type FeedbackSeverity = 'success' | 'error' | 'info' | 'warning';

interface FeedbackContextValue {
  notify: (message: string, severity?: FeedbackSeverity) => void;
}

const FeedbackContext = createContext<FeedbackContextValue | null>(null);

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState('');
  const [severity, setSeverity] = useState<FeedbackSeverity>('success');

  const notify = useCallback((nextMessage: string, nextSeverity: FeedbackSeverity = 'success') => {
    setMessage(nextMessage);
    setSeverity(nextSeverity);
    setOpen(true);
  }, []);

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      <Snackbar
        open={open}
        autoHideDuration={severity === 'error' ? 6000 : 3500}
        onClose={(_event, reason) => {
          if (reason === 'clickaway') return;
          setOpen(false);
        }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        sx={{ mb: { xs: '76px', md: 2 }, zIndex: (theme) => theme.zIndex.snackbar }}
      >
        <Alert
          severity={severity}
          variant="filled"
          onClose={() => setOpen(false)}
          sx={{ minWidth: 240, boxShadow: 3 }}
          role="status"
        >
          {message}
        </Alert>
      </Snackbar>
    </FeedbackContext.Provider>
  );
}

export function useFeedback(): FeedbackContextValue {
  const ctx = useContext(FeedbackContext);
  if (!ctx) return { notify: () => undefined };
  return ctx;
}
