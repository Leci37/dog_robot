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
POSES_FILE = WEB.parent / "poses.json"  # {"A": {"recogida": {"hombro": 90, ...}, "estirada": {...}}, ...}
LOG_FILE = WEB / "acciones.log"
PORT = 8080

MIN_US, MAX_US = 500, 2500   # MG996R aprox. 0-180 grados
SAFE_MIN, SAFE_MAX = 30, 150
MAX_DELTA = 45
CENTER = 90
SPEEDS = {"lento": 0.05, "normal": 0.03, "rapido": 0.012}  # segundos por grado
LEGS = ["A", "B", "C", "D"]
JOINTS = ["hombro", "rodilla", "tobillo"]  # nombres del usuario, del cuerpo hacia el suelo

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
                     "Articulaciones (del cuerpo al suelo): 1 hombro (abre/cierra al lado), "
                     "2 rodilla (pata adelante/atrás), 3 tobillo (dobla la parte de abajo).",
            "canales": {},
        }
    return data


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def save_map(data):
    write_json(MAP_FILE, data)


def load_poses():
    try:
        return json.loads(POSES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def leg_channels(leg):
    """{articulación: canal} de una pata según el mapa (primer canal si hay duplicados)."""
    out = {}
    for k, m in sorted(load_map()["canales"].items(), key=lambda x: int(x[0])):
        if m.get("pata") == leg and m.get("articulacion") not in out:
            out[m["articulacion"]] = int(k)
    return out


def move_many(targets, pause=0.03):
    """Mueve varios canales a la vez, interpolando, para que la pata vaya suave y coordinada."""
    targets = {ch: clamp(a) for ch, a in targets.items()}
    for ch, a in targets.items():  # los sueltos no tienen ángulo conocido: salto escalonado
        if angles[ch] is None:
            kit.servo[ch].angle = a
            angles[ch] = a
            time.sleep(0.4)
    start = {ch: angles[ch] for ch in targets}
    n = max((abs(targets[ch] - start[ch]) for ch in targets), default=0)
    for i in range(1, n + 1):
        for ch in targets:
            kit.servo[ch].angle = round(start[ch] + (targets[ch] - start[ch]) * i / n)
        time.sleep(pause)
    for ch, a in targets.items():
        kit.servo[ch].angle = a
        angles[ch] = a


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


def pause_of(b):
    return SPEEDS.get(b.get("speed"), SPEEDS["normal"])


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
        return {"angles": angles, "map": load_map()["canales"], "poses": load_poses(),
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
            c = int(b.get("center", CENTER))
            if c - d < SAFE_MIN or c + d > SAFE_MAX:
                raise ValueError(f"centro {c} ± {d} se sale de {SAFE_MIN}-{SAFE_MAX}")
            pz = pause_of(b)
            move_to(ch, c, pz)
            time.sleep(0.5)
            move_to(ch, c + d, pz)
            time.sleep(0.3)
            move_to(ch, c - d, pz)
            time.sleep(0.3)
            move_to(ch, c, pz)
            if not b.get("hold", True):
                release(ch)
            log(f"probar canal {ch} centro {c} +/-{d} {b.get('speed', 'normal')} hold={b.get('hold', True)}")
        elif p == "/api/angle":
            ch = check_ch(b["ch"])
            move_to(ch, b["angle"], pause_of(b))
            log(f"canal {ch} -> {angles[ch]}")
        elif p == "/api/pose90":
            chs = sorted(int(k) for k, m in load_map()["canales"].items() if not m.get("libre"))
            for ch in chs:
                move_to(ch, CENTER, pause_of(b))
                time.sleep(0.3)
            log(f"postura 90 en canales {chs}")
        elif p == "/api/pose_save":
            leg, name = b["pata"], b["nombre"]
            if leg not in LEGS or not name:
                raise ValueError("pata o nombre no válido")
            chs = leg_channels(leg)
            pose = {j: angles[ch] for j, ch in chs.items() if angles[ch] is not None}
            if not pose:
                raise ValueError(f"la pata {leg} está suelta: colócala con los deslizadores antes de guardar")
            poses = load_poses()
            poses.setdefault(leg, {})[name] = pose
            write_json(POSES_FILE, poses)
            log(f"postura '{name}' de {leg} guardada: {pose}")
        elif p == "/api/pose_go":
            name, pz = b["nombre"], pause_of(b)
            legs = LEGS if b.get("pata") == "todas" else [b["pata"]]
            poses, done = load_poses(), []
            for leg in legs:  # pata a pata, nunca las 4 a la vez
                pose = poses.get(leg, {}).get(name)
                if not pose:
                    continue
                chs = leg_channels(leg)
                move_many({chs[j]: a for j, a in pose.items() if j in chs}, pz)
                done.append(leg)
                time.sleep(0.3)
            if not done:
                raise ValueError(f"no hay postura '{name}' guardada para {', '.join(legs)}")
            log(f"postura '{name}' en patas {done}")
        elif p == "/api/leg_release":
            leg = b["pata"]
            for ch in leg_channels(leg).values():
                release(ch)
            log(f"soltar pata {leg}")
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
