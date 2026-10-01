import { Linking, Share } from 'react-native'
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import * as Clipboard from 'expo-clipboard'
import { ConfigSheet } from '@/components/results/ConfigSheet'
import { PearlCard } from '@/components/results/PearlCard'
import { LINK, node } from '@/test-utils/fixtures'

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}))

afterEach(() => jest.restoreAllMocks())

describe('configuration sheet', () => {
  it('shows the link exactly as the server sent it', async () => {
    await render(<ConfigSheet node={node(1)} onClose={() => {}} />)
    expect(screen.getByTestId('sheet-link')).toHaveTextContent(LINK, { exact: true })
    expect(screen.getByText('Германия')).toBeTruthy()
    expect(screen.getByText('3/4')).toBeTruthy() // confirmed by providers
    expect(screen.getByText('МТС')).toBeTruthy()
  })

  it('copies and shares the untouched link', async () => {
    const share = jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' })
    await render(<ConfigSheet node={node(1)} onClose={() => {}} />)
    await fireEvent.press(screen.getByTestId('sheet-copy'))
    await waitFor(() => expect(Clipboard.setStringAsync).toHaveBeenCalledWith(LINK))
    expect(await screen.findByText('Скопировано')).toBeTruthy()
    await fireEvent.press(screen.getByTestId('sheet-share'))
    await waitFor(() => expect(share).toHaveBeenCalled())
    expect(share.mock.calls[0]![0]).toMatchObject({ message: LINK })
  })

  it('opens the link in a VPN client, and explains when there is none', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockRejectedValue(new Error('no handler'))
    await render(<ConfigSheet node={node(1)} onClose={() => {}} />)
    await fireEvent.press(screen.getByTestId('sheet-open'))
    await waitFor(() => expect(open).toHaveBeenCalledWith(LINK))
    expect(await screen.findByText(/нет приложения, которое открывает ссылки vless/)).toBeTruthy()
  })

  it('shows a QR code on request', async () => {
    await render(<ConfigSheet node={node(1)} onClose={() => {}} />)
    expect(screen.queryByTestId('qr-code')).toBeNull()
    await fireEvent.press(screen.getByTestId('sheet-qr'))
    expect(screen.getByTestId('qr-code')).toBeTruthy()
    expect(screen.getByText('Скрыть QR-код')).toBeTruthy()
  })

  it('renders nothing without a configuration', async () => {
    await render(<ConfigSheet node={null} onClose={() => {}} />)
    expect(screen.queryByTestId('sheet-link')).toBeNull()
  })
})

describe('configuration card', () => {
  it('summarises the config for screen readers and opens details', async () => {
    const onOpen = jest.fn()
    await render(<PearlCard node={node(2)} onOpen={onOpen} />)
    const card = screen.getByTestId('pearl-2')
    expect(card.props.accessibilityLabel).toContain('Место 2')
    expect(card.props.accessibilityLabel).toContain('Германия')
    await fireEvent.press(card)
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ rank: 2 }))
  })

  it('copies straight from the card', async () => {
    const n = node(3)
    await render(<PearlCard node={n} onOpen={() => {}} />)
    await fireEvent.press(screen.getByTestId('copy-3'))
    await waitFor(() => expect(Clipboard.setStringAsync).toHaveBeenCalledWith(n.link))
  })
})
