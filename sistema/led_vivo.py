#!/usr/bin/python3
"""LED verde (ACT) de la Raspberry Pi como señal de vida de LUIS LECI control.

  corto, corto, largo (· · —) en bucle  -> todo bien: la web del robot responde
  parpadeo rápido continuo              -> el programa no responde (arrancando o caído)
  parpadeo irregular (actividad SD)     -> la Pi está arrancando o apagándose (comportamiento normal)
  quieto (encendido o apagado)          -> colgada: el watchdog la reiniciará en ~1 minuto
  apagado del todo                      -> sin corriente

Servicio systemd: spotmicro-led (como root, para poder escribir en /sys/class/leds).
Al pararse (apagado, reinicio) devuelve el LED a su modo normal.
"""
import signal
import sys
import time
import urllib.request
from pathlib import Path

LED = Path("/sys/class/leds/ACT")
CHECK_URL = "http://127.0.0.1:8080/api/state"
SHORT, LONG, GAP, PAUSE = 0.15, 0.6, 0.15, 0.9
NORMAL_TRIGGER = "mmc0"


def set_led(on):
    (LED / "brightness").write_text("1" if on else "0")


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


def blink(on_s, off_s):
    set_led(True)
    time.sleep(on_s)
    set_led(False)
    time.sleep(off_s)


def main():
    signal.signal(signal.SIGTERM, restore)
    signal.signal(signal.SIGINT, restore)
    (LED / "trigger").write_text("none")
    while True:
        if web_ok():
            blink(SHORT, GAP)
            blink(SHORT, GAP)
            blink(LONG, PAUSE)
        else:
            for _ in range(10):  # ~1,6 s de parpadeo rápido y vuelve a comprobar
                blink(0.08, 0.08)


if __name__ == "__main__":
    main()
