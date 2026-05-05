import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { Toaster, toast } from 'sonner';
import App from './App';
import { AuthProvider } from './context/AuthContext';
import { RoleViewProvider } from './context/RoleViewContext';
import { ThemeProvider } from './context/ThemeContext';
import { TooltipProvider } from './components/ui/tooltip';
import './index.css';

// Single shared QueryClient.  ``staleTime: 60s`` matches the server-side
// 120-second Samsara cache: by the time the user comes back to a tab the
// upstream data has likely refreshed once, but rapid navigation between
// pages reuses the cached payload.  ``retry: 1`` keeps transient hiccups
// invisible without burying real backend failures under retries.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
    mutations: {
      onError: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Request failed';
        toast.error(msg);
      },
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <BrowserRouter basename="/dashboard">
            <AuthProvider>
              <RoleViewProvider>
                <App />
              </RoleViewProvider>
            </AuthProvider>
          </BrowserRouter>
        </TooltipProvider>
        <Toaster richColors position="top-right" closeButton />
        {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
