'use client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'

import { ApiError } from '@/lib/api/client'

/**
 * TanStack Query owns *server* state: projects, assets, jobs, credits, plan.
 *
 * It does NOT own the timeline. The open project's timeline lives in a Zustand
 * store and changes at interaction rate — putting it in a query cache means
 * every clip drag triggers a refetch. See docs/04-frontend-architecture.md §2.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: (failureCount, error) => {
              // Retrying a 4xx just repeats the same rejection. Only server
              // and network failures are worth a second attempt.
              if (error instanceof ApiError && error.status < 500) return false
              return failureCount < 2
            },
          },
          mutations: { retry: false },
        },
      }),
  )

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
