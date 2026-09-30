import type { DiagnosticCheck, DiagnosticIssue, EnrollmentStatus } from '@/types/api'

/** Russian wording for the installer's failed requirements (the server's codes). */
const REASONS: Record<string, string> = {
  os_unsupported: 'Нужна операционная система Linux.',
  kernel_too_old: 'Слишком старое ядро Linux: нужно 3.2 или новее.',
  arch_unsupported: 'Этот тип процессора пока не поддерживается.',
  arch_big_endian_arm: 'ARM с обратным порядком байт (big-endian) не поддерживается.',
  arch_endian_unknown: 'Не удалось определить порядок байт процессора MIPS.',
  binary_unavailable: 'Сборка зонда для этой платформы ещё не опубликована.',
  ram_insufficient: 'Недостаточно оперативной памяти: нужно не меньше 48 МБ.',
  ram_available_insufficient: 'Слишком мало свободной памяти: нужно не меньше 16 МБ.',
  storage_insufficient: 'Не хватает места для файла зонда.',
  no_https_client: 'Нет curl или wget с поддержкой HTTPS.',
  not_root: 'Нужны права root: запустите команду через sudo.',
  no_ipv4: 'Нет выхода в интернет по IPv4.',
}

const WARNINGS: Record<string, string> = {
  no_autostart: 'Не найдена система автозапуска: после перезагрузки зонд нужно будет запустить вручную.',
  ram_unknown: 'Не удалось узнать объём памяти.',
}

const ALTERNATIVES: Record<string, string> = {
  other_device:
    'Доступность зависит от сети, а не от устройства: поставьте зонд на любое другое устройство с Linux ' +
    'за тем же подключением (роутер с OpenWrt, Raspberry Pi, компьютер или виртуальную машину).',
  openwrt_storage: 'OpenWrt: освободите место на /overlay (удалите ненужные пакеты) или подключите extroot на USB-накопителе.',
  docker_host: 'На устройстве есть Docker, но зонду нужен прямой доступ к сети хоста: ставьте его на сам хост.',
}

export function issueText(issue: DiagnosticIssue, kind: 'reason' | 'warning' | 'alternative' = 'reason'): string {
  const text = { reason: REASONS, warning: WARNINGS, alternative: ALTERNATIVES }[kind]
  return text[issue.code] ?? issue.message
}

const CHECK_LABELS: Record<string, string> = {
  os: 'Система',
  kernel: 'Ядро Linux',
  arch: 'Процессор',
  mem_total: 'Память',
  mem_available: 'Свободная память',
  storage_free: 'Свободное место',
  https_client: 'HTTPS-клиент',
  root: 'Права root',
  ipv4: 'Интернет (IPv4)',
  init: 'Автозапуск',
}

export const checkLabel = (check: DiagnosticCheck) => CHECK_LABELS[check.name] ?? check.name

/** Platforms the probe is built for (mirrors app/probes/profiles.py). */
export const PLATFORMS: { name: string; examples: string }[] = [
  { name: 'Linux x86_64', examples: 'ПК, мини-ПК, серверы, виртуальные машины' },
  { name: 'Linux x86 (32-bit)', examples: 'старые ПК и тонкие клиенты' },
  { name: 'Linux ARM64 (aarch64)', examples: 'Raspberry Pi 3/4/5 с 64-битной системой, Orange Pi, роутеры на Filogic' },
  { name: 'Linux ARMv7 / ARMv6 / ARMv5', examples: 'Raspberry Pi с 32-битной системой, старые роутеры и NAS' },
  { name: 'Linux MIPS little-endian (mipsel)', examples: 'роутеры на MediaTek MT7621/MT7628 (Xiaomi, Keenetic с OpenWrt, Netgear)' },
  { name: 'Linux MIPS big-endian', examples: 'роутеры на Qualcomm Atheros (ath79): TP-Link, GL.iNet' },
  { name: 'Linux MIPS64', examples: 'роутеры на Cavium Octeon' },
  { name: 'Linux RISC-V 64', examples: 'VisionFive, Milk-V' },
]

// --- verification -----------------------------------------------------------------------

/** Why automatic verification failed (server codes) and what the person can do about it. */
export const VERIFY_REASONS: Record<string, { reason: string; todo: string[] }> = {
  never_connected: {
    reason: 'Зонд зарегистрировался, но больше не выходил на связь с сервером.',
    todo: [
      'Проверьте, что служба зонда запущена: systemctl status scat-probe (на OpenWrt: /etc/init.d/scat-probe status).',
      'Если служба остановлена, запустите команду установки ещё раз.',
    ],
  },
  jobs_not_fetched: {
    reason: 'Зонд на связи, но не забрал проверочное задание.',
    todo: ['Перезапустите службу зонда.', 'Если не поможет, запустите команду установки ещё раз: она обновит зонд.'],
  },
  no_result: {
    reason: 'Зонд получил проверочное задание, но не прислал результат.',
    todo: ['Проверьте, что подключение устройства к интернету стабильно, и перезапустите службу зонда.'],
  },
  api_unreachable: {
    reason: 'Из вашей сети зонд не смог открыть проверочный адрес Ската.',
    todo: [
      'Проверьте, открывается ли адрес API Ската с этого устройства (например, через curl).',
      'Если его блокирует фаервол роутера или провайдер, разрешите доступ к нему.',
    ],
  },
  unexpected_response: {
    reason: 'Проверочный адрес ответил не так, как ожидалось: похоже, соединение перехватывает прокси или страница входа провайдера.',
    todo: [
      'Отключите для этого устройства прокси, родительский контроль или фильтрацию трафика.',
      'Или поставьте зонд на другое устройство в той же сети.',
    ],
  },
  nonce_not_received: {
    reason: 'Зонд сообщил, что проверка прошла, но сервер так и не получил его запрос.',
    todo: ['Установите зонд официальной командой с этой страницы. Если он уже установлен так, напишите нам.'],
  },
  probe_error: {
    reason: 'Зонд не смог выполнить проверку на этом устройстве.',
    todo: ['Запустите команду установки ещё раз: она поставит свежую версию зонда.'],
  },
  server_misconfigured: {
    reason: 'Проверка временно недоступна на стороне сервера.',
    todo: ['Делать ничего не нужно: мы уже знаем об этом и повторим проверку.'],
  },
  spot_check_failed: {
    reason: 'Зонд прислал результат проверки, которую он не выполнял.',
    todo: ['Переустановите зонд официальной командой с этой страницы.'],
  },
}

const UNKNOWN_REASON = {
  reason: 'Проверка не прошла по неизвестной причине.',
  todo: ['Запустите команду установки ещё раз. Если не поможет, напишите нам.'],
}

export type Tone = 'wait' | 'ok' | 'fail'

export interface VerificationView {
  tone: Tone
  icon: string
  title: string
  lines: string[]
  reason?: string
  todo?: string[]
  /** Whether the page should keep polling. */
  final: boolean
}

/**
 * What the installation page says once the probe exists. Never shows raw states
 * ("pending", "verification_failed"): those go in the details block.
 */
export function verificationView(status: EnrollmentStatus): VerificationView | null {
  const v = status.verification
  const online = v?.online ?? false
  switch (status.state) {
    case 'registered':
      return {
        tone: 'wait', icon: '🟡', title: 'Зонд зарегистрирован', final: false,
        lines: ['Регистрация прошла успешно, зонд подключается к серверу.', 'Сейчас начнётся автоматическая проверка…'],
      }
    case 'verifying':
      return {
        tone: 'wait', icon: '🟡', title: 'Проверяем зонд', final: false,
        lines: online
          ? ['Зонд на связи.', 'Проверяем, что он правильно выполняет задания… Обычно это занимает меньше минуты.']
          : ['Зонд сейчас не на связи. Проверка продолжится, как только он подключится.'],
      }
    case 'verified':
      return {
        tone: 'ok', icon: '🟢', title: 'Зонд проверен', final: online,
        lines: online
          ? ['Всё готово.', 'Это устройство теперь участвует в проверке VLESS-конфигураций.']
          : ['Проверка пройдена, но сейчас зонд не на связи. Как только он подключится, снова начнёт получать задания.'],
      }
    case 'verification_failed': {
      const known = VERIFY_REASONS[v?.reason ?? ''] ?? UNKNOWN_REASON
      return {
        tone: 'fail', icon: '🔴', title: 'Проверка зонда не пройдена', final: !v?.will_retry,
        lines: [
          'Зонд зарегистрирован, но не смог пройти проверку.',
          ...(v?.will_retry ? ['Мы автоматически повторим проверку примерно через 30 минут.'] : []),
        ],
        reason: known.reason,
        todo: known.todo,
      }
    }
    case 'disabled':
      return {
        tone: 'fail', icon: '⚪', title: 'Зонд отключён', final: true,
        lines: ['Администратор отключил этот зонд. Если это ошибка, напишите нам.'],
      }
    default:
      return null
  }
}
