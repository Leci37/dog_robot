#!/bin/bash
# Vigilante de LUIS LECI control: cada minuto comprueba lo importante y lo arregla solo.
# Pensado para la casa sin router ni forma fácil de comprobar nada. Registro: ~/spotmicro/vigilante.log
# Servicio spotmicro-vigilante (oneshot, root) lanzado por spotmicro-vigilante.timer.
LOG=/home/luislecinana/spotmicro/vigilante.log
STATE=/run/spotmicro-vigilante
mkdir -p "$STATE"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
active() { systemctl is-active --quiet "$1"; }

# 1. Red: con cable conectado, la dirección fija del cable directo tiene que estar.
if [ "$(cat /sys/class/net/eth0/carrier 2>/dev/null)" = "1" ] && ! ip -4 addr show eth0 | grep -q '169\.254\.42\.1/'; then
  if nmcli -t -f NAME con show | grep -qx spotmicro-eth0; then
    nmcli con up spotmicro-eth0 >/dev/null 2>&1 && log "red: faltaba 169.254.42.1 -> reactivado el perfil spotmicro-eth0"
  else
    nmcli con add type ethernet ifname eth0 con-name spotmicro-eth0 \
      connection.autoconnect yes connection.autoconnect-priority 100 connection.autoconnect-retries 0 \
      ipv4.method auto ipv4.addresses 169.254.42.1/16 ipv4.may-fail yes ipv4.dhcp-timeout 2147483647 \
      ipv6.method link-local ipv6.addr-gen-mode eui64 ipv6.may-fail yes >/dev/null 2>&1 \
      && nmcli con up spotmicro-eth0 >/dev/null 2>&1 && log "red: no existía el perfil -> recreado spotmicro-eth0"
  fi
  sleep 8
  if ! ip -4 addr show eth0 | grep -q '169\.254\.42\.1/'; then
    ip addr add 169.254.42.1/16 dev eth0 2>/dev/null && log "red: 169.254.42.1 puesta a mano (NetworkManager no lo hizo)"
  fi
fi

# 2. Web del robot: si no responde dos veces seguidas, reiniciarla.
if curl -s -m 6 -o /dev/null http://127.0.0.1:8080/api/state; then
  rm -f "$STATE/web_fallos"
else
  n=$(( $(cat "$STATE/web_fallos" 2>/dev/null || echo 0) + 1 ))
  echo $n > "$STATE/web_fallos"
  if [ $n -ge 2 ]; then
    systemctl restart spotmicro-web && log "web: no respondía ($n veces) -> reiniciada"
    rm -f "$STATE/web_fallos"
  fi
fi

# 3. Pantalla HDMI: en tty1 siempre tiene que haber algo (el panel o, si falla, la consola normal).
if ! active spotmicro-pantalla.service && ! active getty@tty1.service; then
  if systemctl is-failed --quiet spotmicro-pantalla.service; then
    systemctl reset-failed spotmicro-pantalla.service
    n=$(( $(cat "$STATE/panel_fallos" 2>/dev/null || echo 0) + 1 ))
    echo $n > "$STATE/panel_fallos"
    if [ $n -ge 3 ]; then
      systemctl start getty@tty1.service && log "pantalla: el panel falla una y otra vez -> consola normal en la tele"
    else
      systemctl start spotmicro-pantalla.service && log "pantalla: el panel había fallado -> arrancado otra vez ($n)"
    fi
  else
    systemctl start spotmicro-pantalla.service && log "pantalla: no había nada en tty1 -> panel arrancado"
  fi
fi

# 4. LED de vida.
active spotmicro-led.service || { systemctl restart spotmicro-led.service && log "led: estaba parado -> reiniciado"; }

# 5. Espacio en la SD: si queda poco, recortar el journal.
free_kb=$(df --output=avail / | tail -n 1)
if [ "${free_kb:-999999}" -lt 300000 ]; then
  journalctl --vacuum-size=8M >/dev/null 2>&1 && log "disco: quedaban ${free_kb} KB -> journal recortado"
fi

# El registro no crece sin límite.
[ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 200000 ] && tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit 0
