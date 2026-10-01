import { View } from 'react-native'
import { router } from 'expo-router'
import { Button } from '@/components/ui/Button'
import { StateNote } from '@/components/ui/StateNote'
import { colors } from '@/theme'

/** A link the app does not know (e.g. an old scat:// link). */
export default function NotFound() {
  return (
    <View style={{ flex: 1, backgroundColor: colors.abyss, justifyContent: 'center' }}>
      <StateNote title="Здесь пусто" text="Такой страницы в приложении нет.">
        <Button small label="К улову" onPress={() => router.replace('/')} />
      </StateNote>
    </View>
  )
}
