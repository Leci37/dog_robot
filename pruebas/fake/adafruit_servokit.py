"""Simulador de adafruit_servokit para probar el servidor sin tocar la placa de servos real.

Apunta cada ángulo enviado en $FAKE_LOG (canal ángulo) para comprobar luego los límites.
"""
import os

_LOG = os.environ.get("FAKE_LOG", "/tmp/fake_angles.log")


class _Servo:
    def __init__(self, ch):
        self.ch = ch
        self._a = None

    def set_pulse_width_range(self, lo, hi):
        pass

    @property
    def angle(self):
        return self._a

    @angle.setter
    def angle(self, v):
        if v is not None and not 0 <= v <= 180:
            raise ValueError(f"ángulo fuera de 0-180: {v}")
        self._a = v
        with open(_LOG, "a") as f:
            f.write(f"{self.ch} {v}\n")


class _Channel:
    duty_cycle = 0


class _PCA:
    frequency = 50

    def __init__(self):
        self.channels = [_Channel() for _ in range(16)]


class ServoKit:
    def __init__(self, channels=16, **kw):
        self.servo = [_Servo(i) for i in range(channels)]
        self._pca = _PCA()
