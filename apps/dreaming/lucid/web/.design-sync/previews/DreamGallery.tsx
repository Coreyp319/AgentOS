import { DreamGallery, LibraryEmptyEnv } from 'web'

const noop = () => {}

// The saved-dream library, populated — a responsive grid of dream tiles with
// names, frame counts, ages, and Rename/Remove controls. (Thumbnails resolve to
// the live backend; with none, tiles show their designed blank-thumb fallback.)
export const Populated = () => <DreamGallery onOpened={noop} />

// First run / nothing saved yet — the calm empty state.
export const Empty = () => (
  <LibraryEmptyEnv>
    <DreamGallery onOpened={noop} />
  </LibraryEmptyEnv>
)
