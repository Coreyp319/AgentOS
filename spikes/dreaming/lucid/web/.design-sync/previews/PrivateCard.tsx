import { PrivateCard } from 'web'

const noop = () => {}

// No stash yet — offers to create the encrypted private stash before saving.
export const NoStash = () => <PrivateCard stash={{ exists: false, unlocked: false }} onGoStash={noop} />

// Stash exists but is locked — sends the user to unlock it first.
export const StashLocked = () => <PrivateCard stash={{ exists: true, unlocked: false }} onGoStash={noop} />

// Stash unlocked — offers to seal this private dream into it.
export const Unlocked = () => <PrivateCard stash={{ exists: true, unlocked: true }} onGoStash={noop} />

// Already sealed once — the button reads "Update in private stash".
export const AlreadySaved = () => (
  <PrivateCard stash={{ exists: true, unlocked: true, saved_id: 's-letter' }} onGoStash={noop} />
)
