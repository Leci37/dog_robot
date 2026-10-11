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
SAFE_MIN, SAFE_MAX = 30, 150   # límites del ángulo lógico (el que ve el usuario)
PHYS_MIN, PHYS_MAX = 5, 175    # límites del ángulo físico enviado al servo, tras calibrar
MAX_DELTA = 45
CENTER = 90
SPEEDS = {"lento": 0.05, "normal": 0.03, "rapido": 0.012}  # segundos por grado
LEGS = ["A", "B", "C", "D"]
RIGHT = {"B", "C"}  # patas derechas: servos en espejo respecto a las izquierdas
JOINTS = ["hombro", "rodilla", "tobillo"]  # nombres del usuario, del cuerpo hacia el suelo

kit = ServoKit(channels=16)
for _ch in range(16):
    kit.servo[_ch].set_pulse_width_range(MIN_US, MAX_US)
lock = threading.Lock()
angles = {ch: None for ch in range(16)}  # último ángulo LÓGICO mandado; None = suelto
CAL = {}  # canal -> (offset, invertir); se carga del mapa al arrancar


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


def interleaved(per_leg):
    """{pata: {art: x}} -> lista [(pata, art, x)] alternando patas: A1 B1 C1 D1 A2 B2 ..."""
    return [(leg, j, per_leg[leg][j]) for j in JOINTS for leg in LEGS if j in per_leg.get(leg, {})]


def move_many(targets, pause=0.03):
    """Mueve varios canales a la vez, 1 grado por servo en cada paso (alternando entre ellos),
    para que todas las patas vayan juntas y suaves. El orden de `targets` decide el orden de los
    saltos iniciales de los servos sueltos (cuyo ángulo real no conocemos)."""
    targets = {ch: clamp(a) for ch, a in targets.items()}
    for ch, a in targets.items():  # sueltos: salto inevitable, escalonado
        if angles[ch] is None:
            write(ch, a)
            angles[ch] = a
            time.sleep(0.25)
    start = {ch: angles[ch] for ch in targets}
    n = max((abs(targets[ch] - start[ch]) for ch in targets), default=0)
    for i in range(1, n + 1):
        for ch in targets:
            write(ch, round(start[ch] + (targets[ch] - start[ch]) * i / n))
        time.sleep(pause)
    for ch, a in targets.items():
        write(ch, a)
        angles[ch] = a


def clamp(a):
    return max(SAFE_MIN, min(SAFE_MAX, int(a)))


def load_cal():
    CAL.clear()
    for k, m in load_map()["canales"].items():
        if m.get("offset") or m.get("invertir"):
            CAL[int(k)] = (int(m.get("offset", 0)), bool(m.get("invertir", False)))


def physical(ch, a):
    """Ángulo lógico -> físico: invertir (180 - a) y sumar el ajuste del servo."""
    off, inv = CAL.get(ch, (0, False))
    return max(PHYS_MIN, min(PHYS_MAX, (180 - a if inv else a) + off))


def write(ch, a):
    kit.servo[ch].angle = physical(ch, a)


def move_to(ch, target, pause=0.03):
    target = clamp(target)
    cur = angles[ch]
    if cur is None:
        write(ch, target)  # primer pulso: salto absoluto
        time.sleep(0.6)
    else:
        step = 1 if target >= cur else -1
        for a in range(cur, target + step, step):
            write(ch, a)
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
        elif p == "/api/test_many":
            chs = [check_ch(c) for c in b["chs"]]
            if not chs or len(chs) > 4:
                raise ValueError("entre 1 y 4 canales a la vez")
            d = min(abs(int(b.get("delta", 10))), MAX_DELTA)
            c = int(b.get("center", CENTER))
            if c - d < SAFE_MIN or c + d > SAFE_MAX:
                raise ValueError(f"centro {c} ± {d} se sale de {SAFE_MIN}-{SAFE_MAX}")
            pz = pause_of(b)
            move_many({ch: c for ch in chs}, pz)
            time.sleep(0.4)
            for a in (c + d, c - d, c):
                move_many({ch: a for ch in chs}, pz)
                time.sleep(0.25)
            if not b.get("hold", True):
                for ch in chs:
                    release(ch)
            log(f"probar a la vez canales {chs} centro {c} +/-{d}")
        elif p == "/api/angle":
            ch = check_ch(b["ch"])
            move_to(ch, b["angle"], pause_of(b))
            log(f"canal {ch} -> {angles[ch]}")
        elif p == "/api/pose90":
            order = interleaved({leg: leg_channels(leg) for leg in LEGS})
            move_many({ch: CENTER for _, _, ch in order}, pause_of(b))
            log(f"bloquear todo a 90 (alternando): {[f'{l}{JOINTS.index(j) + 1}' for l, j, _ in order]}")
        elif p == "/api/group":
            joint = b["articulacion"]
            if joint not in JOINTS:
                raise ValueError("articulación no válida")
            a, mirror = clamp(b["angle"]), bool(b.get("mirror", True))
            targets = {}
            for leg in LEGS:
                ch = leg_channels(leg).get(joint)
                if ch is not None:
                    targets[ch] = 180 - a if mirror and leg in RIGHT else a
            move_many(targets, pause_of(b))
            log(f"grupo {joint}: {a} (espejo der={mirror}) -> {targets}")
        elif p == "/api/pose_save":
            name = b["nombre"]
            legs = LEGS if b.get("pata") == "todas" else [b["pata"]]
            if not name or any(leg not in LEGS for leg in legs):
                raise ValueError("pata o nombre no válido")
            poses, saved = load_poses(), {}
            for leg in legs:
                pose = {j: angles[ch] for j, ch in leg_channels(leg).items() if angles[ch] is not None}
                if pose:
                    poses.setdefault(leg, {})[name] = pose
                    saved[leg] = pose
            if not saved:
                raise ValueError("las patas están sueltas: colócalas con los deslizadores antes de guardar")
            write_json(POSES_FILE, poses)
            log(f"postura '{name}' guardada: {saved}")
        elif p == "/api/pose_go":
            name, pz = b["nombre"], pause_of(b)
            legs = LEGS if b.get("pata") == "todas" else [b["pata"]]
            poses, per_leg = load_poses(), {}
            for leg in legs:
                pose, chs = poses.get(leg, {}).get(name), leg_channels(leg)
                if pose:
                    per_leg[leg] = {j: (chs[j], a) for j, a in pose.items() if j in chs}
            if not per_leg:
                raise ValueError(f"no hay postura '{name}' guardada para {', '.join(legs)}")
            # todas las patas juntas, poco a poco y alternando
            move_many({ch: a for _, _, (ch, a) in interleaved(per_leg)}, pz)
            log(f"postura '{name}' a la vez en patas {list(per_leg)}")
        elif p == "/api/leg_release":
            leg = b["pata"]
            for ch in leg_channels(leg).values():
                release(ch)
            log(f"soltar pata {leg}")
        elif p == "/api/calibrate":
            ch, action = check_ch(b["ch"]), b["action"]
            data = load_map()
            m = data["canales"].get(str(ch))
            if not m or m.get("libre"):
                raise ValueError("solo se calibran canales mapeados")
            off, inv = CAL.get(ch, (0, False))
            if action != "reset" and angles[ch] is None:
                raise ValueError("el servo está suelto: muévelo primero con su deslizador")
            phys = physical(ch, angles[ch]) if angles[ch] is not None else None
            if action == "zero":      # la posición actual pasa a ser el 90 lógico (sin moverse)
                off, angles[ch] = phys - 90, 90
            elif action == "invert":  # cambia el sentido sin moverse
                inv = not inv
                angles[ch] = (180 - (phys - off)) if inv else (phys - off)
            elif action == "reset":   # quita el ajuste sin moverse
                off, inv = 0, False
                if phys is not None:
                    angles[ch] = phys
            else:
                raise ValueError("acción no válida")
            CAL[ch] = (off, inv)
            m["offset"], m["invertir"] = off, inv
            save_map(data)
            log(f"calibrar canal {ch}: {action} -> ajuste {off:+d}, invertir={inv}, angulo logico {angles[ch]}")
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
                old = data["canales"].get(key, {})
                entry = {"pata": leg, "articulacion": joint}
                for f in ("offset", "invertir", "nota"):  # conservar calibración al re-mapear
                    if f in old:
                        entry[f] = old[f]
                data["canales"][key] = entry
                log(f"mapa: canal {ch} = {leg}{JOINTS.index(joint) + 1} {joint}")
            save_map(data)
        else:
            self._send(404, {"error": "no existe"})
            return
        self._send(200, self.state())


def recover_angles():
    """Al arrancar, deduce de los registros de la PCA9685 dónde están los servos que siguen sujetos,
    para que un reinicio del servidor no los haga saltar ni los dé por sueltos."""
    for ch in range(16):
        try:
            duty = kit._pca.channels[ch].duty_cycle
        except Exception:  # noqa: BLE001
            continue
        if duty in (0, 0xFFFF):
            continue
        us = duty * 1_000_000 / kit._pca.frequency / 0xFFFF
        phys = round((us - MIN_US) * 180 / (MAX_US - MIN_US))
        off, inv = CAL.get(ch, (0, False))
        a = phys - off
        angles[ch] = 180 - a if inv else a


if __name__ == "__main__":
    load_cal()
    recover_angles()
    log(f"angulos recuperados de la PCA: { {c: a for c, a in angles.items() if a is not None} }")
    log(f"servidor arrancado en puerto {PORT}; calibración {CAL}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
