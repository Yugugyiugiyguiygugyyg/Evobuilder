#!/bin/bash
STATE_FILE="$HOME/.rom_build_state"
LOG_FILE="$HOME/build_full.log"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# МАГИЯ: Заворачиваем ВЕСЬ вывод скрипта (и успешный, и ошибки) в лог-файл
exec > >(tee -a "$LOG_FILE") 2>&1

# Строгий режим отлова ошибок
set -eEu -o pipefail

# Если возникает критическая ошибка, отправляем её на сайт
trap 'curl -s -X POST "http://localhost:8080/api/error" -d "Сбой скрипта на строке $LINENO" || true' ERR

if [ ! -f "$STATE_FILE" ]; then
    echo "=== ЭТАП 1: Установка зависимостей (Ubuntu 24.04) ==="
    export DEBIAN_FRONTEND=noninteractive
    
    sudo apt update
    sudo apt install -y git git-lfs aria2 python3 tmux systemd-zram-generator curl cron

    echo -e "[zram0]\ncompression-algorithm = zstd\nzram-fraction = 1\nmax-zram-size = 16384" | sudo tee /etc/systemd/zram-generator.conf > /dev/null
    echo "ClientAliveInterval 60" | sudo tee -a /etc/ssh/sshd_config > /dev/null

    sudo systemctl enable cron
    sudo systemctl start cron

    (crontab -l 2>/dev/null | grep -v 'build.sh' || true; echo "@reboot sleep 30 && cd $DIR && /usr/bin/tmux new-session -d -s evobuild '/bin/bash $DIR/build.sh'") | crontab -

    echo "STAGE_2" > "$STATE_FILE"
    echo "Перезагружаю сервер... После включения скрипт сам продолжит работу!"
    sudo reboot
else
    echo "=== ЭТАП 2: Сборка ==="
    # Больше не удаляем метку, чтобы избежать бесконечных ребутов
    touch "$LOG_FILE"

    # 1. ЗАПУСК ДЕМОНА WEB-UI
    sudo pkill -f web_daemon.py || true
    sudo nice -n -10 python3 "$DIR/web_daemon.py" &
    sleep 3

    # 2. ПОДГОТОВКА ИСХОДНИКОВ
    curl -s -X POST "http://localhost:8080/api/stage" -d "Инициализация репозиториев"
    mkdir -p ~/evox && cd ~/evox
    git config --global user.email "you@example.com" && git config --global user.name "A4" && git lfs install
    
    # Отключаем цветной вывод repo, чтобы он не засорял сайт спецсимволами
    yes "" | repo init -u https://github.com/Evolution-X/manifest -b bq2 --git-lfs --depth=1 --color=never || true
    
    rm -rf .repo/local_manifests
    # ТУТ ДОЛЖНА БЫТЬ ТВОЯ НАСТОЯЩАЯ ССЫЛКА НА ЛОКАЛЬНЫЕ МАНИФЕСТЫ:
    git clone https://github.com/Yugugyiugiyguiygugyyg/bale_manifest.git .repo/local_manifests -b lineage-23.2
    
    # 3. СИНХРОНИЗАЦИЯ
    curl -s -X POST "http://localhost:8080/api/stage" -d "Синхронизация (Repo Sync)"
    # Убрали -q (quiet), чтобы он выводил каждый шаг загрузки
    repo sync --force-sync --optimized-fetch --no-tags --no-clone-bundle --prune -j4

    # 4. НАСТРОЙКА КЭША И УСТРОЙСТВА
    curl -s -X POST "http://localhost:8080/api/stage" -d "Настройка окружения (ccache/breakfast)"
    source build/envsetup.sh
    sudo mkdir -p /mnt/ccache && mkdir -p ~/.cache/ccache
    if ! mountpoint -q /mnt/ccache; then sudo mount --bind $HOME/.cache/ccache /mnt/ccache; fi
    export USE_CCACHE=1 && export CCACHE_EXEC=/usr/bin/ccache && export CCACHE_DIR=/mnt/ccache
    ccache -M 70G -F 0

    breakfast lineage_bale_GAPPS-userdebug

    # 5. ОЖИДАНИЕ КНОПКИ ИЗ БРАУЗЕРА
    echo "Ожидание подтверждения от пользователя через Web-интерфейс..."
    ANS=$(curl -s "http://localhost:8080/api/wait_confirm")

    if [ "$ANS" == "yes" ]; then
        # 6. ФИНАЛЬНАЯ СБОРКА
        curl -s -X POST "http://localhost:8080/api/stage" -d "Сборка прошивки (m evolution)"
        m evolution -j$(nproc)
        curl -s -X POST "http://localhost:8080/api/success"
    else
        echo "Сборка отменена."
        curl -s -X POST "http://localhost:8080/api/error" -d "Отменено пользователем"
        exit 0
    fi
fi
