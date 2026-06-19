import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

// ---- types (mirror lucid_web.py's JSON contract) ----
export type Readiness = {
  coordinator: boolean; comfyui: boolean; ollama: boolean
  can_dream: boolean; why: string[]
}
export type DreamNode = {
  id: number; parent: number; label: string; prompt: string
  clip: string | null; out_frame: string; length?: number   // chosen segment frame count (@16fps); absent on the opening
}
export type Chain = { nodes: DreamNode[] } | null
export type TurnPhase = 'idle' | 'dreaming' | 'done' | 'skipped' | 'refused' | 'error'
export type Turn = { phase: TurnPhase; label: string | null; error: string | null; elapsed?: number }
export type LucidState = {
  session: string; readiness: Readiness; chain: Chain; private: boolean; turn: Turn
}
export type Beat = { label: string; prompt: string }

// per-process CSRF token embedded in the served index.html
const CSRF = (document.querySelector('meta[name=csrf]') as HTMLMetaElement | null)?.content ?? ''

async function post(path: string, body?: unknown): Promise<any> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Lucid-Token': CSRF },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return res.json()
}
const getJSON = (path: string) => fetch(path).then((r) => r.json())

// ---- media (NEW: the surface plays clips for the first time) ----
export const clipUrl = (id: number) => `/api/clip?id=${id}`
export const frameUrl = (id: number) => `/api/frame?id=${id}`

// ---- queries ----
export function useLucidState() {
  return useQuery<LucidState>({
    queryKey: ['state'],
    queryFn: () => getJSON('/api/state'),
    // adaptive cadence, declaratively: fast while a beat generates, calm otherwise (TanStack handles it)
    refetchInterval: (q) => (q.state.data?.turn.phase === 'dreaming' ? 2500 : 5000),
  })
}

// Beats are HELD per frame: the server rolls them once per chain tip and re-serves the same set
// (lucid_linear.beats_for_tip), so a refetch returns identical suggestions. We key on (session, tipId)
// so the menu is pinned to THIS frame of THIS dream — never aliased to a same-length frame of a prior
// dream after a burn/delete + restart (the old chain-length key collided). staleTime Infinity because
// the server guarantees the hold; the key change (new tip) is what advances the story.
export function useBeats(enabled: boolean, session: string, tipId: number) {
  return useQuery<Beat[]>({
    queryKey: ['beats', session, tipId],
    queryFn: () => getJSON('/api/beats').then((j) => j.beats ?? []),
    enabled,
    staleTime: Infinity,
  })
}

// ---- mutations ----
// `resets`: extra query keys to drop on settle. A dream advancing the chain gets a fresh (session,tipId)
// key for free, but starting/burning/deleting a dream must EVICT the prior dream's held menu so the
// next dream re-rolls instead of serving stale suggestions from the cache.
function useStateMutation<V>(fn: (v: V) => Promise<any>, resets: unknown[][] = []) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['state'] })
      // EVICT (not just invalidate) the held menu: node ids restart at 0 each dream, so a new dream's
      // tip key (['beats', session, 0]) collides with the just-ended dream's cached entry. invalidate
      // leaves the stale set in cache to flash before the refetch lands; removeQueries drops it outright.
      for (const key of resets) qc.removeQueries({ queryKey: key })
    },
  })
}

export const useDream = () => useStateMutation((b: { prompt: string; label: string; length?: number }) => post('/api/dream', b))
export const useStart = () =>
  useStateMutation((b: { private: boolean; image_b64?: string; text?: string; consent?: boolean }) =>
    post('/api/start', b), [['beats']])
export const useBurn = () => useStateMutation((_: void) => post('/api/burn'), [['beats']])
export const useDelete = () => useStateMutation((_: void) => post('/api/delete'), [['beats']])

// ---- ADR-0019 reviewable request queue (the durable held + needs-review board) ----
// Mirrors lucid_hub.board(): path-free by design (no snapshot, no spool location ever reaches here).
export type QueueHeld = {
  id: string; title: string; created: number; age_s: number; attempts: number; last_error: string | null
}
export type QueueReview = { id: string; title: string; since: number }
export type QueueBoard = { held: QueueHeld[]; needs_review: QueueReview[]; recent: unknown[] }

export function useQueue() {
  return useQuery<QueueBoard>({
    queryKey: ['queue'],
    queryFn: () => getJSON('/api/queue'),
    refetchInterval: 5000, // the queue moves on the drainer's cadence; a calm poll keeps the board honest
  })
}

// retry / dismiss / approve a single request by id. The id is validated inside lucid_hub (no traversal);
// every action refetches the board AND the main state (a made request becomes a dream).
function useQueueAction(path: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => post(path, { id }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['queue'] })
      qc.invalidateQueries({ queryKey: ['state'] })
    },
  })
}
export const useQueueRetry = () => useQueueAction('/api/queue/retry')
export const useQueueDismiss = () => useQueueAction('/api/queue/dismiss')
export const useQueueApprove = () => useQueueAction('/api/queue/approve')

export function fileToB64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1])
    r.onerror = reject
    r.readAsDataURL(f)
  })
}
