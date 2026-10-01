import { Tabs } from 'expo-router'
import { Icon, type IconName } from '@/components/ui/Icon'
import { colors, fonts } from '@/theme'

const tab = (title: string, icon: IconName) => ({
  title,
  tabBarIcon: ({ focused }: { focused: boolean }) => <Icon name={icon} size={22} color={focused ? colors.pearl : colors.text3} />,
})

/** Four places, one tap each: the catch, a country, the subscription, the providers. */
export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.pearl,
        tabBarInactiveTintColor: colors.text3,
        tabBarStyle: { backgroundColor: colors.deep, borderTopColor: colors.line },
        tabBarLabelStyle: { fontFamily: fonts.uiMedium, fontSize: 11, lineHeight: 15 },
        tabBarItemStyle: { paddingTop: 4 },
        sceneStyle: { backgroundColor: colors.abyss },
      }}
    >
      <Tabs.Screen name="index" options={tab('Улов', 'pearl')} />
      <Tabs.Screen name="countries" options={tab('Страны', 'globe')} />
      <Tabs.Screen name="subscription" options={tab('Подписка', 'link')} />
      <Tabs.Screen name="providers" options={tab('Провайдеры', 'signal')} />
    </Tabs>
  )
}
