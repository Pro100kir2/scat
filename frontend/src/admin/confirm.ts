import { reactive } from 'vue'

/** One confirmation dialog for the whole panel; `confirmAction` resolves with the choice. */
export const dialog = reactive({
  open: false,
  title: '',
  body: '',
  confirm: 'Confirm',
  danger: false,
  resolve: null as ((ok: boolean) => void) | null,
})

export function confirmAction(opts: { title: string; body: string; confirm?: string; danger?: boolean }): Promise<boolean> {
  dialog.resolve?.(false)
  return new Promise((resolve) => {
    Object.assign(dialog, { open: true, title: opts.title, body: opts.body, confirm: opts.confirm ?? 'Confirm',
      danger: opts.danger ?? false, resolve })
  })
}

export function settle(ok: boolean): void {
  const resolve = dialog.resolve
  Object.assign(dialog, { open: false, resolve: null })
  resolve?.(ok)
}
