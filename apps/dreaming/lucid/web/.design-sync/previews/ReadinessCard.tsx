import { ReadinessCard } from 'web'

// All three pieces up — three lit dots, no banner. The calm baseline.
export const AllReady = () => (
  <ReadinessCard r={{ coordinator: true, comfyui: true, ollama: true, can_dream: true, why: [] }} />
)

// Video generator down — the card explains why dreaming is paused.
export const GeneratorDown = () => (
  <ReadinessCard
    r={{ coordinator: true, comfyui: false, ollama: true, can_dream: false, why: ['the video generator isn’t responding'] }}
  />
)

// Graphics turn-taking unavailable — a different missing piece, different banner.
export const CoordinatorDown = () => (
  <ReadinessCard
    r={{ coordinator: false, comfyui: true, ollama: true, can_dream: false, why: ['graphics turn-taking is unavailable'] }}
  />
)
