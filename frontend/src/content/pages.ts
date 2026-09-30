/**
 * Every page of the site. One source for the router, the per-page <head> (client and
 * prerender), and sitemap.xml (vite.config.ts). Paths end in "/" because each page is
 * prerendered to <path>/index.html.
 */
export interface PageMeta {
  path: string
  title: string
  description: string
  /** false: kept out of the sitemap and marked noindex (forms, per-visitor state). */
  indexable: boolean
}

export const PAGES = {
  home: {
    path: '/',
    title: 'Скат — поиск бесплатных VPN: рабочие VLESS-конфигурации',
    description:
      'Скат (SKAT) каждый день ищет бесплатные VPN: проверяет тысячи открытых VLESS-конфигураций через Xray и оставляет только рабочие и быстрые. Без регистрации.',
    indexable: true,
  },
  providers: {
    path: '/providers/',
    title: 'Провайдеры: доступность VPN из сетей разных операторов | Скат',
    description:
      'Зонды Ската проверяют, доступны ли найденные VPN-серверы из сетей разных провайдеров. Список подключённых провайдеров и состояние зондов.',
    indexable: true,
  },
  addProvider: {
    path: '/providers/add/',
    title: 'Добавить своего провайдера | Скат',
    description: 'Подключите зонд Ската в сети своего провайдера: он проверяет только доступность VPN-серверов.',
    indexable: false,
  },
  install: {
    path: '/providers/install/',
    title: 'Установка зонда Ската: Linux, Raspberry Pi, OpenWrt | Скат',
    description:
      'Что такое зонд Ската, какие данные он отправляет и чего не делает. Требования, поддерживаемые платформы (x86_64, ARM, MIPS, OpenWrt), установка и удаление.',
    indexable: true,
  },
} as const satisfies Record<string, PageMeta>

export type PageName = keyof typeof PAGES
