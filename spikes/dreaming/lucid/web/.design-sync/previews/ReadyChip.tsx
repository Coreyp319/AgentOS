import { ReadyChip } from 'web'

// Healthy — one calm word + a green dot by the wordmark.
export const Ready = () => (
  <ReadyChip r={{ coordinator: true, comfyui: true, ollama: true, can_dream: true, why: [] }} />
)

// Paused — a piece is down, so the chip reads "paused" with an amber dot.
export const Paused = () => (
  <ReadyChip
    r={{ coordinator: true, comfyui: false, ollama: true, can_dream: false, why: ['the video generator isn’t responding'] }}
  />
)
