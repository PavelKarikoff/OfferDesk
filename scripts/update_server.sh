#!/usr/bin/env bash
# update_server.sh — быстрый деплой web_app CRM на VPS (BlueTerbium).
#
# Usage (из корня репозитория):
#   ./scripts/update_server.sh
#   ./scripts/update_server.sh --dry-run
#   ./scripts/update_server.sh --pull-only   # только git pull на сервере, без rsync
#
# SSH берётся из .env:
#   BLUETERBIUM_SSH_HOST / PORT / USER / PASSWORD (пароль опционален, лучше ключ)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0
PULL_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --pull-only) PULL_ONLY=1 ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $arg"
      exit 1
      ;;
  esac
done

if [[ -f .env ]]; then
  # Подхватываем только BLUETERBIUM_SSH_* из .env
  TMP_ENV="$(mktemp)"
  grep -E '^[[:space:]]*BLUETERBIUM_SSH_[A-Z0-9_]+=' .env \
    | sed 's/^[[:space:]]*//' \
    | sed 's/[[:space:]]*#.*$//' > "$TMP_ENV" || true
  set -a
  # shellcheck disable=SC1090
  source "$TMP_ENV"
  set +a
  rm -f "$TMP_ENV"
fi

HOST="${BLUETERBIUM_SSH_HOST:-194.67.103.144}"
PORT="${BLUETERBIUM_SSH_PORT:-2222}"
USER_NAME="${BLUETERBIUM_SSH_USER:-root}"
PASSWORD="${BLUETERBIUM_SSH_PASSWORD:-}"
REMOTE_DIR="${REMOTE_DIR:-AI_Bot_avto_KP}"
APP_URL="http://${HOST}:5001"

CTRL_DIR="${TMPDIR:-/tmp}/ai_auogen_ssh_$$"
mkdir -p "$CTRL_DIR"
CTRL_PATH="${CTRL_DIR}/ctl"
cleanup_ssh() {
  ssh -O exit -o ControlPath="$CTRL_PATH" "${USER_NAME}@${HOST}" 2>/dev/null || true
  rm -rf "$CTRL_DIR"
}
trap cleanup_ssh EXIT

SSH_OPTS=(
  -p "$PORT"
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=20
  -o ServerAliveInterval=30
  -o ControlMaster=auto
  -o ControlPersist=120
  -o ControlPath="$CTRL_PATH"
)

SSH_BASE=(ssh "${SSH_OPTS[@]}")
RSYNC_SSH="ssh -p ${PORT} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o ControlMaster=auto -o ControlPersist=120 -o ControlPath=${CTRL_PATH}"

if [[ -n "$PASSWORD" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "В .env задан BLUETERBIUM_SSH_PASSWORD, но sshpass не установлен."
    echo "Установите: brew install hudochenkov/sshpass/sshpass"
    echo "Или настройте SSH-ключ и уберите пароль из .env."
    exit 1
  fi
  export SSHPASS="$PASSWORD"
  SSH_BASE=(sshpass -e ssh "${SSH_OPTS[@]}")
  RSYNC_SSH="sshpass -e ssh -p ${PORT} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o ControlMaster=auto -o ControlPersist=120 -o ControlPath=${CTRL_PATH}"
fi

REMOTE="${USER_NAME}@${HOST}"

echo "=== Деплой CRM на ${REMOTE}:${REMOTE_DIR} ==="
echo "URL: ${APP_URL}"
echo "SSH порт: ${PORT}"

remote() {
  local attempt=1
  local max=4
  while true; do
    if "${SSH_BASE[@]}" "$REMOTE" "$@"; then
      return 0
    fi
    local rc=$?
    if [[ $attempt -ge $max ]]; then
      echo "SSH ошибка после ${max} попыток (код $rc)."
      echo "Проверьте: ssh -p ${PORT} ${REMOTE}"
      echo "Если Connection refused — порт ${PORT} закрыт/sshd лежит, или IP в fail2ban."
      return "$rc"
    fi
    echo "SSH не удался (попытка ${attempt}/${max}), ждём $((attempt * 3))с..."
    sleep $((attempt * 3))
    attempt=$((attempt + 1))
  done
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] ssh ok?"
  remote "echo connected && ls -d ~/${REMOTE_DIR} 2>/dev/null || ls -d ~/*Bot* 2>/dev/null || true"
  exit 0
fi

echo "1/4 Проверка SSH (одно соединение)..."
REMOTE_PATH="$(remote "echo ok; if [ -d ~/${REMOTE_DIR} ]; then echo ~/${REMOTE_DIR}; elif [ -d ~/AI_Auogeneration ]; then echo ~/AI_Auogeneration; else echo ~/${REMOTE_DIR}; fi" | tr -d '\r' | tail -n 1)"
echo "Каталог на сервере: ${REMOTE_PATH}"

if [[ "$PULL_ONLY" -eq 1 ]]; then
  echo "2/4 git pull на сервере..."
  remote "cd '${REMOTE_PATH}' && git pull --ff-only"
else
  echo "2/4 Синхронизация файлов (rsync)..."
  if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync не найден — ставьте rsync или запускайте с --pull-only"
    exit 1
  fi
  rsync -az --delete \
    -e "$RSYNC_SSH" \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude 'deals.db' \
    --exclude 'deals.db.*' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    --exclude 'uploads/' \
    --exclude 'static/uploads/' \
    "${ROOT}/web_app/" \
    "${REMOTE}:${REMOTE_PATH}/web_app/"

  # КП «Стройка»: генератор, шаблоны PDF, шрифты WeasyPrint
  rsync -az \
    -e "$RSYNC_SSH" \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    "${ROOT}/utils/" \
    "${REMOTE}:${REMOTE_PATH}/utils/"

  rsync -az \
    -e "$RSYNC_SSH" \
    --exclude '.DS_Store' \
    "${ROOT}/templates/" \
    "${REMOTE}:${REMOTE_PATH}/templates/"

  if [[ -d "${ROOT}/fonts" ]]; then
    rsync -az \
      -e "$RSYNC_SSH" \
      --exclude '.DS_Store' \
      "${ROOT}/fonts/" \
      "${REMOTE}:${REMOTE_PATH}/fonts/"
  fi

  # Эталон и ключевые утилиты (если нужны на сервере рядом с CRM)
  rsync -az \
    -e "$RSYNC_SSH" \
    --exclude '.DS_Store' \
    "${ROOT}/knowledge_base/" \
    "${REMOTE}:${REMOTE_PATH}/knowledge_base/" 2>/dev/null || true

  # Telegram-бот (привязка chat_id + менеджерский FSM)
  rsync -az \
    -e "$RSYNC_SSH" \
    "${ROOT}/bot.py" \
    "${REMOTE}:${REMOTE_PATH}/bot.py"
fi

echo "3/4 Бэкап БД + перезапуск..."
remote bash -s << ENDSSH
set -euo pipefail
cd '${REMOTE_PATH}'

mkdir -p web_app/uploads logs

if [[ -f web_app/deals.db ]]; then
  stamp=\$(date +%Y%m%d_%H%M%S)
  cp web_app/deals.db "web_app/deals.db.backup_\${stamp}"
  echo "Бэкап: web_app/deals.db.backup_\${stamp}"
else
  echo "deals.db пока нет — пропускаем бэкап"
fi

echo "Останавливаем старый процесс..."
# Если стоят systemd-юниты — ими и управляем
if [[ -f /etc/systemd/system/dommaster-crm.service ]] || systemctl cat dommaster-crm.service >/dev/null 2>&1; then
  systemctl stop dommaster-bot.service 2>/dev/null || true
  systemctl stop dommaster-crm.service 2>/dev/null || true
fi
pkill -f 'python3 .*web_app/app.py' 2>/dev/null || true
pkill -f 'python3 app.py' 2>/dev/null || true
pkill -f 'python3 bot.py' 2>/dev/null || true
pkill -f 'python3 .*bot.py' 2>/dev/null || true
# Waitress остаётся тем же python3 app.py — даём порту освободиться
sleep 2
fuser -k 5001/tcp 2>/dev/null || true
sleep 1

# Зависимости CRM + КП (тихо, если уже стоят)
if command -v pip3 >/dev/null 2>&1; then
  pip3 install -q flask werkzeug python-docx PyPDF2 jinja2 weasyprint openai python-dotenv waitress async_timeout aiohttp aiogram httpx 'gigachat>=0.2.3,<0.3' 2>/dev/null || true
  python3 -c 'import gigachat' 2>/dev/null || echo "ВНИМАНИЕ: пакет gigachat не импортируется"
fi

# .env с OPENAI_API_KEY: корень репо или web_app
if [[ -f .env && ! -f web_app/.env ]]; then
  ln -sfn ../.env web_app/.env 2>/dev/null || cp -n .env web_app/.env 2>/dev/null || true
fi

# Telegram API с этого VPS: основной A-record часто недоступен.
# Пин рабочего DC (см. scripts/fix_telegram_access.sh).
grep -vE '[[:space:]]api\.telegram\.org([[:space:]]|\$)' /etc/hosts > /tmp/hosts.tg 2>/dev/null || true
if [[ ! -s /tmp/hosts.tg ]]; then
  printf '%s\n' \
    '127.0.0.1 localhost' \
    '127.0.1.1 cv7802191.novalocal cv7802191' \
    '::1 localhost ip6-localhost ip6-loopback' \
    'ff02::1 ip6-allnodes' \
    'ff02::2 ip6-allrouters' > /tmp/hosts.tg
fi
printf '%s api.telegram.org\n' '${TELEGRAM_API_IP:-149.154.167.220}' >> /tmp/hosts.tg
cat /tmp/hosts.tg > /etc/hosts
rm -f /tmp/hosts.tg
if [[ -f /etc/gai.conf ]] && ! grep -qE '^precedence ::ffff:0:0/96[[:space:]]+100' /etc/gai.conf; then
  echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
fi
echo "Telegram pin: \$(getent hosts api.telegram.org | head -1 || true)"

if [[ -f /etc/systemd/system/dommaster-crm.service ]] || systemctl cat dommaster-crm.service >/dev/null 2>&1; then
  echo "Запуск через systemd (dommaster-crm / dommaster-bot)..."
  systemctl daemon-reload
  systemctl restart dommaster-crm.service
  sleep 2
  systemctl restart dommaster-bot.service
  sleep 2
  if systemctl is-active --quiet dommaster-crm.service; then
    echo "CRM (systemd) активен"
  else
    echo "ВНИМАНИЕ: dommaster-crm не активен"
    systemctl --no-pager -l status dommaster-crm.service || true
    exit 1
  fi
  if systemctl is-active --quiet dommaster-bot.service; then
    echo "Бот (systemd) активен"
  else
    echo "ВНИМАНИЕ: dommaster-bot не активен"
    systemctl --no-pager -l status dommaster-bot.service || true
  fi
else
  echo "Запуск web_app/app.py (nohup)..."
  cd web_app
  mkdir -p ../reports/kp/stroika ../logs
  nohup python3 app.py > ../logs/app.log 2>&1 &
  sleep 2

  if pgrep -f 'python3 app.py' >/dev/null 2>&1 || pgrep -f 'web_app/app.py' >/dev/null 2>&1; then
    echo "Процесс CRM запущен"
  else
    echo "ВНИМАНИЕ: процесс не найден, смотрите logs/app.log"
    tail -n 40 ../logs/app.log || true
    exit 1
  fi

  cd '${REMOTE_PATH}'
  if [[ -f bot.py ]]; then
    echo "Перезапуск Telegram-бота..."
    pkill -f 'python3 bot.py' 2>/dev/null || true
    pkill -f 'python3 .*bot.py' 2>/dev/null || true
    sleep 1
    nohup python3 bot.py >> logs/bot.log 2>&1 &
    sleep 2
    if pgrep -f 'python3 bot.py' >/dev/null 2>&1; then
      echo "Бот запущен"
    else
      echo "ВНИМАНИЕ: бот не стартовал, смотрите logs/bot.log"
      tail -n 30 logs/bot.log || true
    fi
  fi
fi
ENDSSH

echo "4/4 Проверка /health..."
if curl -fsS --max-time 8 "${APP_URL}/health" >/dev/null 2>&1; then
  echo "health OK → ${APP_URL}/health"
else
  echo "health пока не ответил (firewall/порт/старт). Проверьте: ${APP_URL}"
fi

echo ""
echo "Деплой завершён."
echo "CRM: ${APP_URL}/deals/"
echo "Логи на сервере: ${REMOTE_PATH}/logs/app.log"
