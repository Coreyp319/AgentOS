import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

// ---- types (mirror lucid_web.py's JSON contract) ----
export type Readiness = {
  coordinator: boolean; comfyui: boolean; ollama: boolean
  can_dream: boolean; why: string[]
}
// ADR-0023/0025 spatial feed-forward: a "tag a moment" annotation pinned to a timestamp on a node's clip,
// and OPTIONALLY a point (x,y normalized 0..1, radius r) saying WHERE on the frame. `hold` is the steering
// primitive — it anchors the next beat on that exact moment (more/less/change steer the direction). With a
// point, the LTX engine localizes the steering to a soft-disc region around it; without one it's frame-wide.
// The backend folds a node's notes into the next beat grown from it.
export type Note = {
  id: string; t: number; tag: 'more' | 'less' | 'hold' | 'change'; text: string
  x?: number; y?: number; r?: number          // optional region (ADR-0025): where on the frame, normalized
  mask?: string                                // optional segmentation-mask ref (ADR-0032): the tapped object
}
export type DreamNode = {
  id: number; parent: number; label: string; prompt: string
  clip: string | null; out_frame: string; length?: number   // chosen segment frame count (@16fps); absent on the opening
  caption?: string | null            // VLM grounding: one-line description of what's on this frame (opening has only this)
  rating?: 'sfw' | 'mature'          // inferred content rating sealed at roll time; drives the render + an honest tag
  notes?: Note[]                     // moment tags steering the next beat (spatial feed-forward)
}
export type Chain = { nodes: DreamNode[] } | null
export type TurnPhase = 'idle' | 'dreaming' | 'done' | 'skipped' | 'refused' | 'error'
export type Turn = { phase: TurnPhase; label: string | null; error: string | null; elapsed?: number }
export type Engine = { active: string; options: string[] }
// ADR-0028: the encrypted private stash — status only on /api/state (the entry list needs an unlock).
// `saved_id` = the stash id THIS dream is currently saved as (so the UI offers "Update" vs "Save").
export type StashStatus = { exists: boolean; unlocked: boolean; saved_id?: string | null }
export type LucidState = {
  session: string; name?: string | null
  readiness: Readiness; chain: Chain; private: boolean; turn: Turn
  engine?: Engine   // ADR-0023: which i2v backend run_beat uses ('wan' | '10eros')
  stash?: StashStatus
}
export type Beat = { label: string; prompt: string }
// ADR-0028: a saved (non-private) dream in the library.
export type LibraryDream = {
  session: string; name: string; premise?: string | null
  created?: number | null; updated?: number | null; frames: number; tip?: number | null
}
// ADR-0028: a dream inside the encrypted stash (names live only in the decrypted index).
export type StashDream = {
  id: string; name: string; premise?: string | null
  created?: number; updated?: number; frames: number
}

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
// `ver` is the node's content ref (its session-scoped `clip` / `out_frame` filename, e.g. "<session>_n0.png"),
// appended as a cache-buster. The URL is otherwise keyed only by `id`, which restarts at 0 EVERY dream — so on
// a dream switch / re-roll the browser would re-serve the previous dream's first segment from the same ?id=0
// URL (no new request is made when a src string is unchanged, even with no-store). The backend ignores `v`.
export const clipUrl = (id: number, ver?: string | null) => `/api/clip?id=${id}` + (ver ? `&v=${encodeURIComponent(ver)}` : '')
export const frameUrl = (id: number, ver?: string | null) => `/api/frame?id=${id}` + (ver ? `&v=${encodeURIComponent(ver)}` : '')

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
export function useBeats(enabled: boolean, session: string, nodeId: number) {
  return useQuery<Beat[]>({
    queryKey: ['beats', session, nodeId],
    // `node` grounds the menu on THAT beat (the tip = continue, an earlier beat = branch a new take)
    queryFn: () => getJSON('/api/beats?node=' + nodeId).then((j) => j.beats ?? []),
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
      // the library + stash lists can move on any of these (a new dream saved, an opened dream
      // switched, a burn/delete) — keep them honest without each call wiring its own invalidation.
      qc.invalidateQueries({ queryKey: ['library'] })
      qc.invalidateQueries({ queryKey: ['stash'] })
      // EVICT (not just invalidate) the held menu: node ids restart at 0 each dream, so a new dream's
      // tip key (['beats', session, 0]) collides with the just-ended dream's cached entry. invalidate
      // leaves the stale set in cache to flash before the refetch lands; removeQueries drops it outright.
      for (const key of resets) qc.removeQueries({ queryKey: key })
    },
  })
}

export const useDream = () => useStateMutation((b: { prompt: string; label: string; length?: number; parent?: number }) => post('/api/dream', b))
export const useStart = () =>
  useStateMutation((b: { private: boolean; name?: string; image_b64?: string; text?: string; consent?: boolean }) =>
    post('/api/start', b), [['beats']])
export const useSetEngine = () => useStateMutation((engine: string) => post('/api/engine', { engine }))
export const useBurn = () => useStateMutation((_: void) => post('/api/burn'), [['beats']])
// delete the CURRENT dream (no arg) or any saved library dream by session (no view switch then).
export const useDelete = () => useStateMutation((session?: string) => post('/api/delete', session ? { session } : {}), [['beats']])

// ---- ADR-0023 moment tags (spatial feed-forward): annotate a node's clip; steers the next beat ----
// Both go through useStateMutation so a settle invalidates ['state'] — the next poll reflects node.notes.
export const useAddNote = () =>
  useStateMutation((b: { node: number; t: number; tag: string; text?: string; x?: number; y?: number; r?: number; mask?: string }) =>
    post('/api/note', b))

// ADR-0032: tap an object -> SAM2 returns its mask. A one-off (not a state mutation; the note is saved
// later via addNote with the returned ref). Fail-open: {ok:false} -> the caller saves a plain point.
export type SegmentResult = { ok: boolean; mask?: string; preview?: string; reason?: string }
export const segment = (b: { node: number; t: number; x: number; y: number }): Promise<SegmentResult> =>
  post('/api/segment', b)
export const useDeleteNote = () =>
  useStateMutation((b: { node: number; id: string }) => post('/api/note/delete', b))

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

// ============================ ADR-0028: save & reopen + private stash ============================
export const thumbUrl = (session: string) => `/api/library/thumb?session=${encodeURIComponent(session)}`

// ---- the saved (non-private) dream library ----
export function useLibrary() {
  return useQuery<LibraryDream[]>({
    queryKey: ['library'],
    queryFn: () => getJSON('/api/library').then((j) => (j.dreams ?? []) as LibraryDream[]),
    staleTime: 2000,
  })
}
// reopen switches the current dream -> evict the prior tip's held menu (node ids restart at 0).
export const useOpenDream = () => useStateMutation((session: string) => post('/api/open', { session }), [['beats']])
// rename the CURRENT open dream (omit session) or any saved library dream by session (ADR-0028 B1b).
export const useRenameDream = () => useStateMutation((b: { name: string; session?: string }) => post('/api/rename', b))

// ---- the encrypted stash ----
export function useStash() {
  return useQuery<{ exists: boolean; unlocked: boolean; dreams?: StashDream[] }>({
    queryKey: ['stash'],
    queryFn: () => getJSON('/api/stash'),
    staleTime: 2000,
  })
}
export const useStashInit = () => useStateMutation((passphrase: string) => post('/api/stash/init', { passphrase }))
export const useStashUnlock = () => useStateMutation((passphrase: string) => post('/api/stash/unlock', { passphrase }))
export const useStashLock = () => useStateMutation((_: void) => post('/api/stash/lock'), [['beats']])
export const useStashSave = () => useStateMutation((b: { name?: string }) => post('/api/stash/save', b))
export const useStashOpen = () => useStateMutation((id: string) => post('/api/stash/open', { id }), [['beats']])
export const useStashRename = () => useStateMutation((b: { id: string; name: string }) => post('/api/stash/rename', b))
export const useStashDelete = () => useStateMutation((id: string) => post('/api/stash/delete', { id }), [['beats']])
// change the stash passphrase (re-keys every sealed dream). Backend is crash-atomic and leaves the
// stash UNLOCKED under the new key (lucid_stash.change_passphrase) -> a settle keeps ['stash'] honest.
export const useStashPassphrase = () => useStateMutation((b: { old: string; new: string }) => post('/api/stash/passphrase', b))

export function fileToB64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1])
    r.onerror = reject
    r.readAsDataURL(f)
  })
}
