#!/home/luislecinana/robot/bin/python
"""Web de control del SpotMicro.

http://<ip-de-la-pi> (puerto 80) y :8080  ·  servicio systemd: spotmicro-web
Guarda el mapa en ../servo_map.json, las posturas en ../poses.json, un log de acciones en
acciones.log y un registro permanente de arranques y cortes de corriente en ../energia.log.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from adafruit_servokit import ServoKit

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
MAP_FILE = ROOT / "servo_map.json"
POSES_FILE = ROOT / "poses.json"  # {"A": {"recogida": {"hombro": 90, ...}, "estirada": {...}}, ...}
LOG_FILE = WEB / "acciones.log"
ENERGY_LOG = ROOT / "energia.log"
HELP_FILE = ROOT / "AYUDA.md"
SCREEN_DIR = ROOT / "pantalla"
SCREEN_MODE = SCREEN_DIR / "modo"          # lo que se pide mostrar en el HDMI: panel | web | consola
SCREEN_BOOT = SCREEN_DIR / "arranque"      # opción por defecto del menú al arrancar
SCREEN_STATUS = SCREEN_DIR / "estado"      # lo que muestra ahora (lo escribe el panel)
BROWSER_SCRIPT = SCREEN_DIR / "navegador.sh"
PORTS = (8080, 80)
LINK_LOCAL_IP = "169.254.42.1"             # dirección fija para cable directo (ver CLAUDE.md)
IPV6_LL = "fe80::ba27:ebff:fe16:ec15"      # IPv6 link-local fija (EUI-64 de la MAC)

MIN_US, MAX_US = 500, 2500   # MG996R aprox. 0-180 grados
SAFE_MIN, SAFE_MAX = 30, 150   # límites del ángulo lógico (el que ve el usuario)
PHYS_MIN, PHYS_MAX = 5, 175    # límites del ángulo físico enviado al servo, tras calibrar
MAX_DELTA = 45
CENTER = 90
SPEEDS = {"lento": 0.05, "normal": 0.03, "rapido": 0.012}  # segundos por grado
LEGS = ["A", "B", "C", "D"]
RIGHT = {"B", "C"}  # patas derechas: servos en espejo respecto a las izquierdas
FRONT = {"A", "B"}

# Posturas candidatas para descubrir el sentido de cada articulación: ángulos lógicos de la pata
# izquierda trasera; derechas en espejo (180 - a) y, en la variante "delanteras invertidas", las
# delanteras también en espejo en rodilla y tobillo. El usuario marca cuáles quedan bien.
CANDIDATE_BASES = [
    ("neutra", "Neutra (todo a 90°)", {"hombro": 90, "rodilla": 90, "tobillo": 90}),
    ("r+t-", "Rodilla +25 · tobillo −40", {"hombro": 90, "rodilla": 115, "tobillo": 50}),
    ("r-t+", "Rodilla −25 · tobillo +40", {"hombro": 90, "rodilla": 65, "tobillo": 130}),
    ("r+t+", "Rodilla +25 · tobillo +40", {"hombro": 90, "rodilla": 115, "tobillo": 130}),
    ("r-t-", "Rodilla −25 · tobillo −40", {"hombro": 90, "rodilla": 65, "tobillo": 50}),
]
CANDIDATES = []
for _id, _name, _base in CANDIDATE_BASES:
    CANDIDATES.append({"id": _id + "|igual", "nombre": _name, "delanteras": "igual", "base": _base})
    if _id != "neutra":
        CANDIDATES.append({"id": _id + "|inv", "nombre": _name, "delanteras": "invertidas", "base": _base})
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


def fsync_dir(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, data):
    """Escritura a prueba de cortes de corriente: fichero temporal + fsync + copia .bak + rename."""
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp.replace(path)
    fsync_dir(path.parent)


# ---------- sistema: energía, red, pantalla ----------

THROTTLE_BITS = [
    (0, "FALTA DE CORRIENTE AHORA"), (1, "frecuencia limitada ahora"), (2, "CPU frenada ahora"),
    (3, "límite de temperatura ahora"), (16, "hubo falta de corriente desde el arranque"),
    (17, "hubo frecuencia limitada"), (18, "hubo CPU frenada"), (19, "hubo límite de temperatura"),
]
power = {"throttled": None}
_sys_cache = {"t": 0, "data": None}


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()[:8]


def energy_log(msg):
    with ENERGY_LOG.open("a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
        f.flush()
        os.fsync(f.fileno())


def run(cmd, timeout=4):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def read_throttled():
    out = run(["vcgencmd", "get_throttled"])
    try:
        return int(out.split("=")[1], 16)
    except (IndexError, ValueError):
        return None


def power_monitor():
    """Vigila la alimentación cada 2 s y apunta en energia.log cada cambio (se guarda aunque se corte)."""
    last = None
    while True:
        t = read_throttled()
        if t is not None:
            power["throttled"] = t
            if last is not None and t != last:
                if t & 1 and not last & 1:
                    energy_log("FALTA DE CORRIENTE detectada (under-voltage)")
                elif last & 1 and not t & 1:
                    energy_log("tensión normal otra vez")
                elif t & 0x10000 and not last & 0x10000:
                    energy_log("caída de tensión breve (entre dos lecturas)")
                if t & 4 and not last & 4:
                    energy_log("CPU frenada por alimentación o temperatura")
                if t & 8 and not last & 8:
                    energy_log("límite de temperatura alcanzado")
            last = t
        time.sleep(2)


def boot_check():
    """Al arrancar el servidor: ¿es un arranque nuevo de la Pi? ¿terminó bien el anterior?"""
    bid = boot_id()
    try:
        lines = ENERGY_LOG.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        lines = []
    boots = [ln for ln in lines if " ARRANQUE " in ln]
    if boots and f"boot={bid}" in boots[-1]:
        energy_log(f"servidor web reiniciado (misma sesión de la Pi, boot={bid})")
        return
    if boots:
        prev = boots[-1].split("boot=")[1].split()[0].rstrip(";")
        clean = any(f"APAGADO-LIMPIO boot={prev}" in ln for ln in lines)
        how = "limpio" if clean else "DE GOLPE (corte de corriente, cuelgue o desenchufe sin apagar)"
    else:
        how = "desconocido (primer registro)"
    t = read_throttled()
    up = float(Path("/proc/uptime").read_text().split()[0])
    energy_log(f"ARRANQUE boot={bid}; la Pi lleva {up:.0f} s encendida; el arranque anterior terminó: {how}; "
               f"throttled={hex(t) if t is not None else '?'}")


def on_sigterm(signum, frame):
    state = run(["systemctl", "is-system-running"], timeout=2)
    if state == "stopping":
        energy_log(f"APAGADO-LIMPIO boot={boot_id()}")
    else:
        energy_log(f"servidor web parado (sistema: {state or '?'})")
    os._exit(0)


def read_text(path, default=""):
    try:
        return path.read_text().strip()
    except (FileNotFoundError, PermissionError):
        return default


def net_info():
    try:
        addrs = json.loads(run(["ip", "-j", "addr", "show"]) or "[]")
        routes = json.loads(run(["ip", "-j", "route", "show", "default"]) or "[]")
    except json.JSONDecodeError:
        addrs, routes = [], []
    ipv4, ipv6_ll = [], ""
    for it in addrs:
        if it.get("ifname") == "lo":
            continue
        for a in it.get("addr_info", []):
            if a.get("family") == "inet":
                ipv4.append({"if": it["ifname"], "ip": a["local"], "prefix": a["prefixlen"]})
            elif a.get("family") == "inet6" and a.get("scope") == "link" and it.get("ifname") == "eth0":
                ipv6_ll = a["local"]
    carrier = read_text(Path("/sys/class/net/eth0/carrier"), "0") == "1"
    gw = next((r.get("gateway") for r in routes if r.get("gateway")), "")
    mode = "sin-cable" if not carrier else ("router" if gw else "directo")
    port = "" if bound_ports and 80 in bound_ports else ":8080"
    urls = [f"http://{a['ip']}{port}" for a in ipv4 if a["if"] == "eth0" and a["ip"] != LINK_LOCAL_IP]
    urls.append(f"http://{LINK_LOCAL_IP}{port}")
    return {"mode": mode, "carrier": carrier, "gateway": gw, "ipv4": ipv4, "ipv6_ll": ipv6_ll,
            "urls": urls, "hostname": socket.gethostname()}


def screen_info():
    def active(unit):
        return run(["systemctl", "is-active", unit], timeout=2) == "active"
    hdmi = ""
    for st in Path("/sys/class/drm").glob("card*-HDMI-A-1/status"):
        hdmi = read_text(st)
    return {"modo": read_text(SCREEN_MODE, "panel"), "arranque": read_text(SCREEN_BOOT, "panel"),
            "estado": read_text(SCREEN_STATUS, ""), "panel_activo": active("spotmicro-pantalla.service"),
            "consola_activa": active("getty@tty1.service"),
            "navegador_instalado": BROWSER_SCRIPT.exists() and bool(shutil.which("cage")) and bool(shutil.which("chromium")),
            "hdmi": hdmi}


def system_info():
    now = time.time()
    if _sys_cache["data"] and now - _sys_cache["t"] < 3:
        return _sys_cache["data"]
    t = power["throttled"] if power["throttled"] is not None else read_throttled()
    temp = run(["vcgencmd", "measure_temp"]).replace("temp=", "").replace("'C", "")
    uv = run(["journalctl", "-k", "-b", "-q", "--no-pager", "-g", "Undervoltage detected"], timeout=6)
    try:
        energy = ENERGY_LOG.read_text(errors="replace").splitlines()[-15:]
    except FileNotFoundError:
        energy = []
    data = {
        "uptime_s": int(float(Path("/proc/uptime").read_text().split()[0])),
        "temp_c": temp,
        "throttled": t,
        "throttled_txt": [txt for bit, txt in THROTTLE_BITS if t is not None and t >> bit & 1],
        "caidas_tension_arranque": len(uv.splitlines()) if uv else 0,
        "net": net_info(),
        "screen": screen_info(),
        "energia": energy,
        "ports": sorted(bound_ports),
        "ipv6_ll_fija": IPV6_LL,
        "ip_directa": LINK_LOCAL_IP,
    }
    _sys_cache.update(t=now, data=data)
    return data


def set_screen(mode):
    if mode not in ("panel", "web", "consola"):
        raise ValueError("modo de pantalla no válido")
    SCREEN_DIR.mkdir(exist_ok=True)
    SCREEN_MODE.write_text(mode + "\n")
    if mode == "consola":
        subprocess.Popen(["sudo", "-n", "systemctl", "start", "getty@tty1.service"])
    elif run(["systemctl", "is-active", "spotmicro-pantalla.service"], timeout=2) != "active":
        subprocess.Popen(["sudo", "-n", "systemctl", "start", "spotmicro-pantalla.service"])
    _sys_cache["data"] = None


bound_ports = set()


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
        return {"angles": angles, "map": load_map()["canales"], "poses": load_poses(), "candidatas": CANDIDATES,
                "limits": {"min": SAFE_MIN, "max": SAFE_MAX, "max_delta": MAX_DELTA}}

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._send(200, self.state())
        elif path == "/api/system":
            self._send(200, system_info())
        elif path in ("/ayuda", "/ayuda.txt"):
            self._send(200, HELP_FILE.read_bytes() if HELP_FILE.exists() else b"sin ayuda", "text/plain; charset=utf-8")
        else:
            self._send(404, {"error": "no existe"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if urlsplit(self.path).path in ("/api/screen", "/api/screen_boot", "/api/system_action"):
                self.route_system(body)  # no tocan servos: sin esperar al cerrojo de movimiento
                return
            with lock:
                self.route(body)
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log(f"ERROR {self.path}: {e!r}")
            self._send(500, {"error": repr(e)})

    def route_system(self, b):
        p = urlsplit(self.path).path
        if p == "/api/screen":
            set_screen(b.get("modo", ""))
            log(f"pantalla HDMI -> {b.get('modo')}")
        elif p == "/api/screen_boot":
            mode = b.get("modo", "")
            if mode not in ("panel", "web", "consola"):
                raise ValueError("modo de arranque no válido")
            SCREEN_DIR.mkdir(exist_ok=True)
            SCREEN_BOOT.write_text(mode + "\n")
            log(f"pantalla HDMI al arrancar -> {mode}")
        elif p == "/api/system_action":
            action = b.get("accion")
            if action not in ("apagar", "reiniciar"):
                raise ValueError("acción no válida")
            with lock:
                for ch in range(16):
                    release(ch)
            log(f"SISTEMA: {action} (servos soltados antes)")
            energy_log(f"{action} pedido desde la web/panel")
            verb = "poweroff" if action == "apagar" else "reboot"
            # temporizador fuera de este servicio, para que la respuesta llegue antes de apagar
            subprocess.Popen(["sudo", "-n", "systemd-run", "--on-active=2", "--timer-property=AccuracySec=100ms",
                              "/usr/bin/systemctl", verb])
        _sys_cache["data"] = None
        self._send(200, system_info())

    def route(self, b):
        p = urlsplit(self.path).path
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
        elif p == "/api/candidate_go":
            cand = next((c for c in CANDIDATES if c["id"] == b["id"]), None)
            if not cand:
                raise ValueError("postura candidata desconocida")
            mirror = b.get("mirror", {})
            per_leg = {}
            for leg in LEGS:
                chs = leg_channels(leg)
                per_leg[leg] = {}
                for j, a in cand["base"].items():
                    if j not in chs:
                        continue
                    if cand["delanteras"] == "invertidas" and leg in FRONT and j != "hombro":
                        a = 180 - a
                    if mirror.get(j, True) and leg in RIGHT:
                        a = 180 - a
                    per_leg[leg][j] = (chs[j], a)
            move_many({ch: a for _, _, (ch, a) in interleaved(per_leg)}, pause_of(b))
            log(f"candidata {cand['id']} ({cand['nombre']}, delanteras {cand['delanteras']})")
        elif p == "/api/candidate_mark":
            cid = b["id"]
            if not any(c["id"] == cid for c in CANDIDATES):
                raise ValueError("postura candidata desconocida")
            poses = load_poses()
            marks = poses.setdefault("_candidatas", {})
            if b.get("mark") in ("bien", "mal"):
                marks[cid] = {"mark": b["mark"], "nota": str(b.get("nota", ""))[:200]}
            else:
                marks.pop(cid, None)
            write_json(POSES_FILE, poses)
            log(f"candidata {cid} marcada: {marks.get(cid)}")
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


ThreadingHTTPServer.daemon_threads = True


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
    signal.signal(signal.SIGTERM, on_sigterm)
    load_cal()
    recover_angles()
    boot_check()
    threading.Thread(target=power_monitor, daemon=True).start()
    servers = []
    for port in PORTS:
        try:
            servers.append(ThreadingHTTPServer(("0.0.0.0", port), Handler))
            bound_ports.add(port)
        except OSError as e:  # p. ej. el 80 sin permiso: seguimos con el 8080
            log(f"no puedo escuchar en el puerto {port}: {e}")
    log(f"angulos recuperados de la PCA: { {c: a for c, a in angles.items() if a is not None} }")
    log(f"servidor arrancado en puertos {sorted(bound_ports)}; calibración {CAL}")
    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    servers[0].serve_forever()
