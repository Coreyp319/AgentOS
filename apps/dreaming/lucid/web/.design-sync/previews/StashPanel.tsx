import { StashPanel, StashCreateEnv, StashLockedEnv } from 'web'

const noop = () => {}

// Unlocked with sealed dreams — the hero state (uses the default seeded stash).
export const UnlockedGrid = () => <StashPanel onOpened={noop} />

// First run — create the encrypted stash (passphrase + the "no recovery" warning).
export const CreateStash = () => (
  <StashCreateEnv>
    <StashPanel onOpened={noop} />
  </StashCreateEnv>
)

// Stash exists but locked — the unlock prompt.
export const Locked = () => (
  <StashLockedEnv>
    <StashPanel onOpened={noop} />
  </StashLockedEnv>
)
