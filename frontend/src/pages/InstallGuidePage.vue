<script setup lang="ts">
import { computed } from 'vue'
import CommandBlock from '@/components/providers/CommandBlock.vue'
import { env } from '@/config/env'
import { PLATFORMS } from '@/utils/probe'

// No window here: the page is prerendered and must hydrate to the same text.
const api = computed(() => env.apiUrl || 'https://api.scat-search.ru')
const script = computed(() => `${api.value}/api/probes/install.sh`)

const systemdUnit = computed(() => `[Unit]
Description=Скат provider probe
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/scat-probe run --config /etc/scat-probe/probe.json
Restart=always
RestartSec=10
CapabilityBoundingSet=
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/etc/scat-probe
MemoryMax=64M

[Install]
WantedBy=multi-user.target`)

const procdScript = `#!/bin/sh /etc/rc.common
USE_PROCD=1
START=95

start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/scat-probe run --config /etc/scat-probe/probe.json
    procd_set_param respawn 3600 10 0
    procd_close_instance
}`
</script>

<template>
  <main id="top" class="page container guide">
    <header>
      <p class="eyebrow">Зонд Ската</p>
      <h1 class="page-title">Установка <em>зонда</em></h1>
      <p class="page-lead">
        Всё о маленькой программе, которая помогает Скату понять, открываются ли найденные VPN-серверы из сети
        вашего провайдера: что она делает, чего не делает никогда и как её поставить или удалить.
      </p>
      <div class="page-actions">
        <RouterLink class="btn btn--primary" to="/providers/add/">Добавить провайдера и получить токен</RouterLink>
      </div>
    </header>

    <nav class="toc" aria-label="Содержание">
      <a href="#what">Что это</a>
      <a href="#data">Какие данные</a>
      <a href="#never">Чего не делает</a>
      <a href="#requirements">Требования</a>
      <a href="#platforms">Платформы</a>
      <a href="#interactive">Установка</a>
      <a href="#manual">Вручную</a>
      <a href="#openwrt">OpenWrt</a>
      <a href="#remove">Удаление</a>
      <a href="#trouble">Если не работает</a>
    </nav>

    <section id="what" class="part">
      <h2 class="section-title">Что такое <em>зонд</em> и зачем он нужен</h2>
      <p>
        Скат каждый день находит рабочие VLESS-конфигурации и проверяет их со своего сервера. Но провайдеры
        блокируют по-разному: сервер, который открывается у одного оператора, у другого может не отвечать.
        Узнать это можно только изнутри сети самого провайдера.
      </p>
      <p>
        Зонд живёт на устройстве в вашей сети и по заданию Ската проверяет, можно ли достучаться до адреса и
        порта сервера: установить TCP-соединение, пройти TLS-рукопожатие, получить HTTP-ответ. Результаты
        появляются в карточках лучших конфигураций в разделе «Доступность в сетях».
      </p>
    </section>

    <section id="data" class="part">
      <h2 class="section-title">Какие данные <em>отправляет</em></h2>
      <ul>
        <li>При установке: сведения об устройстве для проверки совместимости (система, ядро, архитектура и модель процессора, объём памяти и места, система автозапуска, есть ли curl, wget, IPv4 и IPv6).</li>
        <li>Раз в 30 секунд: сигнал «на связи» с версией зонда и временем работы.</li>
        <li>По каждому заданию: получилось ли соединение, за сколько миллисекунд, код ошибки или HTTP-статус. Содержимое ответов серверов не передаётся.</li>
        <li>Сервер видит IP-адрес, с которого приходят запросы. Он нигде не публикуется и нужен только для защиты от злоупотреблений.</li>
      </ul>
    </section>

    <section id="never" class="part">
      <h2 class="section-title">Чего зонд <em>не делает</em></h2>
      <ul class="never">
        <li><strong>Не запускает VLESS и Xray</strong> и не является VPN-клиентом: через него не идёт ничей трафик.</li>
        <li><strong>Не открывает входящие порты.</strong> Все соединения исходящие: зонд сам обращается к серверу Ската по HTTPS.</li>
        <li><strong>Не выполняет команды.</strong> Задание может быть только одним из трёх типов проверки со строгой схемой: TCP-соединение, TLS-рукопожатие, HTTP GET. Любое другое задание отбрасывается.</li>
        <li><strong>Не заглядывает в вашу сеть.</strong> Адреса в локальной сети, самого устройства, облачных метаданных и другие служебные диапазоны отклоняются и на сервере, и в самом зонде.</li>
        <li><strong>Не смотрит ваш трафик</strong> и не собирает сведения о других устройствах.</li>
      </ul>
    </section>

    <section id="requirements" class="part">
      <h2 class="section-title">Требования</h2>
      <ul>
        <li>Linux с ядром 3.2 или новее (включая OpenWrt).</li>
        <li>От 48 МБ оперативной памяти, из них 16 МБ свободно. Зонду обычно хватает 10 МБ.</li>
        <li>Около 8 МБ свободного места для файла зонда (сборка для MIPS весит 6,9 МБ).</li>
        <li>Права root, чтобы установить службу автозапуска.</li>
        <li>curl или wget с поддержкой HTTPS и выход в интернет по IPv4.</li>
      </ul>
      <p class="muted">Подходит любое устройство за тем же подключением: результат зависит от сети, а не от устройства.</p>
    </section>

    <section id="platforms" class="part">
      <h2 class="section-title">Поддерживаемые <em>платформы</em></h2>
      <table class="platforms">
        <thead>
          <tr><th scope="col">Платформа</th><th scope="col">Например</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in PLATFORMS" :key="p.name">
            <td>{{ p.name }}</td>
            <td>{{ p.examples }}</td>
          </tr>
        </tbody>
      </table>
      <p class="muted">
        Нужную сборку выбирает сервер по результатам диагностики. Например, роутер на MediaTek MT7621 с OpenWrt
        получает сборку MIPS little-endian: команда <code class="mono">uname -m</code> там показывает просто «mips»,
        поэтому скрипт смотрит ещё на порядок байт и архитектуру OpenWrt.
      </p>
    </section>

    <section id="interactive" class="part">
      <h2 class="section-title">Интерактивная <em>установка</em></h2>
      <p>
        Получите токен на странице <RouterLink to="/providers/add/">«Добавить провайдера»</RouterLink>: там же
        будет готовая команда и видно, как идёт установка. Команда выглядит так:
      </p>
      <CommandBlock :command="`curl -fsSL ${script} | sh -s -- --token ВАШ_ТОКЕН`" />
      <ol>
        <li><strong>Диагностика.</strong> Скрипт собирает сведения об устройстве и отправляет их на сервер по HTTPS.</li>
        <li><strong>Решение сервера.</strong> SUPPORTED или UNSUPPORTED. Если устройство не подходит, ничего не скачивается и не устанавливается, а скрипт объясняет, какие требования не выполнены.</li>
        <li><strong>Установка.</strong> Скачивается сборка для вашей платформы, проверяется её SHA-256, пробный запуск; затем установка, регистрация зонда (он получает свой ID и секрет), служба автозапуска, запуск и проверка первого сигнала.</li>
      </ol>
      <p class="muted">Только проверить устройство, ничего не устанавливая: добавьте <code class="mono">--diagnose-only</code>.</p>
    </section>

    <section id="manual" class="part">
      <h2 class="section-title">Ручная <em>установка</em></h2>
      <p>
        Список сборок с контрольными суммами: <a :href="`${api}/api/probes/releases`" rel="nofollow">{{ api }}/api/probes/releases</a>.
        На странице «Добавить провайдера» есть готовые команды под вашу платформу и токен.
      </p>
      <ol>
        <li>Скачайте сборку своей платформы в <code class="mono">/usr/local/bin/scat-probe</code> (на OpenWrt в <code class="mono">/usr/bin</code>).</li>
        <li>Сверьте SHA-256: <code class="mono">sha256sum /usr/local/bin/scat-probe</code>. Не совпала — не запускайте.</li>
        <li>Зарегистрируйте: <code class="mono">scat-probe enroll --api {{ api }} --token ВАШ_ТОКЕН</code>.</li>
        <li>Добавьте службу и запустите её.</li>
      </ol>
      <p class="label">systemd: <code class="mono">/etc/systemd/system/scat-probe.service</code>, затем <code class="mono">systemctl enable --now scat-probe</code></p>
      <pre class="code mono">{{ systemdUnit }}</pre>
      <p class="label">OpenWrt (procd): <code class="mono">/etc/init.d/scat-probe</code>, затем <code class="mono">chmod +x</code>, <code class="mono">enable</code> и <code class="mono">start</code></p>
      <pre class="code mono">{{ procdScript }}</pre>
    </section>

    <section id="openwrt" class="part">
      <h2 class="section-title">OpenWrt</h2>
      <ul>
        <li>Если нет HTTPS в wget: <code class="mono">opkg update && opkg install uclient-fetch libustream-mbedtls ca-bundle</code>.</li>
        <li>Команда для OpenWrt: <code class="mono">wget -qO- {{ script }} | sh -s -- --token ВАШ_ТОКЕН</code>.</li>
        <li>Зонд ставится в <code class="mono">/usr/bin</code> на раздел overlay и запускается через procd. Журнал: <code class="mono">logread -e scat-probe</code>.</li>
        <li>Если на overlay мало места, освободите его (удалите ненужные пакеты) или подключите extroot на USB-накопителе.</li>
      </ul>
    </section>

    <section id="remove" class="part">
      <h2 class="section-title">Удаление</h2>
      <p>Остановит и удалит зонд, службу и учётные данные. На сайте зонд станет «не на связи».</p>
      <CommandBlock :command="`curl -fsSL ${script} | sh -s -- --uninstall`" />
    </section>

    <section id="trouble" class="part">
      <h2 class="section-title">Если <em>не работает</em></h2>
      <ul>
        <li><strong>«could not reach»</strong>: проверьте, что на устройстве работает HTTPS и верное время (без него не проходит проверка сертификатов).</li>
        <li><strong>«token expired / already used»</strong>: токен одноразовый и действует 24 часа. Создайте новый на странице «Добавить провайдера».</li>
        <li><strong>Зонд установлен, но не на связи</strong>: журнал службы (<code class="mono">journalctl -u scat-probe</code> или <code class="mono">logread -e scat-probe</code>) и команда <code class="mono">scat-probe check</code>.</li>
        <li><strong>«credentials rejected»</strong>: зонд отключён или его учётные данные удалены. Удалите его и поставьте заново с новым токеном.</li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.toc {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 40px;
}

.toc a {
  padding: 6px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--text-2);
  font-size: 13.5px;
  text-decoration: none;
}

.toc a:hover {
  border-color: var(--line-strong);
  color: var(--text);
}

.part {
  max-width: 820px;
  margin-top: clamp(48px, 7vw, 80px);
  color: var(--text-2);
}

.part .section-title {
  margin-bottom: 18px;
  color: var(--text);
}

.part p + p,
.part p + ul,
.part ul + p,
.part p + ol,
.part ol + p {
  margin-top: 12px;
}

.part ul,
.part ol {
  display: grid;
  gap: 8px;
  margin: 12px 0 0;
  padding-left: 22px;
}

.part strong {
  color: var(--text);
}

.part a {
  color: var(--aqua);
}

.part code {
  font-size: 0.86em;
  overflow-wrap: anywhere;
}

.never li::marker {
  content: '✕  ';
  color: var(--coral);
}

.muted {
  font-size: 14.5px;
  color: var(--text-3);
}

.label {
  margin-top: 20px;
  font-size: 14px;
}

.code {
  margin: 8px 0 0;
  padding: 14px 16px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: rgba(3, 12, 18, 0.6);
  color: var(--text);
  font-size: 12.5px;
  line-height: 1.6;
}

.platforms {
  width: 100%;
  border-collapse: collapse;
  font-size: 14.5px;
}

.platforms th {
  padding: 0 16px 10px 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-3);
  text-align: left;
}

.platforms td {
  padding: 10px 16px 10px 0;
  border-top: 1px solid var(--line);
  vertical-align: top;
}

.platforms td:first-child {
  color: var(--text);
  white-space: nowrap;
}

@media (max-width: 560px) {
  .platforms td:first-child {
    white-space: normal;
  }
}
</style>
