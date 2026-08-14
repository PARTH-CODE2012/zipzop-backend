import { EditorClient } from './editor-client'

/**
 * The editor route.
 *
 * This server component exists only to read the route param and hand off. All
 * of the editor is client-only: it mounts <video> elements, holds a WebGL
 * context and keeps the timeline document in memory, none of which can be
 * server-rendered. Everything below EditorClient is `'use client'`.
 *
 * Built in M2 (timeline shell) and M3 (editing). See PHASE1-TASKS.md.
 */
export default async function EditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <EditorClient projectId={id} />
}
