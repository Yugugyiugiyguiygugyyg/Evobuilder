#!/bin/bash
CONFIG_FILE="$HOME/.rom_build_config"
STATE_FILE="$HOME/.rom_build_state"
LOG_FILE="$HOME/build_full.log"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Сохранение аргументов
if [ "$#" -eq 2 ]; then
    echo "export TG_TOKEN=\"$1\"" > "$CONFIG_FILE"
    echo "export TG_CHAT_ID=\"$2\"" >> "$CONFIG_FILE"
fi

if [ -f "$CONFIG_FILE" ]; then source "$CONFIG_FILE"
else echo "Ошибка: Нет токенов! Использование: bash script.sh <TOKEN> <CHAT_ID>"; exit 1; fi

set -eEu -o pipefail
# Если скрипт упадет, отправляем сигнал ошибки демону
trap 'curl -s -X POST "http://localhost:8080/api/error" -d "Ошибка на строке $LINENO" || true' ERR

if [ ! -f "$STATE_FILE" ]; then
    echo "=== ЭТАП 1: Установка зависимостей ==="
    sudo apt update && sudo apt install git aria2 python3-pip python3-requests tmux systemd-zram-generator curl -y

    # Настройка ZRAM и SSH
    echo -e "[zram0]\ncompression-algorithm = zstd\nzram-fraction = 1\nmax-zram-size = 16384" | sudo tee /etc/systemd/zram-generator.conf > /dev/null
    echo "ClientAliveInterval 60" | sudo tee -a /etc/ssh/sshd_config > /dev/null

    echo "STAGE_2" > "$STATE_FILE"
    # Автозапуск после ребута
    (crontab -l 2>/dev/null || true; echo "@reboot sleep 20 && tmux new-session -d -s rombuild 'bash $DIR/build_manager.sh >> $LOG_FILE 2>&1'") | crontab -
    sudo reboot
else
    echo "=== ЭТАП 2: Сборка ==="
    (crontab -l 2>/dev/null || true) | grep -v 'build_manager.sh' | crontab -
    rm -f "$STATE_FILE"
    touch "$LOG_FILE"

    # 1. ЗАПУСК ДЕМОНА В ФОНЕ с максимальным приоритетом
    # (pkill убивает старые процессы, если они зависли)
    pkill -f daemon.py || true
    sudo nice -n -10 python3 "$DIR/daemon.py" &
    sleep 3 # Ждем, пока веб-сервер поднимется

    # 2. ПОДГОТОВКА ИСХОДНИКОВ
    curl -s -X POST "http://localhost:8080/api/stage" -d "Инициализация репозиториев"
    mkdir -p ~/evox && cd ~/evox
    git config --global user.email "you@example.com" && git config --global user.name "A4" && git lfs install
    yes "" | repo init -u https://github.com/Evolution-X/manifest -b bq2 --git-lfs --depth=1 2>&1 | tee -a "$LOG_FILE"
    
    rm -rf .repo/local_manifests
    git clone https://github.com/Yugugyiugiyguiygugyyg/bale_manifest.git .repo/local_manifests -b lineage-23.2 2>&1 | tee -a "$LOG_FILE"
    
    # 3. СИНХРОНИЗАЦИЯ
    curl -s -X POST "http://localhost:8080/api/stage" -d "Синхронизация (Repo Sync)"
    repo sync --force-sync --optimized-fetch --no-tags --no-clone-bundle --prune -j4 2>&1 | tee -a "$LOG_FILE"

    # 4. НАСТРОЙКА КЭША
    curl -s -X POST "http://localhost:8080/api/stage" -d "Настройка окружения (ccache/breakfast)"
    source build/envsetup.sh
    sudo mkdir -p /mnt/ccache && mkdir -p ~/.cache/ccache
    if ! mountpoint -q /mnt/ccache; then sudo mount --bind $HOME/.cache/ccache /mnt/ccache; fi
    export USE_CCACHE=1 && export CCACHE_EXEC=/usr/bin/ccache && export CCACHE_DIR=/mnt/ccache
    ccache -M 70G -F 0

    breakfast lineage_bale_GAPPS-userdebug 2>&1 | tee -a "$LOG_FILE"

    # --- 5. ВАЖНО: ЗАМОРОЗКА СКРИПТА, ЖДЕМ ОТВЕТА ОТ ВЕБ-UI ИЛИ ТГ ---
    echo "Ожидание подтверждения от пользователя..."
    ANS=$(curl -s "http://localhost:8080/api/wait_confirm")

    if [ "$ANS" == "yes" ]; then
        # 6. ФИНАЛЬНАЯ СБОРКА
        curl -s -X POST "http://localhost:8080/api/stage" -d "Сборка прошивки (m evolution)"
        m evolution -j$(nproc) 2>&1 | tee -a "$LOG_FILE"
        curl -s -X POST "http://localhost:8080/api/success"
    else
        echo "Сборка отменена."
        curl -s -X POST "http://localhost:8080/api/error" -d "Сборка отменена пользователем"
        exit 0
    fi
fi
