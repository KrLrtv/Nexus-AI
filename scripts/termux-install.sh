#!/data/data/com.termux/files/usr/bin/bash
# NEXUS AI - clean F-Droid Termux installer (no migration, no pack, no old HOME).
# Verified public dependencies: proot-distro, Termux-PlatformIO, nrflash, sherpa-onnx.
# For a FULL install, pass a trusted release ZIP URL (or local ZIP) containing source.
set -Eeuo pipefail

VERSION='0.1.2-alpha'
MODEL='sherpa-onnx-small-zipformer-ru-2024-09-18'
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${MODEL}.tar.bz2"
TPIO_REPO='https://github.com/7wp81x/Termux-PlatformIO.git'
CFG="$HOME/.config/nexus"
LOG="$HOME/nexus-fdroid-install.log"
STAGE=''

info() { printf '\n[NEXUS] %s\n' "$*"; }
warn() { printf '\n[NEXUS WARNING] %s\n' "$*" >&2; }
die() { printf '\n[NEXUS ERROR] %s\n' "$*" >&2; exit 1; }
cleanup() { if [[ -n "$STAGE" && -d "$STAGE" ]]; then rm -rf -- "$STAGE"; fi; }
trap cleanup EXIT
trap 'warn "Установка прервана. Детали: $LOG"' ERR

show_help() {
  cat <<'EOF'
NEXUS AI: чистая установка в Termux из F-Droid.

  bash NEXUS_FDroid_Clean_v2.sh prepare
      Установить только окружение: Ubuntu, STT, tpio, nrflash, eSpeak.
      Не требует старого NEXUS и релизного архива.

  bash NEXUS_FDroid_Clean_v2.sh install HTTPS_RELEASE_ZIP_URL
      Установить полную NEXUS из опубликованного релиза.
      ZIP должен содержать nexus-ai и nexus-firmware/firmware (либо
      исходники android_bridge + прошивку platformio.ini/src/main.cpp).
      Можно передать локальный файл ZIP вместо URL.

  bash NEXUS_FDroid_Clean_v2.sh inspect ZIP_FILE
      Без установки проверить, что ZIP подходит по структуре.

  bash NEXUS_FDroid_Clean_v2.sh doctor
      Проверить пакеты, команды и исходники.

  NEXUS_RELEASE_SHA256=<sha256> bash ... install URL
      Сверить ожидаемый SHA-256 релизного ZIP до установки.

Не переносит старые базы, пароли, архивы Ubuntu или файлы Termux.
Не прошивает M5 без отдельного действия пользователя.
EOF
}

need_termux() {
  [[ -n "${PREFIX:-}" && "$PREFIX" == '/data/data/com.termux/files/usr' ]] || \
    die 'Запускай внутри Termux, установленного через F-Droid (не в Ubuntu PRoot).'
  command -v pkg >/dev/null 2>&1 || die 'Не найдена команда pkg.'
  if command -v termux-info >/dev/null 2>&1; then
    info 'Проверь источник Termux: F-Droid Termux и Termux:API должны быть подписаны совместимо.'
  fi
  [[ "${HOME}" == /data/data/com.termux/files/home ]] || die "Необычный HOME: $HOME; остановлено для защиты файлов."
}

init_log() {
  mkdir -p -- "$CFG"
  chmod 700 "$CFG"
  touch "$LOG"
  chmod 600 "$LOG"
  exec > >(tee -a "$LOG") 2>&1
}

install_termux_packages() {
  info 'Устанавливаю официальные пакеты Termux...'
  pkg update -y
  pkg install -y python git curl wget tar unzip proot-distro libusb termux-api espeak ffmpeg
  # Optional external tooling may depend on compiler libs; installed only when needed.
  command -v python >/dev/null 2>&1 || die 'python не найден после pkg install'
  command -v proot-distro >/dev/null 2>&1 || die 'proot-distro не найден после pkg install'
  command -v espeak >/dev/null 2>&1 || warn 'eSpeak не обнаружен. Проверь pkg install espeak.'
}

install_flashing_tools() {
  info 'Устанавливаю nrflash + espbridge из PyPI...'
  python -m pip install --no-cache-dir nrflash espbridge
  command -v nrflash >/dev/null 2>&1 || die 'nrflash не появился в PATH.'
  info 'Устанавливаю tpio из открытого Termux-PlatformIO...'
  local dir="$HOME/.cache/nexus/Termux-PlatformIO"
  mkdir -p "$(dirname "$dir")"
  if [[ -d "$dir/.git" ]]; then
    git -C "$dir" fetch --depth 1 origin && git -C "$dir" reset --hard FETCH_HEAD
  elif [[ ! -e "$dir" ]]; then
    git clone --depth 1 "$TPIO_REPO" "$dir"
  else
    die "Существует несвязанный каталог $dir; не перезаписываю."
  fi
  # Installs the wrapper in Termux; the bootstrap below uses its supported setup command.
  (cd "$dir" && bash install.sh)
  command -v tpio >/dev/null 2>&1 || die 'Команда tpio не обнаружена.'
  # On Android with SELinux enforcing, Ubuntu apt/dpkg needs PRoot's
  # link2symlink hard-link emulation. Upstream tpio setup may explicitly
  # disable it, making dpkg fail on /usr/bin/perl and status-old.
  # Patch only the setup helper; leave proot_compile() unchanged.
  local proot_lib="$dir/lib/proot.sh"
  [[ -f "$proot_lib" ]] || die "Не найден tpio PRoot helper: $proot_lib"
  python - "$proot_lib" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text(encoding='utf-8')
old = 'proot-distro login "$DISTRO" --no-link2symlink -- "$@"'
new = 'proot-distro login "$DISTRO" -- "$@"'
if old in source:
    p.write_text(source.replace(old, new, 1), encoding='utf-8')
elif new not in source:
    raise SystemExit("Неизвестная версия tpio lib/proot.sh; автоисправление остановлено")
PY
  info 'Настраиваю Ubuntu PRoot и PlatformIO...'
  tpio setup
}

install_stt() {
  info 'Устанавливаю русское локальное STT в Ubuntu (модель загружается из sherpa-onnx GitHub Releases)...'
  local script
  script="$(mktemp "${TMPDIR:-$PREFIX/tmp}/nexus-stt-XXXXXX.sh")"
  cat > "$script" <<'EOS'
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip python3-dev ca-certificates curl tar bzip2 libgomp1 libopenblas0-pthread
mkdir -p /opt/nexus-stt/models
if [[ ! -x /opt/nexus-stt/venv/bin/python ]]; then
  python3 -m venv /opt/nexus-stt/venv
fi
/opt/nexus-stt/venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir sherpa-onnx flask numpy
/opt/nexus-stt/venv/bin/python -c 'import sherpa_onnx, flask, numpy; print("sherpa-onnx OK")'
# Legacy NEXUS worker may run with Ubuntu system Python, so expose venv deps there.
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
mkdir -p "/usr/local/lib/python${PYVER}/dist-packages"
printf '%s\n' "/opt/nexus-stt/venv/lib/python${PYVER}/site-packages" > "/usr/local/lib/python${PYVER}/dist-packages/nexus-stt.pth"
if [[ ! -f "/opt/nexus-stt/models/@@MODEL@@/tokens.txt" ]]; then
  cd /opt/nexus-stt/models
  curl -fL --retry 3 --connect-timeout 15 -o '@@MODEL@@.tar.bz2' '@@URL@@'
  tar -xjf '@@MODEL@@.tar.bz2'
  [[ -f '@@MODEL@@/encoder.int8.onnx' && -f '@@MODEL@@/tokens.txt' ]] || exit 11
  rm -f '@@MODEL@@.tar.bz2'
fi
EOS
  # MODEL and URL are constant strings, not user input.
  sed -i "s|@@MODEL@@|$MODEL|g; s|@@URL@@|$MODEL_URL|g" "$script"
  # proot-distro may be installed via tpio setup; access script in shared Termux HOME through /data/data path.
  if ! proot-distro login ubuntu -- bash -s < "$script"; then
    rm -f -- "$script"
    die 'Не удалось настроить sherpa-onnx. См. лог. Остальные части окружения могли установиться.'
  fi
  rm -f -- "$script"
  info 'STT готово.'
}

fetch_release() {
  local source="$1"
  local output="$STAGE/release.zip"
  if [[ "$source" == https://* ]]; then
    info "Скачиваю релиз NEXUS по HTTPS..."
    curl -fL --retry 3 --connect-timeout 15 --max-time 600 -o "$output" "$source"
  elif [[ -f "$source" && "$source" == *.zip ]]; then
    cp -- "$source" "$output"
  else
    die 'Нужен HTTPS URL опубликованного ZIP с исходниками или локальный ZIP-файл.'
  fi
  local actual
  actual="$(sha256sum "$output" | awk '{print $1}')"
  info "SHA256 ZIP: $actual"
  if [[ -n "${NEXUS_RELEASE_SHA256:-}" && "$actual" != "${NEXUS_RELEASE_SHA256,,}" ]]; then
    die 'Контрольная сумма ZIP не совпала. Ничего не устанавливаю.'
  fi
  [[ "$(stat -c %s "$output")" -le 400000000 ]] || die 'Релиз больше 400 MB: возможно, выбран неправильный ZIP.'
  printf '%s\n' "$output"
}

# safe_extract: no zip-slip, no links, no overwritten files; layouts validated by real file presence.
extract_release() {
  local archive="$1" dest="$2"
  python - "$archive" "$dest" <<'PY'
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import sys, shutil, stat
archive,dest=Path(sys.argv[1]),Path(sys.argv[2])
dest.mkdir(parents=True,exist_ok=True)
with ZipFile(archive) as z:
    infos=z.infolist();seen=set();total=0
    if len(infos)>16000: raise SystemExit('Too many archive members')
    for zi in infos:
        n=zi.filename.replace('\\','/')
        p=Path(n)
        if not n or p.is_absolute() or '..' in p.parts or any(x in ('','.') for x in p.parts) or ':' in n:
            raise SystemExit('Unsafe archive filename: '+n)
        if n in seen:raise SystemExit('Duplicate archive member: '+n)
        seen.add(n)
        typ=stat.S_IFMT(zi.external_attr>>16)
        if typ==stat.S_IFLNK:raise SystemExit('Symlink in archive: '+n)
        if p.name.lower() in {'secrets.env','gemini.env','device.env','.env','credentials.json','token.json'} or p.suffix.lower() in {'.pem','.p12','.pfx','.key'}:
            raise SystemExit('Secret/credential file in release ZIP: '+n)
        if zi.file_size>140_000_000:raise SystemExit('Huge archive file: '+n)
        total+=zi.file_size
        if total>900_000_000:raise SystemExit('Archive expanded size exceeds limit')
    for zi in infos:
        n=zi.filename.replace('\\','/')
        p=dest/n
        if zi.is_dir():p.mkdir(parents=True,exist_ok=True);continue
        p.parent.mkdir(parents=True,exist_ok=True)
        with z.open(zi) as src, p.open('xb') as f: shutil.copyfileobj(src,f,1024*1024)
# Find source roots by checking required files. Support GitHub source-zip wrapping directory.
app=[];fw=[]
for p in dest.rglob('nexusctl.py'):
    if p.is_file() and (p.parent/'android_bridge/app.py').is_file() and (p.parent/'android_bridge/supervisor.py').is_file():app.append(p.parent)
for p in dest.rglob('platformio.ini'):
    if p.is_file() and (p.parent/'src/main.cpp').is_file():fw.append(p.parent)
if len(app)!=1 or len(fw)!=1:
    raise SystemExit(f'Release must have exactly one nexusctl.py/android_bridge and one platformio.ini/src/main.cpp: found app={len(app)} firmware={len(fw)}')
if (app[0]/'android_bridge/planner.py').is_file() is False:
    raise SystemExit('planner.py missing, source incomplete')
print('NEXUS source:',app[0]);print('Firmware source:',fw[0])
(dest/'.nexus-source-paths').write_text(app[0].relative_to(dest).as_posix()+'\n'+fw[0].relative_to(dest).as_posix()+'\n')
PY
}

install_source() {
  local unpack="$STAGE/unpack"
  extract_release "$STAGE/release.zip" "$unpack"
  local app_rel fw_rel
  app_rel="$(sed -n '1p' "$unpack/.nexus-source-paths")"
  fw_rel="$(sed -n '2p' "$unpack/.nexus-source-paths")"
  [[ ! -e "$HOME/nexus-ai" && ! -e "$HOME/nexus-firmware" ]] || \
    die 'Найдена прежняя установка NEXUS. Для безопасности остановлено, исходники не перезаписаны.'
  mkdir -p "$HOME/nexus-firmware"
  mv "$unpack/$app_rel" "$HOME/nexus-ai"
  mv "$unpack/$fw_rel" "$HOME/nexus-firmware/firmware"
  info 'Исходники NEXUS установлены в ~/nexus-ai и ~/nexus-firmware/firmware.'
}

make_venv() {
  info 'Устанавливаю зависимости сервера NEXUS в отдельное Python-окружение...'
  local py="$HOME/.venvs/nexus/bin/python"
  mkdir -p "$HOME/.venvs"
  if [[ ! -x "$py" ]]; then python -m venv "$HOME/.venvs/nexus"; fi
  "$py" -m pip install --disable-pip-version-check --no-cache-dir flask requests
  local req
  for req in "$HOME/nexus-ai/requirements.txt" "$HOME/nexus-ai/android_bridge/requirements.txt"; do
    [[ ! -f "$req" ]] || "$py" -m pip install --disable-pip-version-check --no-cache-dir -r "$req"
  done
  cat > "$PREFIX/bin/nexus" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
PY="$HOME/.venvs/nexus/bin/python"
if [[ "$#" -eq 0 ]]; then
  if [[ -f "$HOME/nexus-ai/nexus_tui.py" ]]; then exec "$PY" "$HOME/nexus-ai/nexus_tui.py"; fi
elif [[ "$1" == ui ]]; then
  shift
  exec "$PY" "$HOME/nexus-ai/nexus_tui.py" "$@"
elif [[ "$1" == --legacy ]]; then
  shift
fi
exec "$PY" "$HOME/nexus-ai/nexusctl.py" "$@"
EOF
  chmod 700 "$PREFIX/bin/nexus"
}

install_stt_launcher() {
  info 'Устанавливаю nexus-stt: локальный STT worker + команды start/stop/status...'
  local worker="$HOME/nexus-ai/android_bridge/nexus_stt_worker.py"
  [[ -f "$worker" ]] || die 'В релизе отсутствует nexus_stt_worker.py.'
  # Copy worker into Ubuntu filesystem: the default PRoot environment does not
  # guarantee that arbitrary Termux HOME paths are visible inside Ubuntu.
  proot-distro login ubuntu -- bash -c 'install -d -m 755 /opt/nexus-stt; cat >/opt/nexus-stt/nexus_stt_worker.py' < "$worker"
  cat > "$PREFIX/bin/nexus-stt" <<'EOS'
#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail
WORKER='/opt/nexus-stt/nexus_stt_worker.py'
MODEL='/opt/nexus-stt/models/sherpa-onnx-small-zipformer-ru-2024-09-18'
LOG="$HOME/nexus-stt.log"
PID="$HOME/.config/nexus/stt_worker.pid"
health() { curl --noproxy '*' -fsS --max-time 2 'http://127.0.0.1:8766/health' >/dev/null 2>&1; }
case "${1:-status}" in
  status)
    if health; then echo 'NEXUS STT: работает';else echo 'NEXUS STT: не запущен';exit 1;fi;;
  start)
    if health;then echo 'NEXUS STT: уже запущен';exit 0;fi
    mkdir -p "$HOME/.config/nexus"
    nohup proot-distro login ubuntu -- /opt/nexus-stt/venv/bin/python "$WORKER" --model "$MODEL" --port 8766 \
      >>"$LOG" 2>&1 </dev/null &
    echo "$!" > "$PID"
    chmod 600 "$PID"
    for i in $(seq 1 25);do
      if health;then echo 'NEXUS STT: запущен';exit 0;fi
      sleep 2
    done
    echo 'NEXUS STT: не ответил за 50с, проверь ~/nexus-stt.log' >&2
    exit 1;;
  stop)
    if health;then curl --noproxy '*' --max-time 3 -fsS -X POST 'http://127.0.0.1:8766/stop' >/dev/null || true;fi
    rm -f "$PID"
    echo 'NEXUS STT: остановлен';;
  restart) "$0" stop; "$0" start;;
  *) echo 'Используй: nexus-stt start|stop|restart|status' >&2; exit 2;;
esac
EOS
  chmod 700 "$PREFIX/bin/nexus-stt"
}

configure_m5_firmware() {
  local file="$HOME/nexus-firmware/firmware/src/config_local.h"
  [[ ! -f "$file" ]] || { warn 'Существующий config_local.h не изменён.'; return; }
  info 'Настройка Wi-Fi для прошивки M5StickC Plus2 (необязательно сейчас).'
  python - <<'PY2'
from pathlib import Path
import getpass, json, os, shlex
home=Path.home(); path=home/'nexus-firmware/firmware/src/config_local.h'
ssid=input('SSID Wi-Fi 2.4 ГГц (Enter = настроить позже): ').strip()
if not ssid:
    print('Wi-Fi не настроен. Для сборки прошивки позже создай src/config_local.h по шаблону config_local.example.h.')
    raise SystemExit(0)
password=getpass.getpass('Пароль Wi-Fi (не отображается): ')
secret_env=home/'.config/nexus/secrets.env'
token=''
for line in secret_env.read_text(encoding='utf-8').splitlines():
    if line.startswith('export NEXUS_DEVICE_TOKEN='):
        pieces=shlex.split(line[7:])
        if len(pieces)==1 and pieces[0].startswith('NEXUS_DEVICE_TOKEN='):
            token=pieces[0].split('=',1)[1]
if len(token)<16:
    raise SystemExit('Токен M5StickC не найден или слишком короткий')
q=lambda x:json.dumps(x,ensure_ascii=False)
content=('// Local credentials; NEVER commit this file.\\n'
         '#pragma once\\n'
         f'#define NEXUS_WIFI_SSID {q(ssid)}\\n'
         f'#define NEXUS_WIFI_PASSWORD {q(password)}\\n'
         '#define NEXUS_BRIDGE_URL "AUTO"\\n'
         f'#define NEXUS_DEVICE_TOKEN {q(token)}\\n')
with path.open('x',encoding='utf-8') as f:f.write(content.replace('\\n','\n'))
os.chmod(path,0o600)
print('Локальная конфигурация ESP32 создана. Значения не показаны и исключены из Git.')
PY2
}

configure_first_run() {
  [[ ! -f "$CFG/secrets.env" ]] || { warn 'Существующий secrets.env не перезаписан.'; return; }
  info 'Первоначальная настройка. Ключи не выводятся на экран и не записываются в журнал.'
  python - <<'PY'
from pathlib import Path
import os, secrets, shlex, getpass
cfg=Path.home()/'.config/nexus';cfg.mkdir(parents=True,exist_ok=True);cfg.chmod(0o700)
path=cfg/'secrets.env'
if path.exists():exit()
token=getpass.getpass('Токен M5StickC (Enter = создать новый): ').strip() or secrets.token_urlsafe(32)
gem=getpass.getpass('Ключ Gemini (Enter = настрою позже): ').strip()
rout=getpass.getpass('Ключ OpenRouter (необязательно): ').strip()
model='gemini-3.1-flash-lite'
with path.open('x') as f:
    for k,v in [('NEXUS_DEVICE_TOKEN',token),('GEMINI_API_KEY',gem),('GEMINI_MODEL',model),('OPENROUTER_API_KEY',rout)]:
        f.write('export '+k+'='+shlex.quote(v)+'\n')
path.chmod(0o600)
print('Секреты сохранены. При новой прошивке токен M5 должен совпадать с этим значением.')
PY
  for mode in 'stt_mode:sherpa' 'voice_clarity_mode:clear' 'speed_mode:balanced'; do
    local key="${mode%%:*}" value="${mode#*:}"
    [[ -f "$CFG/$key" ]] || printf '%s\n' "$value" > "$CFG/$key"
    chmod 600 "$CFG/$key"
  done
}

run_doctor() {
  info "NEXUS Clean Installer $VERSION: диагностика"
  for bin in pkg python git curl proot-distro tpio nrflash espeak nexus-stt; do
    if command -v "$bin" >/dev/null 2>&1; then printf '  OK   %s\n' "$bin"; else printf '  --   %s\n' "$bin"; fi
  done
  for f in "$HOME/nexus-ai/nexusctl.py" "$HOME/nexus-ai/android_bridge/app.py" "$HOME/nexus-firmware/firmware/platformio.ini" "$HOME/.config/nexus/secrets.env" "$HOME/nexus-firmware/firmware/src/config_local.h"; do
    if [[ -f "$f" ]]; then printf '  OK   %s\n' "$f"; else printf '  --   %s\n' "$f"; fi
  done
  if command -v proot-distro >/dev/null 2>&1 && proot-distro login ubuntu -- bash -lc 'test -f /opt/nexus-stt/models/sherpa-onnx-small-zipformer-ru-2024-09-18/tokens.txt' >/dev/null 2>&1; then
    info 'Локальная модель sherpa-onnx: установлена.'
  else
    warn 'Локальная модель sherpa-onnx: не подтверждена.'
  fi
  info "Подробности установки: $LOG"
}

ACTION="${1:-prepare}"
case "$ACTION" in
  -h|--help|help) show_help; exit 0;;
  inspect)
    [[ -n "${2:-}" ]] || die 'Укажи локальный ZIP для проверки.'
    command -v python >/dev/null 2>&1 || die 'Для inspect требуется Python.'
    STAGE="$(mktemp -d)"; extract_release "$2" "$STAGE/unpack"; info 'ZIP содержит оба исходных проекта.'; exit 0;;
  doctor) need_termux; run_doctor; exit 0;;
  prepare|install) need_termux;;
  *) show_help; die "Неизвестная команда: $ACTION";;
esac

if [[ "$ACTION" == install ]]; then
  [[ -n "${2:-}" ]] || { show_help; die 'Для полной установки укажи опубликованный ZIP с исходниками NEXUS. Без исходников невозможно воспроизвести текущую R89.'; }
  [[ ! -e "$HOME/nexus-ai" && ! -e "$HOME/nexus-firmware" ]] || die 'NEXUS уже установлен: это чистый установщик.'
fi

# Avoid logging potentially secret command-line URL query strings; regular release URLs preferred.
init_log
STAGE="$(mktemp -d "${TMPDIR:-$PREFIX/tmp}/nexus-fresh-XXXXXX")"
if [[ "$ACTION" == install ]]; then
  # A fresh F-Droid Termux has no Python: the ZIP verifier below requires it.
  # Install only this prerequisite before verifying the archive structure.
  if ! command -v python >/dev/null 2>&1; then
    info 'Устанавливаю Python для предварительной проверки релизного ZIP...'
    pkg install -y python
  fi
  fetch_release "$2" >/dev/null
  extract_release "$STAGE/release.zip" "$STAGE/precheck"
  rm -rf "$STAGE/precheck"
  info 'Структура ZIP проверена до установки пакетов.'
fi
install_termux_packages
install_flashing_tools
install_stt
if [[ "$ACTION" == install ]]; then
  install_source
  make_venv
  configure_first_run
  install_stt_launcher
  configure_m5_firmware
  info 'Проверка исходников и окружения...'
  run_doctor
  info 'Готово. Запусти: nexus. Прошивка не выполнялась.'
  info 'Для сборки и прошивки M5: cd ~/nexus-firmware/firmware && tpio run -e m5stickc_plus2'
else
  info 'БАЗОВОЕ ОКРУЖЕНИЕ ПОДГОТОВЛЕНО: Ubuntu, STT, eSpeak, PlatformIO, tpio, nrflash.'
  warn 'Сам NEXUS ещё НЕ установлен: нужен опубликованный ZIP с полными исходниками.'
  info 'Затем: bash /sdcard/Download/NEXUS_FDroid_Clean_v2.sh install https://.../NEXUS_1_0.zip'
fi
