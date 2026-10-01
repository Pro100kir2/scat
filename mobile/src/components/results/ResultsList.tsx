import { useEffect, useState } from 'react'
import { Animated, StyleSheet, View } from 'react-native'
import type { PearlNode } from '@scat/shared'
import { PearlCard } from './PearlCard'
import { colors, radius } from '@/theme'

/** The catch, best first. */
export function ResultsList({ nodes, onOpen }: { nodes: PearlNode[]; onOpen: (node: PearlNode) => void }) {
  return (
    <View style={{ gap: 10 }}>
      {nodes.map((node) => <PearlCard key={node.id} node={node} onOpen={onOpen} />)}
    </View>
  )
}

/** Placeholder cards while the first results load. */
export function ResultsSkeleton({ still = false }: { still?: boolean }) {
  const [pulse] = useState(() => new Animated.Value(0.5))
  useEffect(() => {
    if (still) return
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 1, duration: 800, useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 0.5, duration: 800, useNativeDriver: true }),
    ]))
    loop.start()
    return () => loop.stop()
  }, [pulse, still])
  return (
    <View style={{ gap: 10 }} accessibilityLabel="Загружаем результаты" accessibilityRole="progressbar" testID="results-skeleton">
      {[0, 1, 2].map((i) => <Animated.View key={i} style={[styles.skeleton, { opacity: pulse }]} />)}
    </View>
  )
}

const styles = StyleSheet.create({
  skeleton: { height: 118, borderRadius: radius.md, backgroundColor: colors.sea, borderWidth: 1, borderColor: colors.line },
})
