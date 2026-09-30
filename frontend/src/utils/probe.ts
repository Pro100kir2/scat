import type { DiagnosticCheck, DiagnosticIssue } from '@/types/api'

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
