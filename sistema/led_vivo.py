#!/usr/bin/python3
"""LED verde (ACT) de la Raspberry Pi como señal de vida de LUIS LECI control.

  corto, corto, largo (· · —) en bucle  -> todo bien: la web del robot responde
  parpadeo rápido continuo              -> el programa no responde (arrancando o caído)
  parpadeo irregular (actividad SD)     -> la Pi está arrancando o apagándose (comportamiento normal)
  quieto (encendido o apagado)          -> colgada: el watchdog la reiniciará en ~1 minuto
  apagado del todo                      -> sin corriente

El patrón va sincronizado con la hora: el LED de vida de la placa de servos (un LED en un canal libre de la
PCA9685, lo mueve web/server.py con el mismo LED_PATTERN) parpadea exactamente a la vez.
Servicio systemd: spotmicro-led (como root, para poder escribir en /sys/class/leds).
Al pararse (apagado, reinicio) devuelve el LED a su modo normal.
"""
import signal
import sys
import time
import urllib.request
from pathlib import Path

LED = Path("/sys/class/leds/ACT")
CHECK_URL = "http://127.0.0.1:8080/api/consumo"
LED_PATTERN = [(1, 0.15), (0, 0.15), (1, 0.15), (0, 0.15), (1, 0.6), (0, 0.9)]  # igual que en server.py
LED_PERIOD = sum(d for _, d in LED_PATTERN)
FAST = 0.08
NORMAL_TRIGGER = "mmc0"


def phase_on(t):
    t %= LED_PERIOD
    for on, d in LED_PATTERN:
        if t < d:
            return bool(on)
        t -= d
    return False


def restore(*_):
    try:
        (LED / "trigger").write_text(NORMAL_TRIGGER)
    except OSError:
        pass
    sys.exit(0)


def web_ok():
    try:
        with urllib.request.urlopen(CHECK_URL, timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def main():
    signal.signal(signal.SIGTERM, restore)
    signal.signal(signal.SIGINT, restore)
    (LED / "trigger").write_text("none")
    ok, checked, last = True, 0.0, None
    while True:
        now = time.time()
        if now - checked > 4:
            ok, checked = web_ok(), now
        on = phase_on(now) if ok else int(now / FAST) % 2 == 0
        if on != last:
            (LED / "brightness").write_text("1" if on else "0")
            last = on
        time.sleep(0.01)


if __name__ == "__main__":
    main()
