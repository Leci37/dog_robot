#!/bin/bash
# Prueba de red del SpotMicro (se ejecuta como root con systemd-run; deshace todo si algo va mal).
LOG=/home/luislecinana/spotmicro/net_test.log
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') prueba de red"
OLD=$(nmcli -g NAME,DEVICE con show --active | grep ':eth0$' | cut -d: -f1)
echo "perfil anterior: $OLD"
st() { echo "  $(date +%T) estado=$(nmcli -g GENERAL.STATE con show spotmicro-eth0 2>/dev/null) | $(ip -br addr show eth0 | tr -s ' ')"; }

if ! nmcli con show spotmicro-eth0 >/dev/null 2>&1; then
  nmcli con add type ethernet ifname eth0 con-name spotmicro-eth0 \
    connection.autoconnect yes connection.autoconnect-priority 100 connection.autoconnect-retries 0 \
    ipv4.method auto ipv4.addresses 169.254.42.1/16 ipv4.may-fail yes ipv4.dhcp-timeout 2147483647 \
    ipv6.method link-local ipv6.addr-gen-mode eui64 ipv6.may-fail yes || { echo "FALLO al crear el perfil"; exit 1; }
fi
nmcli -f connection.id,ipv4.method,ipv4.addresses,ipv4.dhcp-timeout,ipv6.method,ipv6.addr-gen-mode con show spotmicro-eth0

echo "-- fase 1: activar el perfil nuevo con el router"
if ! nmcli -w 40 con up spotmicro-eth0; then
  echo "FALLO al activar; vuelvo a $OLD"; nmcli con up "$OLD"; exit 1
fi
sleep 10; st; ip route show default

echo "-- fase 2: simular que no hay router (bloqueo las respuestas DHCP que entran por eth0)"
tc qdisc add dev eth0 ingress
tc filter add dev eth0 parent ffff: protocol ip prio 1 u32 match ip protocol 17 0xff match ip sport 67 0xffff action drop
nmcli -w 15 con up spotmicro-eth0
for i in 1 2 3 4 5 6 7 8 9; do sleep 10; st; done
echo "  web en 169.254.42.1 (local): $(curl -s -m 3 -o /dev/null -w '%{http_code}' http://169.254.42.1/)"
echo "  ruta por defecto: $(ip route show default | head -n1)"

echo "-- fase 3: restaurar (quito el bloqueo y vuelvo a pedir IP al router)"
tc qdisc del dev eth0 ingress
nmcli -w 40 con up spotmicro-eth0
sleep 10; st; ip route show default
if ! ip -4 addr show eth0 | grep -q '192\.168\.8\.'; then
  echo "SIN IP DEL ROUTER: vuelvo al perfil anterior $OLD"; nmcli con up "$OLD"; sleep 10; st
fi
echo "=== fin $(date '+%F %T')"
