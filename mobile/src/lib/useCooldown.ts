import { useCallback, useEffect, useRef, useState } from 'react'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { cappedUntil, remainingSeconds } from '@scat/shared'

/**
 * A countdown that survives app restarts: the end time is kept in storage, capped by
 * `maxSeconds` so a stale value can't lock the button for long. Storage problems only mean
 * the countdown is not remembered.
 */
export function useCooldown(key: string, maxSeconds: number) {
  const [until, setUntil] = useState(0)
  const [remaining, setRemaining] = useState(0)
  const timer = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  useEffect(() => {
    let alive = true
    AsyncStorage.getItem(key)
      .then((stored) => {
        if (alive) setUntil(cappedUntil(stored, maxSeconds))
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [key, maxSeconds])

  useEffect(() => {
    clearInterval(timer.current)
    const tick = () => {
      const left = remainingSeconds(until)
      setRemaining(left)
      if (left <= 0) {
        clearInterval(timer.current)
        if (until) AsyncStorage.removeItem(key).catch(() => undefined)
      }
    }
    tick()
    if (remainingSeconds(until) > 0) timer.current = setInterval(tick, 1000)
    return () => clearInterval(timer.current)
  }, [until, key])

  const start = useCallback((seconds: number) => {
    const end = Date.now() + Math.min(seconds, maxSeconds) * 1000
    setUntil(end)
    AsyncStorage.setItem(key, String(end)).catch(() => undefined)
  }, [key, maxSeconds])

  return { remaining, start }
}
