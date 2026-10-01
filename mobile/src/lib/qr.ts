import { encode } from 'uqr'

/**
 * QR code for a link, generated on the device (uqr, the same encoder as the site): the
 * link is never sent anywhere. Returns one SVG path for the dark modules and the size of
 * the grid including the quiet border.
 */
export function qrPath(text: string, border = 2): { d: string; size: number } {
  const { data, size } = encode(text, { ecc: 'M', border })
  let d = ''
  for (let y = 0; y < size; y++) {
    const row = data[y]!
    for (let x = 0; x < size; x++) {
      if (!row[x]) continue
      // Merge horizontal runs so the path stays short.
      let run = 1
      while (x + run < size && row[x + run]) run++
      d += `M${x} ${y}h${run}v1h-${run}z`
      x += run - 1
    }
  }
  return { d, size }
}
