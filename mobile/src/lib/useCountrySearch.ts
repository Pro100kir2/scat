import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, asApiError, type CountrySearchResponse } from '@scat/shared'
import { api } from './api'

export type CountryPhase = 'idle' | 'searching' | 'ready' | 'error'

export interface CountrySearchState {
  phase: CountryPhase
  country: string | null
  data: CountrySearchResponse | null
  error: ApiError | null
}

const POLL_MS = 4000
const IDLE: CountrySearchState = { phase: 'idle', country: null, data: null, error: null }

/**
 * Configs that exit in one country. The server answers 202 while its search runs, so the
 * same request is repeated every few seconds until the result is ready. Leaving the screen
 * or starting another search cancels the one in flight.
 */
export function useCountrySearch(pollMs = POLL_MS) {
  const [state, setState] = useState<CountrySearchState>(IDLE)
  const controller = useRef<AbortController | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const cancel = useCallback(() => {
    clearTimeout(timer.current)
    controller.current?.abort()
    controller.current = null
  }, [])

  useEffect(() => cancel, [cancel])

  const search = useCallback((country: string) => {
    cancel()
    const code = country.trim().toUpperCase()
    const abort = new AbortController()
    controller.current = abort
    setState({ phase: 'searching', country: code, data: null, error: null })

    const attempt = async () => {
      try {
        const data = await api.searchCountry(code, abort.signal)
        if (abort.signal.aborted) return
        if (data.ready || data.nodes.length) {
          // Stale results are shown at once while the server refreshes them.
          setState({ phase: 'ready', country: code, data, error: null })
          if (data.ready) return
        } else {
          setState({ phase: 'searching', country: code, data, error: null })
        }
        timer.current = setTimeout(attempt, pollMs)
      } catch (err) {
        const apiErr = asApiError(err)
        if (apiErr.kind === 'aborted' || abort.signal.aborted) return
        setState({ phase: 'error', country: code, data: null, error: apiErr })
      }
    }
    void attempt()
  }, [cancel, pollMs])

  const reset = useCallback(() => {
    cancel()
    setState(IDLE)
  }, [cancel])

  return { state, search, reset }
}

/** What went wrong, in words. */
export function countryErrorText(error: ApiError): { title: string; text: string } {
  if (error.status === 503) {
    return { title: 'Скат занят', text: 'Сейчас идёт слишком много поисков по странам. Попробуйте через минуту.' }
  }
  if (error.kind === 'rate_limit') {
    return { title: 'Скату нужна передышка', text: `Слишком много запросов. Попробуйте через ${error.retryAfter ?? 60} с.` }
  }
  if (error.kind === 'network' || error.kind === 'timeout') {
    return { title: 'Нет связи со Скатом', text: 'Проверьте соединение и попробуйте ещё раз.' }
  }
  return { title: 'Поиск не удался', text: 'Попробуйте ещё раз через некоторое время.' }
}
