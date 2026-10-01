import { View } from 'react-native'
import { WithSkiaWeb } from '@shopify/react-native-skia/lib/module/web'
import type { OceanProps } from './types'

/**
 * Browser preview only (`npm run web`): Skia runs on CanvasKit (WebAssembly), which has to
 * be loaded before any Skia code is imported. The file is served from public/canvaskit.wasm
 * (copied by `setup-skia-web` on install).
 */
export function OceanView(props: OceanProps) {
  return (
    <WithSkiaWeb
      opts={{ locateFile: (file: string) => `/${file}` }}
      getComponent={() => import('./OceanCanvas')}
      fallback={<View style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, backgroundColor: '#0b2533' }} />}
      componentProps={props}
    />
  )
}
