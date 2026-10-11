#!/home/luislecinana/robot/bin/python
"""Web de mapeo de servos del SpotMicro.

http://<ip-de-la-pi>:8080  ·  servicio systemd: spotmicro-web
Guarda el mapa en ../servo_map.json y deja un log de acciones en acciones.log.
"""
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from adafruit_servokit import ServoKit

WEB = Path(__file__).resolve().parent
MAP_FILE = WEB.parent / "servo_map.json"
LOG_FILE = WEB / "acciones.log"
PORT = 8080

MIN_US, MAX_US = 500, 2500   # MG996R aprox. 0-180 grados
SAFE_MIN, SAFE_MAX = 30, 150
MAX_DELTA = 20
CENTER = 90
LEGS = ["A", "B", "C", "D"]
JOINTS = ["hombro", "muslo", "rodilla"]

kit = ServoKit(channels=16)
for _ch in range(16):
    kit.servo[_ch].set_pulse_width_range(MIN_US, MAX_US)
lock = threading.Lock()
angles = {ch: None for ch in range(16)}  # último ángulo mandado; None = suelto


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


def load_map():
    try:
        data = json.loads(MAP_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if "canales" not in data:
        data = {
            "_nota": "Canal PCA9685 -> pata y articulación. Vista desde arriba, cabeza delante: "
                     "A delantera izq, B delantera der, C trasera der, D trasera izq. "
                     "Articulaciones: hombro (abre/cierra al lado), muslo (adelante/atrás), rodilla.",
            "canales": {},
        }
    return data


def save_map(data):
    tmp = MAP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(MAP_FILE)


def clamp(a):
    return max(SAFE_MIN, min(SAFE_MAX, int(a)))


def move_to(ch, target, pause=0.03):
    target = clamp(target)
    cur = angles[ch]
    if cur is None:
        kit.servo[ch].angle = target  # primer pulso: salto absoluto
        time.sleep(0.6)
    else:
        step = 1 if target >= cur else -1
        for a in range(cur, target + step, step):
            kit.servo[ch].angle = a
            time.sleep(pause)
    angles[ch] = target


def release(ch):
    kit.servo[ch].angle = None
    angles[ch] = None


def check_ch(ch):
    ch = int(ch)
    if not 0 <= ch <= 15:
        raise ValueError("canal fuera de rango 0-15")
    return ch


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass

    def state(self):
        return {"angles": angles, "map": load_map()["canales"],
                "limits": {"min": SAFE_MIN, "max": SAFE_MAX, "max_delta": MAX_DELTA}}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, self.state())
        else:
            self._send(404, {"error": "no existe"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            with lock:
                self.route(body)
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log(f"ERROR {self.path}: {e!r}")
            self._send(500, {"error": repr(e)})

    def route(self, b):
        p = self.path
        if p == "/api/test":
            ch = check_ch(b["ch"])
            d = min(abs(int(b.get("delta", 10))), MAX_DELTA)
            move_to(ch, CENTER)
            time.sleep(0.5)
            move_to(ch, CENTER + d)
            time.sleep(0.3)
            move_to(ch, CENTER - d)
            time.sleep(0.3)
            move_to(ch, CENTER)
            if not b.get("hold", True):
                release(ch)
            log(f"probar canal {ch} +/-{d} hold={b.get('hold', True)}")
        elif p == "/api/angle":
            ch = check_ch(b["ch"])
            move_to(ch, b["angle"])
            log(f"canal {ch} -> {angles[ch]}")
        elif p == "/api/release":
            ch = check_ch(b["ch"])
            release(ch)
            log(f"soltar canal {ch}")
        elif p == "/api/off":
            for ch in range(16):
                release(ch)
            log("SOLTAR TODO")
        elif p == "/api/map":
            ch = check_ch(b["ch"])
            data = load_map()
            key = str(ch)
            if b.get("clear"):
                data["canales"].pop(key, None)
                log(f"mapa: canal {ch} borrado")
            elif b.get("libre"):
                data["canales"][key] = {"libre": True}
                log(f"mapa: canal {ch} = libre (no se mueve)")
            else:
                leg, joint = b["pata"], b["articulacion"]
                if leg not in LEGS or joint not in JOINTS:
                    raise ValueError("pata o articulación no válida")
                data["canales"][key] = {"pata": leg, "articulacion": joint}
                log(f"mapa: canal {ch} = {leg}{JOINTS.index(joint) + 1} {joint}")
            save_map(data)
        else:
            self._send(404, {"error": "no existe"})
            return
        self._send(200, self.state())


if __name__ == "__main__":
    log(f"servidor arrancado en puerto {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
