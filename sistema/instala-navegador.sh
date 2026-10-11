#!/bin/sh
# Instala el navegador kiosco de la pantalla HDMI (cage + chromium). Reanudable: apt reaprovecha lo descargado.
# Se lanza desde la web (Sistema > Pantalla HDMI > Instalar navegador) o a mano:
#   sudo systemd-run --unit=spotmicro-instala-navegador ~/spotmicro/sistema/instala-navegador.sh
LOG=/var/log/instala-navegador.log
{
  echo "inicio $(date)"
  dpkg --configure -a
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
    cage chromium libgl1-mesa-dri libegl-mesa0 fonts-dejavu-core
  echo "FIN-CODIGO-$? $(date)"
  sync
} > "$LOG" 2>&1
