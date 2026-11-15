#!/data/data/com.termux/files/usr/bin/bash

set -e

# Загружаем настройки
source "$HOME/.config/nexus/secrets.env"
source "$HOME/.config/nexus/gemini.env"

# Проверенная модель
export GEMINI_MODEL="gemini-3.1-flash-lite"

# Проверяем ключ
if [ -z "${GEMINI_API_KEY:-}" ]; then
    echo "ERROR: Gemini API key missing"
    exit 1
fi

echo "NEXUS AI starting..."
echo "Model: $GEMINI_MODEL"

cd "$HOME/nexus-ai/android_bridge"

exec python supervisor.py
