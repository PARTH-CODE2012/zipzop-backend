/**
 * Project list — contract §5, and M4.5 item 1.
 *
 * A server component wrapping a client one, for the same reason every other
 * account-aware screen does: the list belongs to the signed-in user, the session
 * lives in the browser, and rendering it on the server would either leak one
 * account's projects into another's cache or need the token somewhere it should
 * not be.
 */
import { ProjectsClient } from '@/app/projects/projects-client'

export default function ProjectsPage() {
  return <ProjectsClient />
}
