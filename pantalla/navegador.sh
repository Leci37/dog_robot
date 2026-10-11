#!/bin/sh
# Abre la web del robot a pantalla completa en el HDMI (lo lanza panel.py dentro de su sesión de tty1).
# Perfil del navegador en /tmp (RAM): no escribe en la SD y no se corrompe con los cortes de corriente.
PERFIL=/tmp/spotmicro-kiosko
mkdir -p "$PERFIL"
exec cage -s -- chromium \
  --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble \
  --disable-gpu --disable-background-networking --disable-sync --disable-component-update \
  --disable-features=Translate,MediaRouter --password-store=basic --ozone-platform=wayland \
  --user-data-dir="$PERFIL" --disk-cache-dir="$PERFIL/cache" --check-for-update-interval=31536000 \
  "http://127.0.0.1:8080/lite"
