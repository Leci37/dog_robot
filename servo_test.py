#!/home/luislecinana/robot/bin/python
"""Prueba prudente de UN canal de la PCA9685.

Uso:
  servo_test.py CANAL [--center 90] [--delta 10] [--hold]
  servo_test.py --off            # suelta todos los canales (PWM a 0)

Qué hace con CANAL:
  1. Lo lleva a --center grados (por defecto 90 = 1500 us).
     OJO: el primer pulso es absoluto; el servo salta desde donde esté hasta ese ángulo.
  2. Lo mueve +delta, -delta y vuelve al centro, despacio.
  3. Lo suelta (sin par) salvo que se pase --hold.
Robot SIEMPRE en el aire y con fuente de servos adecuada en V+ (nunca la Pi).
"""
import argparse
import sys
import time

from adafruit_servokit import ServoKit

MIN_US, MAX_US = 500, 2500   # MG996R aprox. 0-180 grados
SAFE_MIN, SAFE_MAX = 30, 150  # límites duros de este script
MAX_DELTA = 20


def release(kit, ch):
    kit.servo[ch].angle = None  # duty 0: el servo queda suelto


def sweep(kit, ch, start, end, step=1, pause=0.03):
    rng = range(start, end + (1 if end >= start else -1), step if end >= start else -step)
    for a in rng:
        kit.servo[ch].angle = a
        time.sleep(pause)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("channel", nargs="?", type=int)
    p.add_argument("--center", type=int, default=90)
    p.add_argument("--delta", type=int, default=10)
    p.add_argument("--hold", action="store_true")
    p.add_argument("--off", action="store_true")
    a = p.parse_args()

    kit = ServoKit(channels=16)
    for ch in range(16):
        kit.servo[ch].set_pulse_width_range(MIN_US, MAX_US)

    if a.off:
        for ch in range(16):
            release(kit, ch)
        print("todos los canales sueltos")
        return

    if a.channel is None or not 0 <= a.channel <= 15:
        sys.exit("indica un canal 0-15")
    delta = min(abs(a.delta), MAX_DELTA)
    lo, hi = a.center - delta, a.center + delta
    if lo < SAFE_MIN or hi > SAFE_MAX:
        sys.exit(f"fuera de límites seguros {SAFE_MIN}-{SAFE_MAX}")

    ch = a.channel
    print(f"canal {ch}: centro {a.center}, +/-{delta} grados")
    try:
        kit.servo[ch].angle = a.center
        time.sleep(0.8)
        sweep(kit, ch, a.center, hi)
        time.sleep(0.4)
        sweep(kit, ch, hi, lo)
        time.sleep(0.4)
        sweep(kit, ch, lo, a.center)
        time.sleep(0.4)
    finally:
        if not a.hold:
            release(kit, ch)
            print("canal soltado")
        else:
            print(f"canal {ch} sujeto en {a.center}")


if __name__ == "__main__":
    main()
