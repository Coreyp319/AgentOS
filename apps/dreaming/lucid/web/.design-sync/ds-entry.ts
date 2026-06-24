// Design-system entry for the AgentOS Lucid sync (cfg.entry).
//
// The app has no library build — its only entry, src/main.tsx, runs
// ReactDOM.createRoot(...).render(<App/>) at module load, which throws when
// bundled with no #root and pollutes the bundle with app-bootstrap code. So
// instead of letting the converter synthesize an entry from every src file,
// this barrel re-exports ONLY the shippable design-system components. Keep it
// in sync with cfg.componentSrcMap when adding/removing components.
export {
  Nav,
  EngineToggle,
  PreviewToggle,
  ReadyChip,
  ReadinessCard,
  PrivateCard,
  LibraryCard,
} from '../src/components'
export { StashPanel, DreamGallery } from '../src/Library'
