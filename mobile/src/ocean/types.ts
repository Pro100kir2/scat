import type { Rect, SceneMode } from '@scat/shared/ocean'

export interface OceanProps {
  mode: SceneMode
  /** Discoveries so far in the current dive; increments become pearls. */
  found: number
  /** A still, composed scene instead of animation (system "reduce motion"). */
  reducedMotion: boolean
  /** Where the headline sits, so the manta keeps out from behind it. */
  safeZone: Rect | null
  /** Stop animating (screen not focused, scene scrolled away): saves battery. */
  paused: boolean
  onReveal: (total: number) => void
  onDrained: () => void
  /** A pearl was tapped: the rank of the result it stands for. */
  onPearl: (rank: number) => void
}

/** Frames per second of the scene. 30 keeps the manta fluid at half the cost of 60. */
export const OCEAN_FPS = 30
