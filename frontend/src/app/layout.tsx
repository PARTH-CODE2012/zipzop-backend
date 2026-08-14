import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { Providers } from '@/app/providers'
import '@/styles/globals.css'

export const metadata: Metadata = {
  title: 'ZipZop — AI Video Editor',
  description: 'A video editor where the AI tools sit in the toolbar next to the ordinary ones.',
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
