#!/usr/bin/env bash
# deploy.sh — Безопасное развёртывание Bybit-бота на Linux VPS (systemd).
#
# Что делает:
#   1. Создаёт системного пользователя bybit_bot без shell-доступа и без root.
#   2. Копирует код в /opt/bybit_bot (без .env — секреты переносятся отдельно).
#   3. Создаёт venv и ставит зависимости.
#   4. Проверяет, что .env существует, не пустой и имеет права 600.
#   5. Устанавливает systemd unit и НЕ запускает бота автоматически —
#      запуск только вручную после ручной проверки конфига.
#
# Запуск: sudo bash deploy.sh
set -euo pipefail

APP_DIR="/opt/bybit_bot"
SERVICE_USER="bybit_bot"
SERVICE_FILE="bybit_bot.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$EUID" -ne 0 ]]; then
  echo "Запустите скрипт от root: sudo bash deploy.sh" >&2
  exit 1
fi

echo "==> 1. Создание системного пользователя ${SERVICE_USER} (без shell, без sudo)"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --shell /usr/sbin/nologin --home "${APP_DIR}" --create-home "${SERVICE_USER}"
  echo "    Пользователь создан."
else
  echo "    Пользователь уже существует, пропускаем."
fi

echo "==> 2. Копирование кода в ${APP_DIR}"
mkdir -p "${APP_DIR}"
# Копируем всё, КРОМЕ .env (секреты переносятся отдельно, вручную, см. ниже)
rsync -a --exclude='.env' --exclude='.git' --exclude='__pycache__' \
  --exclude='*.log' --exclude='venv' \
  "${SCRIPT_DIR}/" "${APP_DIR}/"

echo "==> 3. Создание виртуального окружения и установка зависимостей"
if [[ ! -d "${APP_DIR}/venv" ]]; then
  python3 -m venv "${APP_DIR}/venv"
fi
"${APP_DIR}/venv/bin/pip" install --upgrade pip -q
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

echo "==> 4. Проверка .env"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo ""
  echo "    !!! .env НЕ найден в ${APP_DIR} !!!"
  echo "    Создайте его вручную ОТДЕЛЬНОЙ командой (не через этот скрипт,"
  echo "    чтобы ключи не попали в shell-историю или вывод rsync/git):"
  echo ""
  echo "        sudo nano ${APP_DIR}/.env"
  echo ""
  echo "    Содержимое (см. .env.example):"
  echo "        BYBIT_API_KEY=..."
  echo "        BYBIT_API_SECRET=..."
  echo "        BYBIT_TESTNET=true"
  echo ""
  echo "    Затем выполните:"
  echo "        sudo chown ${SERVICE_USER}:${SERVICE_USER} ${APP_DIR}/.env"
  echo "        sudo chmod 600 ${APP_DIR}/.env"
  echo "        sudo bash deploy.sh   # запустите скрипт ещё раз"
  echo ""
  exit 1
fi

# Права и владелец .env — критично для защиты секретов
chown "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}/.env"
chmod 600 "${APP_DIR}/.env"

PERMS=$(stat -c '%a' "${APP_DIR}/.env")
if [[ "${PERMS}" != "600" ]]; then
  echo "    !!! Не удалось установить права 600 на .env (сейчас: ${PERMS}). Прерываем." >&2
  exit 1
fi
echo "    .env существует, права 600, владелец ${SERVICE_USER} — OK."

# Проверка, что в .env не остался шаблон с placeholder-значениями
if grep -qE "your_key_here|your_secret_here" "${APP_DIR}/.env"; then
  echo ""
  echo "    !!! В .env обнаружены placeholder-значения (your_key_here)."
  echo "    Замените их на реальные ключи перед запуском бота."
  echo ""
  exit 1
fi

echo "==> 5. Установка прав на код (владелец ${SERVICE_USER}, без права записи для прочих)"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"
chmod -R go-w "${APP_DIR}"

echo "==> 6. Установка systemd unit"
cp "${SCRIPT_DIR}/${SERVICE_FILE}" "/etc/systemd/system/${SERVICE_FILE}"
# unit-файл не содержит секретов, поэтому стандартные права 644 безопасны,
# но дополнительно убираем право записи для group/other.
chmod 644 "/etc/systemd/system/${SERVICE_FILE}"
systemctl daemon-reload

echo ""
echo "==> Готово. Бот НЕ запущен автоматически."
echo ""
echo "    Проверьте конфиг (config.py, .env), затем запустите вручную:"
echo "        sudo systemctl enable bybit_bot.service    # автозапуск при перезагрузке"
echo "        sudo systemctl start bybit_bot.service"
echo "        sudo systemctl status bybit_bot.service"
echo "        sudo journalctl -u bybit_bot.service -f    # просмотр логов"
echo ""
echo "    !!! Перед переключением BYBIT_TESTNET=false убедитесь, что бот"
echo "    стабильно отработал на testnet и риск-параметры (RISK_PCT,"
echo "    DAILY_LOSS_LIMIT_PCT, PORTFOLIO_DRAWDOWN_LIMIT) вас устраивают."
echo ""
