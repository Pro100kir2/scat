import { useEffect, useRef, useState } from 'react'
import { AccessibilityInfo, type StyleProp, type ViewStyle } from 'react-native'
import { copyText } from '@/lib/links'
import { Button } from './Button'

interface Props {
  text: string
  label?: string
  copiedLabel?: string
  variant?: 'default' | 'primary' | 'quiet'
  small?: boolean
  style?: StyleProp<ViewStyle>
  testID?: string
}

/** Copy with a short-lived confirmation on the button itself (and for screen readers). */
export function CopyButton({ text, label = 'Копировать', copiedLabel = 'Скопировано', variant, small, style, testID }: Props) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  async function onPress() {
    clearTimeout(timer.current)
    const ok = await copyText(text)
    setState(ok ? 'copied' : 'failed')
    AccessibilityInfo.announceForAccessibility(ok ? copiedLabel : 'Не удалось скопировать')
    timer.current = setTimeout(() => setState('idle'), 1800)
  }

  return (
    <Button
      testID={testID}
      label={state === 'copied' ? copiedLabel : state === 'failed' ? 'Не удалось' : label}
      icon={state === 'copied' ? 'check' : 'copy'}
      onPress={onPress}
      variant={variant}
      small={small}
      style={style}
    />
  )
}
