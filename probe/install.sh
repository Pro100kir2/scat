#!/bin/sh
# Скат provider probe installer.
#
#   curl -fsSL __SCAT_API_URL__/api/probes/install.sh | sh -s -- --token <TOKEN>
#   wget -qO- __SCAT_API_URL__/api/probes/install.sh | sh -s -- --token <TOKEN>
#
# Options:
#   --token TOKEN      one-time enrollment token from the website
#   --diagnose-only    run the compatibility check and stop; installs nothing
#   --print-diagnostics  print the collected facts as JSON and stop (nothing is sent)
#   --uninstall        stop and remove the probe, its service and its credentials
#   --reenroll         register a new probe even if this device already has one
#
# What happens, in this order:
#   1. DIAGNOSTIC  facts about this device are collected and sent to the server over HTTPS
#   2. VERDICT     the server answers SUPPORTED or UNSUPPORTED. If unsupported, the script
#                  explains why and exits. Nothing is downloaded or installed.
#   3. INSTALL     the matching binary is downloaded, its SHA-256 is verified, it is test-run,
#                  installed, registered, set up as a service, started and checked.
#
# The probe only checks whether VPN endpoints are reachable from this network. It does not
# run VLESS or Xray, is not a VPN, opens no listening ports and never executes commands
# sent by the server. This script never evaluates anything the server sends.

set -u

API="__SCAT_API_URL__"
INSTALLER_VERSION="1"
# Alternate root, like DESTDIR: all reads and writes of system paths go below it.
# Only the test-suite sets it; on a real device it is empty.
R="${SCAT_PROBE_ROOT:-}"

CONFIG_DIR="/etc/scat-probe"
CONFIG="$CONFIG_DIR/probe.json"
BIN_NAME="scat-probe"

TOKEN=""
MODE="install"
REENROLL=0
TMP=""

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; cleanup; exit "${2:-1}"; }
have() { command -v "$1" >/dev/null 2>&1; }
cleanup() { [ -n "$TMP" ] && rm -rf "$TMP"; TMP=""; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

while [ $# -gt 0 ]; do
    case "$1" in
        --token) [ $# -ge 2 ] || die "--token needs a value"; TOKEN="$2"; shift 2 ;;
        --token=*) TOKEN="${1#--token=}"; shift ;;
        --diagnose-only) MODE="diagnose"; shift ;;
        --print-diagnostics) MODE="print"; shift ;;
        --uninstall) MODE="uninstall"; shift ;;
        --reenroll) REENROLL=1; shift ;;
        --api) [ $# -ge 2 ] || die "--api needs a value"; API="$2"; shift 2 ;;
        -h|--help) sed -n '2,24p' "$0" 2>/dev/null || say "see the website for usage"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

check_api() {
    case "$API" in
        https://*) ;;
        http://127.0.0.1:*|http://localhost:*) ;;  # local development only
        *) die "invalid API address: $API" ;;
    esac
    case "$API" in *[!A-Za-z0-9.:/_-]*) die "invalid API address: $API" ;; esac
}
case "$TOKEN" in
    ""|skenr_*) ;;
    *) die "this does not look like an enrollment token" ;;
esac
case "$TOKEN" in
    *[!A-Za-z0-9_-]*) die "the token contains unexpected characters" ;;
esac
[ "$MODE" = print ] || check_api

# ------------------------------------------------------------------ helpers
readf() { [ -r "$R$1" ] && cat "$R$1" 2>/dev/null; }

# Value of KEY='value' / KEY="value" / KEY=value in a file, without sourcing it.
kv() {
    readf "$1" | sed -n "s/^$2=//p" | head -n 1 | sed -e "s/^[\"']//" -e "s/[\"']\$//"
}

json_escape() {
    printf '%s' "$1" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}
jstr() { if [ -z "$1" ]; then printf 'null'; else printf '"%s"' "$(json_escape "$1")"; fi; }
jnum() { case "$1" in ''|*[!0-9]*) printf 'null' ;; *) printf '%s' "$1" ;; esac; }
jbool() { case "$1" in 1) printf 'true' ;; 0) printf 'false' ;; *) printf 'null' ;; esac; }
yn() { if "$@" >/dev/null 2>&1; then printf 1; else printf 0; fi; }

# ------------------------------------------------------------------ 1. diagnostic
collect() {
    D_OS=$(uname -s 2>/dev/null)
    D_KERNEL=$(uname -r 2>/dev/null)
    D_MACHINE=$(uname -m 2>/dev/null)

    D_DISTRO=$(kv /etc/os-release PRETTY_NAME)
    [ -n "$D_DISTRO" ] || D_DISTRO=$(kv /etc/os-release NAME)
    D_DISTRO_VERSION=$(kv /etc/os-release VERSION_ID)

    D_OPENWRT=0; D_OWRT_VERSION=""; D_OWRT_ARCH=""; D_OWRT_BOARD=""
    if [ -r "$R/etc/openwrt_release" ]; then
        D_OPENWRT=1
        D_OWRT_VERSION=$(kv /etc/openwrt_release DISTRIB_RELEASE)
        D_OWRT_ARCH=$(kv /etc/openwrt_release DISTRIB_ARCH)
        D_OWRT_BOARD=$(kv /etc/openwrt_release DISTRIB_TARGET)
        [ -n "$D_DISTRO" ] || D_DISTRO=$(kv /etc/openwrt_release DISTRIB_DESCRIPTION)
    fi

    # CPU: x86 "model name", MIPS "system type" (e.g. MediaTek MT7621), ARM "Hardware"/"Model".
    D_CPU=""
    for field in "model name" "system type" "Hardware" "Model" "cpu model" "Processor" "uarch"; do
        D_CPU=$(readf /proc/cpuinfo | sed -n "s/^$field[[:space:]]*:[[:space:]]*//p" | head -n 1)
        [ -n "$D_CPU" ] && break
    done
    D_CORES=$(readf /proc/cpuinfo | grep -c '^processor' 2>/dev/null)
    case "$D_CORES" in ''|0) D_CORES=$(nproc 2>/dev/null) ;; esac

    D_MEM_TOTAL=$(readf /proc/meminfo | awk '/^MemTotal:/ {print $2; exit}')
    D_MEM_AVAIL=$(readf /proc/meminfo | awk '/^MemAvailable:/ {print $2; exit}')
    if [ -z "$D_MEM_AVAIL" ]; then  # kernels before 3.14
        D_MEM_AVAIL=$(readf /proc/meminfo | awk '/^(MemFree|Buffers|Cached):/ {s += $2} END {if (s) print s}')
    fi

    # Storage where the binary goes: /usr/bin (the OpenWrt overlay) or /usr/local/bin.
    if [ "$D_OPENWRT" = 1 ]; then INSTALL_DIR_GUESS=/usr/bin; else INSTALL_DIR_GUESS=/usr/local/bin; fi
    probe_dir="$INSTALL_DIR_GUESS"
    while [ ! -d "$R$probe_dir" ] && [ "$probe_dir" != "/" ]; do probe_dir=$(dirname "$probe_dir"); done
    D_STORAGE_PATH="$probe_dir"
    df_line=$(df -kP "$R$probe_dir" 2>/dev/null | awk 'NR==2')
    [ -n "$df_line" ] || df_line=$(df -k "$R$probe_dir" 2>/dev/null | awk 'NR==2')
    D_STORAGE_TOTAL=$(printf '%s' "$df_line" | awk '{print $2}')
    D_STORAGE_FREE=$(printf '%s' "$df_line" | awk '{print $4}')

    # Init system.
    if [ -d "$R/run/systemd/system" ]; then D_INIT=systemd
    elif [ "$D_OPENWRT" = 1 ] || [ -x "$R/sbin/procd" ]; then D_INIT=procd
    elif [ -x "$R/sbin/openrc-run" ] || [ -x "$R/sbin/openrc" ]; then D_INIT=openrc
    elif [ -d "$R/etc/init.d" ]; then D_INIT=sysvinit
    else D_INIT=unknown
    fi
    D_SYSTEMD=0; [ "$D_INIT" = systemd ] && D_SYSTEMD=1

    D_CURL=$(yn have curl)
    D_WGET=$(yn have wget)
    D_WGET_HTTPS=0
    if [ "$D_WGET" = 1 ] && wget --help 2>&1 | grep -qi -e 'no-check-certificate' -e 'ca-certificate'; then D_WGET_HTTPS=1; fi
    have uclient-fetch && D_WGET_HTTPS=1
    D_BUSYBOX=$(yn have busybox)
    D_DOCKER=$(yn have docker)

    D_USER=$(id -un 2>/dev/null)
    if [ "$(id -u 2>/dev/null)" = 0 ]; then D_ROOT=1; else D_ROOT=0; fi

    # The userland's word size and byte order, from the ELF header of the shell itself.
    # `uname -m` says "mips" for both byte orders and "aarch64" under 32-bit userlands.
    D_ELF_CLASS=""; D_ENDIAN=""
    for f in /bin/busybox /bin/sh; do
        [ -r "$R$f" ] || continue
        bytes=$(od -An -tu1 -j4 -N2 "$R$f" 2>/dev/null) \
            || bytes=$(hexdump -s 4 -n 2 -e '2/1 "%u "' "$R$f" 2>/dev/null) || bytes=""
        set -- $bytes
        if [ $# -ge 2 ]; then
            case "$1" in 1) D_ELF_CLASS=32 ;; 2) D_ELF_CLASS=64 ;; esac
            case "$2" in 1) D_ENDIAN=little ;; 2) D_ENDIAN=big ;; esac
            break
        fi
    done

    # Connectivity from the routing tables (no extra traffic): a default route per family.
    D_IPV4=0; D_IPV6=0
    readf /proc/net/route | awk 'NR>1 && $2=="00000000" {f=1} END {exit !f}' && D_IPV4=1
    readf /proc/net/ipv6_route | awk '$1=="00000000000000000000000000000000" && $2=="00" {f=1} END {exit !f}' && D_IPV6=1
}

diag_json() {
    printf '{'
    printf '"os":%s,' "$(jstr "$D_OS")"
    printf '"distro":%s,' "$(jstr "$D_DISTRO")"
    printf '"distro_version":%s,' "$(jstr "$D_DISTRO_VERSION")"
    printf '"kernel":%s,' "$(jstr "$D_KERNEL")"
    printf '"machine":%s,' "$(jstr "$D_MACHINE")"
    printf '"cpu_model":%s,' "$(jstr "$D_CPU")"
    printf '"cpu_cores":%s,' "$(jnum "$D_CORES")"
    printf '"mem_total_kb":%s,' "$(jnum "$D_MEM_TOTAL")"
    printf '"mem_available_kb":%s,' "$(jnum "$D_MEM_AVAIL")"
    printf '"storage_total_kb":%s,' "$(jnum "$D_STORAGE_TOTAL")"
    printf '"storage_free_kb":%s,' "$(jnum "$D_STORAGE_FREE")"
    printf '"storage_path":%s,' "$(jstr "$D_STORAGE_PATH")"
    printf '"init":%s,' "$(jstr "$D_INIT")"
    printf '"systemd":%s,' "$(jbool "$D_SYSTEMD")"
    printf '"openwrt":%s,' "$(jbool "$D_OPENWRT")"
    printf '"openwrt_version":%s,' "$(jstr "$D_OWRT_VERSION")"
    printf '"openwrt_arch":%s,' "$(jstr "$D_OWRT_ARCH")"
    printf '"openwrt_board":%s,' "$(jstr "$D_OWRT_BOARD")"
    printf '"curl":%s,' "$(jbool "$D_CURL")"
    printf '"wget":%s,' "$(jbool "$D_WGET")"
    printf '"wget_https":%s,' "$(jbool "$D_WGET_HTTPS")"
    printf '"busybox":%s,' "$(jbool "$D_BUSYBOX")"
    printf '"docker":%s,' "$(jbool "$D_DOCKER")"
    printf '"is_root":%s,' "$(jbool "$D_ROOT")"
    printf '"user":%s,' "$(jstr "$D_USER")"
    printf '"elf_class":%s,' "$(jnum "$D_ELF_CLASS")"
    printf '"endian":%s,' "$(jstr "$D_ENDIAN")"
    printf '"ipv4":%s,' "$(jbool "$D_IPV4")"
    printf '"ipv6":%s,' "$(jbool "$D_IPV6")"
    printf '"installer_version":%s' "$(jstr "$INSTALLER_VERSION")"
    printf '}'
}

# ------------------------------------------------------------------ HTTPS
http_post() {  # http_post URL JSON → body on stdout
    if have curl; then
        curl -fsS --max-time 60 -H 'Content-Type: application/json' --data-binary "$2" "$1"
    elif have wget; then
        wget -qO- -T 60 --header='Content-Type: application/json' --post-data="$2" "$1"
    else
        return 127
    fi
}

http_get() {  # http_get URL FILE
    if have curl; then
        curl -fsSL --max-time 300 -o "$2" "$1"
    elif have wget; then
        wget -qO "$2" -T 300 "$1"
    else
        return 127
    fi
}

sha256_of() {
    if have sha256sum; then sha256sum "$1" | awk '{print $1}'
    elif have openssl; then openssl dgst -sha256 "$1" | awk '{print $NF}'
    elif have shasum; then shasum -a 256 "$1" | awk '{print $1}'
    else return 127
    fi
}

# ------------------------------------------------------------------ 2. verdict
V_VERDICT=""; V_PROFILE=""; V_PROFILE_NAME=""; V_SERVICE=""; V_DIR=""; V_URL=""; V_SHA=""; V_VERSION=""
V_REASONS=""; V_WARNINGS=""; V_ALTERNATIVES=""
NL='
'

ask_server() {
    body="{\"diagnostics\":$(diag_json)"
    [ -n "$TOKEN" ] && body="$body,\"token\":\"$TOKEN\""
    body="$body}"
    reply=$(http_post "$API/api/probes/diagnostics?format=env" "$body") \
        || die "the compatibility check could not reach $API (is HTTPS working? was the token copied fully?)"
    # Strict line parser: known keys only, nothing is ever evaluated.
    while IFS= read -r line; do
        key=${line%%=*}; value=${line#*=}
        case "$key" in
            verdict) V_VERDICT=$value ;;
            profile) V_PROFILE=$value ;;
            profile_name) V_PROFILE_NAME=$value ;;
            service) V_SERVICE=$value ;;
            install_dir) V_DIR=$value ;;
            url) V_URL=$value ;;
            sha256) V_SHA=$value ;;
            version) V_VERSION=$value ;;
            reason) V_REASONS="$V_REASONS  - $value$NL" ;;
            warning) V_WARNINGS="$V_WARNINGS  - $value$NL" ;;
            alternative) V_ALTERNATIVES="$V_ALTERNATIVES  - $value$NL" ;;
        esac
    done <<EOF
$reply
EOF
    case "$V_VERDICT" in SUPPORTED|UNSUPPORTED) ;; *) die "unexpected reply from the server" ;; esac
}

validate_profile() {
    case "$V_PROFILE" in linux-[a-z0-9]*) ;; *) die "server sent an invalid profile" ;; esac
    case "$V_PROFILE" in *[!a-z0-9-]*) die "server sent an invalid profile" ;; esac
    case "$V_SHA" in *[!0-9a-f]*|'') die "server sent an invalid checksum" ;; esac
    [ ${#V_SHA} -eq 64 ] || die "server sent an invalid checksum"
    case "$V_URL" in "$API/api/probes/download/$V_PROFILE") ;; *) die "server sent an unexpected download URL" ;; esac
    case "$V_DIR" in /usr/bin|/usr/local/bin) ;; *) die "server sent an unexpected install directory" ;; esac
    case "$V_SERVICE" in systemd|procd|openrc|sysvinit|none) ;; *) die "server sent an unknown service type" ;; esac
}

# ------------------------------------------------------------------ 3. install
write_service() {
    bin="$V_DIR/$BIN_NAME"
    case "$V_SERVICE" in
    systemd)
        mkdir -p "$R/etc/systemd/system"
        cat > "$R/etc/systemd/system/scat-probe.service" <<UNIT
[Unit]
Description=Скат provider probe (network reachability checks only)
Documentation=$API/api/probes/releases
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$bin run --config $CONFIG
Restart=always
RestartSec=10
# Outbound TCP needs no privileges at all.
CapabilityBoundingSet=
AmbientCapabilities=
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$CONFIG_DIR
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
LockPersonality=yes
MemoryMax=64M

[Install]
WantedBy=multi-user.target
UNIT
        ;;
    procd)
        mkdir -p "$R/etc/init.d"
        cat > "$R/etc/init.d/scat-probe" <<INIT
#!/bin/sh /etc/rc.common
# Скат provider probe (network reachability checks only)
USE_PROCD=1
START=95
STOP=10

start_service() {
    procd_open_instance
    procd_set_param command $bin run --config $CONFIG
    procd_set_param respawn 3600 10 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
INIT
        chmod 755 "$R/etc/init.d/scat-probe"
        ;;
    openrc)
        mkdir -p "$R/etc/init.d"
        cat > "$R/etc/init.d/scat-probe" <<INIT
#!/sbin/openrc-run
description="Скат provider probe (network reachability checks only)"
command="$bin"
command_args="run --config $CONFIG"
command_background=true
pidfile="/run/scat-probe.pid"
depend() { need net; }
INIT
        chmod 755 "$R/etc/init.d/scat-probe"
        ;;
    sysvinit)
        mkdir -p "$R/etc/init.d"
        cat > "$R/etc/init.d/scat-probe" <<INIT
#!/bin/sh
### BEGIN INIT INFO
# Provides:          scat-probe
# Required-Start:    \$network \$remote_fs
# Required-Stop:     \$network \$remote_fs
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Скат provider probe
### END INIT INFO
PIDFILE=/var/run/scat-probe.pid
case "\$1" in
  start) nohup $bin run --config $CONFIG >/dev/null 2>&1 & echo \$! > \$PIDFILE ;;
  stop) [ -f \$PIDFILE ] && kill \$(cat \$PIDFILE) 2>/dev/null; rm -f \$PIDFILE ;;
  restart) \$0 stop; sleep 1; \$0 start ;;
  *) echo "usage: \$0 start|stop|restart"; exit 1 ;;
esac
INIT
        chmod 755 "$R/etc/init.d/scat-probe"
        ;;
    esac
}

start_service() {
    case "$V_SERVICE" in
        systemd) systemctl daemon-reload && systemctl enable --now scat-probe ;;
        procd) "$R/etc/init.d/scat-probe" enable && "$R/etc/init.d/scat-probe" restart ;;
        openrc) rc-update add scat-probe default && rc-service scat-probe restart ;;
        sysvinit)
            if have update-rc.d; then update-rc.d scat-probe defaults
            elif have chkconfig; then chkconfig --add scat-probe
            fi
            "$R/etc/init.d/scat-probe" restart ;;
        none)
            warn "no supported init system: starting the probe now, but it will not start after a reboot"
            nohup "$R$V_DIR/$BIN_NAME" run --config "$R$CONFIG" >/dev/null 2>&1 & ;;
    esac
}

install_probe() {
    [ "$(id -u 2>/dev/null)" = 0 ] || die "root privileges are required (re-run with sudo)"
    validate_profile

    step "Downloading $V_PROFILE_NAME build ($V_VERSION)"
    TMP=$(mktemp -d "${TMPDIR:-/tmp}/scat-probe.XXXXXX") || die "cannot create a temporary directory"
    http_get "$V_URL" "$TMP/$BIN_NAME" || die "download failed"

    step "Verifying SHA-256"
    actual=$(sha256_of "$TMP/$BIN_NAME") || die "no sha256sum, openssl or shasum found: cannot verify the download"
    [ "$actual" = "$V_SHA" ] || die "checksum mismatch: expected $V_SHA, got $actual. Nothing was installed."
    say "checksum OK"
    chmod 755 "$TMP/$BIN_NAME"
    # Proves the binary runs on this CPU before anything is placed on the system.
    "$TMP/$BIN_NAME" version >/dev/null 2>&1 || die "the downloaded probe does not run on this device. Nothing was installed."

    step "Installing to $V_DIR/$BIN_NAME"
    mkdir -p "$R$V_DIR" || die "cannot create $V_DIR"
    cp "$TMP/$BIN_NAME" "$R$V_DIR/.$BIN_NAME.new" && mv -f "$R$V_DIR/.$BIN_NAME.new" "$R$V_DIR/$BIN_NAME" \
        || die "cannot write to $V_DIR"

    if [ -f "$R$CONFIG" ] && [ "$REENROLL" = 0 ]; then
        step "Keeping the existing probe identity ($CONFIG)"
    else
        [ -n "$TOKEN" ] || die "--token is required to register this probe"
        step "Registering the probe"
        mkdir -p "$R$CONFIG_DIR" && chmod 700 "$R$CONFIG_DIR"
        force=""; [ "$REENROLL" = 1 ] && force="--force"
        "$R$V_DIR/$BIN_NAME" enroll --api "$API" --token "$TOKEN" --config "$R$CONFIG" $force \
            || die "registration failed (the token is single-use and expires after 24 hours)"
    fi

    step "Setting up the $V_SERVICE service"
    write_service
    start_service || die "could not start the service"

    step "Waiting for the first heartbeat"
    i=0
    while [ $i -lt 10 ]; do
        if "$R$V_DIR/$BIN_NAME" check --config "$R$CONFIG" 2>/dev/null; then
            say ""
            say "Done. The probe is Online; you can close this terminal."
            say "Uninstall later with:  curl -fsSL $API/api/probes/install.sh | sh -s -- --uninstall"
            return 0
        fi
        i=$((i + 1)); sleep 2
    done
    die "the probe is installed but did not reach the server yet; check its log (logread / journalctl -u scat-probe)"
}

uninstall_probe() {
    [ "$(id -u 2>/dev/null)" = 0 ] || die "root privileges are required (re-run with sudo)"
    step "Removing the Скат probe"
    if [ -f "$R/etc/systemd/system/scat-probe.service" ]; then
        systemctl disable --now scat-probe 2>/dev/null
        rm -f "$R/etc/systemd/system/scat-probe.service"
        systemctl daemon-reload 2>/dev/null
    fi
    if [ -x "$R/etc/init.d/scat-probe" ]; then
        "$R/etc/init.d/scat-probe" stop >/dev/null 2>&1
        # `disable` exists only in procd scripts; the others are removed from runlevels below.
        grep -q USE_PROCD "$R/etc/init.d/scat-probe" && "$R/etc/init.d/scat-probe" disable >/dev/null 2>&1
        have rc-update && rc-update del scat-probe default >/dev/null 2>&1
        have update-rc.d && update-rc.d -f scat-probe remove >/dev/null 2>&1
        rm -f "$R/etc/init.d/scat-probe"
    fi
    rm -f "$R/usr/bin/$BIN_NAME" "$R/usr/local/bin/$BIN_NAME"
    rm -rf "$R$CONFIG_DIR"
    say "Removed. The probe will show as Offline on the website."
}

# ------------------------------------------------------------------ main
case "$MODE" in
    uninstall) uninstall_probe; exit 0 ;;
esac

step "DIAGNOSTIC: checking this device"
collect
if [ "$MODE" = print ]; then diag_json; printf '\n'; exit 0; fi
say "  system:  ${D_DISTRO:-$D_OS} (kernel $D_KERNEL)"
say "  cpu:     $D_MACHINE${D_CPU:+, $D_CPU}${D_CORES:+, $D_CORES cores}"
say "  memory:  $(( ${D_MEM_TOTAL:-0} / 1024 )) MB total, $(( ${D_MEM_AVAIL:-0} / 1024 )) MB free"
say "  storage: $(( ${D_STORAGE_FREE:-0} / 1024 )) MB free on $D_STORAGE_PATH"
say "  init:    $D_INIT${D_OWRT_VERSION:+, OpenWrt $D_OWRT_VERSION}"

step "Asking the server whether this device is supported"
ask_server
if [ "$V_VERDICT" = UNSUPPORTED ]; then
    say ""
    say "UNSUPPORTED: this device cannot run the probe. Nothing was installed."
    say "Failed requirements:"
    printf '%s' "$V_REASONS"
    if [ -n "$V_ALTERNATIVES" ]; then say "What you can do instead:"; printf '%s' "$V_ALTERNATIVES"; fi
    exit 3
fi
say "SUPPORTED: $V_PROFILE_NAME, service: $V_SERVICE"
[ -n "$V_WARNINGS" ] && printf 'Notes:\n%s' "$V_WARNINGS"
[ "$MODE" = diagnose ] && exit 0

install_probe
