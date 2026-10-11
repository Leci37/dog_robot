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
PORTS = tuple(int(p) for p in os.environ.get("SPOTMICRO_PORTS", "8080,80").split(","))  # pruebas: otro puerto
INSTALL_SCRIPT = ROOT / "sistema" / "instala-navegador.sh"
INSTALL_UNIT = "spotmicro-instala-navegador"
LINK_LOCAL_IP = "169.254.42.1"             # dirección fija para cable directo (ver CLAUDE.md)
IPV6_LL = "fe80::ba27:ebff:fe16:ec15"      # IPv6 link-local fija (EUI-64 de la MAC)

MIN_US, MAX_US = 500, 2500   # MG996R aprox. 0-180 grados
SAFE_MIN, SAFE_MAX = 30, 150   # límites del ángulo lógico (el que ve el usuario)
PHYS_MIN, PHYS_MAX = 5, 175    # límites del ángulo físico enviado al servo, tras calibrar
MAX_DELTA = 45
CENTER = 90
SPEEDS = {"lento": 0.05, "normal": 0.03, "rapido": 0.012}  # segundos por grado
FAST = bool(os.environ.get("SPOTMICRO_FAST"))  # solo para pruebas con el simulador: sin esperas reales
if FAST:
    SPEEDS = {k: 0.0 for k in SPEEDS}
LEGS = ["A", "B", "C", "D"]
RIGHT = {"B", "C"}  # patas derechas: servos en espejo respecto a las izquierdas
FRONT = {"A", "B"}
JOINTS = ["pectoral", "hombro", "codo"]  # nombres del usuario, del cuerpo hacia el suelo (1, 2, 3)
ROUTINES_FILE = ROOT / "rutinas.json"    # {"nombre": {"programa": [...], "bloques": {...}|null, "origen": str}}
CONFIG_FILE = ROOT / "config.json"       # {"convencion": {"sh": ±1, "sk": ±1, "fi": bool}, "pruebas": {...}}
STATIC_DIR = WEB / "static"

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
                     "Articulaciones (del cuerpo al suelo): 1 pectoral (abre/cierra al lado), "
                     "2 hombro (pata adelante/atrás), 3 codo (dobla la parte de abajo).",
            "nomenclatura": 2,
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
    installing = any(active(u) for u in (INSTALL_UNIT, "instala-navegador3", "instala-navegador2"))
    return {"modo": read_text(SCREEN_MODE, "panel"), "arranque": read_text(SCREEN_BOOT, "panel"),
            "estado": read_text(SCREEN_STATUS, ""), "panel_activo": active("spotmicro-pantalla.service"),
            "instalando_navegador": installing,
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


# ---------- rutinas: lenguaje de pasos, ejecución en segundo plano ----------
#
# Un programa es una lista de pasos JSON. Ángulos "de gesto": los de la pata D (trasera izquierda);
# al resto de patas se les aplica el equivalente para que hagan el mismo gesto (espejo 180−a en las
# derechas y, si la convención dice delanteras invertidas, también en hombro/codo de las delanteras).
#   {"op": "articulacion", "pata": "A".."D"|"todas", "art": "pectoral|hombro|codo", "angulo": 30..150}
#   {"op": "articulacion_rel", "pata": ..., "art": ..., "delta": -60..60}
#   {"op": "pata", "pata": "A", "pectoral": a?, "hombro": a?, "codo": a?}
#   {"op": "set", "objetivos": [{"pata", "art", "angulo"}]}   ángulos lógicos YA calculados (rutinas generadas)
#   {"op": "postura", "nombre": "de pie"}    {"op": "mitad"}    {"op": "soltar", "pata"?: "A"}
#   {"op": "esperar", "s": 0..30}    {"op": "velocidad", "v": "lento|normal|rapido"}
#   {"op": "repetir", "n": 1..50, "pasos": [...]}    {"op": "rutina", "nombre": "..."}

routine = {"running": False, "nombre": "", "paso": 0, "total": 0, "error": "", "stop": False, "fin": ""}


class RoutineStop(Exception):
    pass


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_config():
    return load_json(CONFIG_FILE, {})


def gesture_angle(leg, art, a, conv=None):
    """Ángulo de gesto (referido a la pata D) -> ángulo lógico del servo de esa pata."""
    conv = conv if conv is not None else (load_config().get("convencion") or {})
    dev = a - 90
    if conv.get("fi") and leg in FRONT and art != "pectoral":
        dev = -dev
    v = 90 + dev
    return clamp(180 - v if leg in RIGHT else v)


def logical_to_gesture(leg, art, v, conv):
    """Inversa de gesture_angle (para los pasos relativos)."""
    if leg in RIGHT:
        v = 180 - v
    dev = v - 90
    if conv.get("fi") and leg in FRONT and art != "pectoral":
        dev = -dev
    return 90 + dev


def legs_of(spec):
    if spec in (None, "todas"):
        return LEGS
    if spec not in LEGS:
        raise ValueError(f"pata no válida: {spec}")
    return [spec]


def check_art(art):
    if art not in JOINTS:
        raise ValueError(f"articulación no válida: {art}")
    return art


def count_steps(steps, depth=0):
    n = 0
    for st in steps:
        n += count_steps(st.get("pasos", []), depth + 1) * max(1, min(50, int(st.get("n", 1)))) if st.get("op") == "repetir" else 1
    return n


def wait_s(secs):
    end = time.time() + max(0.0, min(30.0, float(secs))) / (100 if FAST else 1)
    while time.time() < end:
        if routine["stop"]:
            raise RoutineStop()
        time.sleep(0.05)


def run_steps(steps, ctx, depth=0):
    if depth > 6:
        raise ValueError("demasiadas rutinas dentro de rutinas")
    for st in steps:
        if routine["stop"]:
            raise RoutineStop()
        op = st.get("op")
        if op == "repetir":
            for _ in range(max(1, min(50, int(st.get("n", 1))))):
                run_steps(st.get("pasos", []), ctx, depth + 1)
            continue
        if op == "esperar":
            wait_s(st.get("s", 0.5))
        elif op == "velocidad":
            ctx["pause"] = SPEEDS.get(st.get("v"), SPEEDS["normal"])
        elif op == "rutina":
            r = load_json(ROUTINES_FILE, {}).get(st.get("nombre", ""))
            if not r:
                raise ValueError(f"no existe la rutina '{st.get('nombre')}'")
            run_steps(r["programa"], ctx, depth + 1)
            continue
        else:
            if not power_cfg()["permitir_sin_corriente"] and low_power_now():
                raise ValueError("falta de corriente en la Pi: rutina parada para que no se apague")
            with lock:  # cada paso con el cerrojo de servos; entre pasos la web sigue respondiendo
                run_move(st, ctx)
                save_last()
        routine["paso"] += 1


def run_move(st, ctx):
    op, conv = st.get("op"), ctx["conv"]
    targets = {}
    if op == "articulacion":
        art = check_art(st.get("art"))
        for leg in legs_of(st.get("pata")):
            ch = leg_channels(leg).get(art)
            if ch is not None:
                targets[ch] = gesture_angle(leg, art, float(st.get("angulo", 90)), conv)
    elif op == "articulacion_rel":
        art = check_art(st.get("art"))
        d = max(-60, min(60, float(st.get("delta", 0))))
        for leg in legs_of(st.get("pata")):
            ch = leg_channels(leg).get(art)
            if ch is not None:
                cur = angles[ch] if angles[ch] is not None else gesture_angle(leg, art, 90, conv)
                targets[ch] = gesture_angle(leg, art, logical_to_gesture(leg, art, cur, conv) + d, conv)
    elif op == "pata":
        leg = legs_of(st.get("pata"))[0]
        chs = leg_channels(leg)
        for art in JOINTS:
            if st.get(art) is not None and art in chs:
                targets[chs[art]] = gesture_angle(leg, art, float(st[art]), conv)
    elif op == "set":
        for o in st.get("objetivos", []):
            ch = leg_channels(legs_of(o["pata"])[0]).get(check_art(o["art"]))
            if ch is not None:
                targets[ch] = clamp(o["angulo"])
    elif op == "postura":
        poses = load_poses()
        for leg in LEGS:
            pose, chs = poses.get(leg, {}).get(st.get("nombre", "")), leg_channels(leg)
            for art, a in (pose or {}).items():
                if art in chs:
                    targets[chs[art]] = a
        if not targets:
            raise ValueError(f"no hay postura '{st.get('nombre')}' guardada")
    elif op == "mitad":
        for leg in LEGS:
            for ch in leg_channels(leg).values():
                targets[ch] = CENTER
    elif op == "soltar":
        for leg in legs_of(st.get("pata")):
            for ch in leg_channels(leg).values():
                release(ch)
        return
    else:
        raise ValueError(f"paso desconocido: {op}")
    if targets:
        move_many(targets, ctx["pause"])


def start_routine(program, name):
    if routine["running"]:
        raise ValueError(f"ya se está ejecutando '{routine['nombre']}': párala primero")
    if not isinstance(program, list) or not program:
        raise ValueError("programa vacío")
    routine.update(running=True, nombre=name, paso=0, total=count_steps(program), error="", stop=False, fin="")
    ctx = {"pause": SPEEDS["normal"], "conv": load_config().get("convencion") or {}}

    def worker():
        try:
            run_steps(program, ctx)
            routine["fin"] = "terminada"
        except RoutineStop:
            routine["fin"] = "parada"
        except Exception as e:  # noqa: BLE001
            routine["error"] = str(e)
            routine["fin"] = "error"
        finally:
            routine["running"] = False
            log(f"rutina '{name}': {routine['fin']}{' - ' + routine['error'] if routine['error'] else ''}")

    log(f"rutina '{name}' empieza ({routine['total']} pasos)")
    threading.Thread(target=worker, daemon=True).start()


# ---------- rutinas para probar (generadas) ----------
# Patas de longitud parecida: para agacharse con la pezuña bajo la cadera, el codo se dobla beta y el
# hombro beta/2. Como no sabemos hacia qué lado gira cada servo, la etapa 1 prueba las 8 combinaciones
# (signo del hombro sh, del codo sk, delanteras igual o invertidas fi). Cuando el usuario marca una como
# buena se guarda la convención y la etapa 2 genera rutinas (de pie, tumbarse, sentarse, andar…).

def dev_angle(leg, art, dev, conv):
    s = conv.get("sh", 1) if art == "hombro" else conv.get("sk", 1) if art == "codo" else 1
    if conv.get("fi") and leg in FRONT and art != "pectoral":
        s = -s
    v = 90 + s * dev
    return clamp(180 - v if leg in RIGHT else v)


def stance(conv, beta, legs=None, pectoral=0.0, hip_extra=None):
    out = []
    for leg in legs or LEGS:
        hd = beta / 2 + (hip_extra or {}).get(leg, 0)
        for art, dev in (("pectoral", pectoral), ("hombro", hd), ("codo", beta)):
            out.append({"pata": leg, "art": art, "angulo": dev_angle(leg, art, dev, conv)})
    return out


def S(objs):
    return {"op": "set", "objetivos": objs}


def walk_program(conv, step_l, step_r, cycles=2, beta=40, lift=18):
    """Paso a paso (creep): una pata cada vez en el orden D A C B; tras cada paso el cuerpo avanza."""
    order, h0, k0 = ["D", "A", "C", "B"], beta / 2, beta
    step = {L: (step_l if L in ("A", "D") else step_r) for L in LEGS}
    hd = {"D": h0 - step["D"] / 2, "A": h0 - step["A"] / 4, "C": h0, "B": h0 + step["B"] / 4}
    prog = [{"op": "velocidad", "v": "normal"}, S(stance(conv, beta)), {"op": "esperar", "s": 0.6},
            S([{"pata": L, "art": "hombro", "angulo": dev_angle(L, "hombro", hd[L], conv)} for L in LEGS])]
    cycle = []
    for L in order:
        cycle.append(S([{"pata": L, "art": "codo", "angulo": dev_angle(L, "codo", k0 + lift, conv)}]))
        cycle.append(S([{"pata": L, "art": "hombro", "angulo": dev_angle(L, "hombro", h0 + step[L] / 2, conv)}]))
        cycle.append(S([{"pata": L, "art": "codo", "angulo": dev_angle(L, "codo", k0, conv)}]))
        hd[L] = h0 + step[L] / 2
        for x in LEGS:
            hd[x] -= step[x] / 4
        cycle.append(S([{"pata": x, "art": "hombro", "angulo": dev_angle(x, "hombro", hd[x], conv)} for x in LEGS]))
    prog.append({"op": "repetir", "n": cycles, "pasos": cycle})
    prog.append(S(stance(conv, beta)))
    return prog


def candidate_routines(conv_saved):
    """Etapa 1 (sin convención): 8 variantes de 'de pie'. Etapa 2 (con convención): rutinas útiles."""
    cands = []
    for sh in (1, -1):
        for sk in (1, -1):
            for fi in (False, True):
                conv = {"sh": sh, "sk": sk, "fi": fi}
                n = len(cands) + 1
                cands.append({
                    "id": f"depie|{sh}|{sk}|{int(fi)}", "etapa": 1, "nombre": f"De pie · variante {n}",
                    "desc": f"hombro {'+' if sh > 0 else '−'}, codo {'+' if sk > 0 else '−'}, delanteras "
                            f"{'invertidas' if fi else 'igual que las traseras'}",
                    "conv": conv,
                    "programa": [{"op": "velocidad", "v": "normal"}, {"op": "mitad"}, {"op": "esperar", "s": 1},
                                 S(stance(conv, 40)), {"op": "esperar", "s": 2}],
                })
    if not conv_saved:
        return cands
    c = conv_saved
    st = lambda beta, **kw: S(stance(c, beta, **kw))  # noqa: E731
    two = [
        ("de-pie-alto", "De pie alto", "Patas casi rectas (agachado 30)", [st(30)]),
        ("de-pie-bajo", "De pie bajo", "Más agachado (55)", [st(55)]),
        ("tumbarse", "Tumbarse", "Despacio hasta lo más agachado (60)",
         [{"op": "velocidad", "v": "lento"}, st(40), st(60)]),
        ("levantarse", "Levantarse", "De tumbado a de pie, despacio",
         [{"op": "velocidad", "v": "lento"}, st(60), {"op": "esperar", "s": 1}, st(40)]),
        ("sentarse", "Sentarse", "Traseras muy dobladas, delanteras casi rectas",
         [{"op": "velocidad", "v": "lento"}, st(40),
          S(stance(c, 60, legs=["C", "D"]) + stance(c, 20, legs=["A", "B"]))]),
        ("reverencia", "Estirarse (reverencia)", "Delanteras dobladas, traseras rectas, y vuelta",
         [{"op": "velocidad", "v": "lento"}, st(40),
          S(stance(c, 60, legs=["A", "B"]) + stance(c, 25, legs=["C", "D"])), {"op": "esperar", "s": 1.5}, st(40)]),
        ("flexiones", "Flexiones", "Sube y baja 3 veces",
         [st(40), {"op": "repetir", "n": 3, "pasos": [st(55), st(30)]}, st(40)]),
        ("balanceo", "Balancearse a los lados", "Pectorales a un lado y al otro, 3 veces",
         [st(40), {"op": "repetir", "n": 3, "pasos": [
             S([{"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", 12 if L in ("A", "D") else -12, c)} for L in LEGS]),
             S([{"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", -12 if L in ("A", "D") else 12, c)} for L in LEGS])]},
          st(40)]),
        ("saludar-b", "Saludar con la pata B", "Levanta la delantera derecha y la mueve",
         [st(40), S([{"pata": "B", "art": "hombro", "angulo": dev_angle("B", "hombro", 55, c)},
                     {"pata": "B", "art": "codo", "angulo": dev_angle("B", "codo", 20, c)}]),
          {"op": "repetir", "n": 3, "pasos": [
              S([{"pata": "B", "art": "pectoral", "angulo": dev_angle("B", "pectoral", 15, c)}]),
              S([{"pata": "B", "art": "pectoral", "angulo": dev_angle("B", "pectoral", -15, c)}])]},
          st(40)]),
        ("marcar-paso", "Marcar el paso (trote en el sitio)", "Levanta en diagonal A+C y luego B+D, 4 veces",
         [st(40), {"op": "repetir", "n": 4, "pasos": [
             S([{"pata": L, "art": "codo", "angulo": dev_angle(L, "codo", 58, c)} for L in ("A", "C")]),
             S(stance(c, 40, legs=["A", "C"])),
             S([{"pata": L, "art": "codo", "angulo": dev_angle(L, "codo", 58, c)} for L in ("B", "D")]),
             S(stance(c, 40, legs=["B", "D"]))]}]),
        ("andar-corto", "Andar hacia delante (paso corto)", "Una pata cada vez, 2 ciclos", walk_program(c, 12, 12)),
        ("andar-largo", "Andar hacia delante (paso largo)", "Una pata cada vez, 2 ciclos", walk_program(c, 20, 20)),
        ("andar-atras", "Andar hacia atrás", "Una pata cada vez, 2 ciclos", walk_program(c, -12, -12)),
        ("girar-izq", "Girar a la izquierda", "Izquierdas hacia atrás, derechas hacia delante", walk_program(c, -12, 12)),
        ("girar-der", "Girar a la derecha", "Izquierdas hacia delante, derechas hacia atrás", walk_program(c, 12, -12)),
    ]
    for rid, name, desc, prog in two:
        cands.append({"id": rid, "etapa": 2, "nombre": name, "desc": desc, "programa": prog})
    return cands


def save_convention(conv):
    cfg = load_config()
    cfg["convencion"] = conv
    write_json(CONFIG_FILE, cfg)
    poses = load_poses()  # posturas básicas para los botones Recoger/Estirar y la tecla R/E del panel
    for name, beta in (("de pie", 40), ("estirada", 10), ("recogida", 60)):
        for o in stance(conv, beta):
            poses.setdefault(o["pata"], {}).setdefault(name, {})[o["art"]] = o["angulo"]
    write_json(POSES_FILE, poses)


def routines_info():
    cfg = load_config()
    conv = cfg.get("convencion")
    saved = load_json(ROUTINES_FILE, {})
    return {"guardadas": {k: {"origen": v.get("origen", ""), "pasos": count_steps(v["programa"]),
                              "bloques": bool(v.get("bloques"))} for k, v in saved.items()},
            "candidatas": [{k: v for k, v in c.items() if k != "programa"} | {"pasos": count_steps(c["programa"])}
                           for c in candidate_routines(conv)],
            "convencion": conv, "pruebas": cfg.get("pruebas", {}), "ejecutando": dict(routine)}


# ---------- pestaña de estado: cómo me estoy conectando ----------

def connection_info(client, host):
    s = system_info()
    if client.startswith("127.") or client == "::1":
        via = "desde la propia Pi (pantalla HDMI o túnel SSH)"
    elif client.startswith("169.254."):
        via = "por cable directo entre el PC y la Pi (sin router)"
    elif client.startswith("fe80:"):
        via = "por IPv6 de enlace local (cable directo)"
    elif s["net"]["gateway"]:
        via = "a través del router"
    else:
        via = "por la red local"
    keys = [k.split()[-1] for k in read_text(Path.home() / ".ssh" / "authorized_keys").splitlines() if k.strip()]
    ssh_ip = next((a["ip"] for a in s["net"]["ipv4"] if a["if"] == "eth0" and a["ip"] != LINK_LOCAL_IP), LINK_LOCAL_IP)
    return {
        "cliente": client, "host": host, "via": via, "modo": s["net"]["mode"], "gateway": s["net"]["gateway"],
        "internet": bool(s["net"]["gateway"]), "ipv4": s["net"]["ipv4"], "ipv6_ll": s["net"]["ipv6_ll"],
        "hostname": s["net"]["hostname"], "urls": s["net"]["urls"], "ports": s["ports"],
        "web_credencial": "sin contraseña: cualquiera conectado a la misma red puede usarla",
        "ssh": {"usuario": os.environ.get("USER") or Path.home().name, "claves": keys,
                "contrasena": "la del usuario de la Pi (apuntada en NOTAS-LOCALES.md, que no se sube a GitHub)",
                "comandos": [f"ssh luislecinana@{ssh_ip}", f"ssh luislecinana@{LINK_LOCAL_IP}",
                             f"ssh -L 8080:localhost:8080 luislecinana@{IPV6_LL}%<ifIndex>"]},
        "rutas": {"web": s["net"]["urls"], "ayuda": "/ayuda", "api": ["/api/state", "/api/system", "/api/routines",
                  "/api/informe", "/api/conexion"], "carpeta_en_la_pi": str(ROOT)},
        "pi": {"uptime_s": s["uptime_s"], "hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "throttled": s["throttled"], "temp_c": s["temp_c"]},
    }


# ---------- informe para la mudanza ----------

def report():
    items = []

    def add(area, item, ok, detail="", warn=False):
        items.append({"area": area, "item": item, "estado": "ok" if ok else ("aviso" if warn else "fallo"), "detalle": detail})

    def active(u):
        return run(["systemctl", "is-active", u], timeout=3) == "active"
    s = system_info()
    add("Servicios", "Web del robot (spotmicro-web)", True, f"puertos {s['ports']}")
    add("Servicios", "Pantalla HDMI (spotmicro-pantalla o consola)", active("spotmicro-pantalla.service") or active("getty@tty1.service"))
    add("Servicios", "Navegador para la pantalla (cage + chromium)", s["screen"]["navegador_instalado"],
        "sin él, la opción 'página web' de la pantalla no funciona", warn=True)
    add("Servicios", "SSH", active("ssh.service"))
    keys = read_text(Path.home() / ".ssh" / "authorized_keys").splitlines()
    names = [k.split()[-1] for k in keys if k.strip()]
    add("Servicios", "Claves SSH autorizadas (PC de sobremesa y portátil)", len(names) >= 2, ", ".join(names), warn=True)
    prof = {k: run(["nmcli", "-g", k, "con", "show", "spotmicro-eth0"])
            for k in ("GENERAL.STATE", "connection.autoconnect", "connection.autoconnect-priority")}
    add("Red", "Perfil de red 'spotmicro-eth0' activo y automático",
        prof["GENERAL.STATE"] == "activated" and prof["connection.autoconnect"] == "yes" and prof["connection.autoconnect-priority"] == "100",
        " · ".join(f"{k.split('.')[-1]}={v or '?'}" for k, v in prof.items()))
    ips = [a["ip"] for a in s["net"]["ipv4"]]
    add("Red", f"Dirección fija para cable directo {LINK_LOCAL_IP}", LINK_LOCAL_IP in ips, ", ".join(ips))
    add("Red", f"IPv6 fija {IPV6_LL}", s["net"]["ipv6_ll"] == IPV6_LL, s["net"]["ipv6_ll"])
    add("Red", "Modo de red ahora", True, s["net"]["mode"])
    th = s["throttled"] or 0
    add("Alimentación", "Ahora", not th & 1, ", ".join(s["throttled_txt"]) or "sin avisos")
    add("Alimentación", "Caídas de tensión en este arranque", s["caidas_tension_arranque"] == 0, str(s["caidas_tension_arranque"]), warn=True)
    golpes = [ln for ln in read_text(ENERGY_LOG).splitlines() if "DE GOLPE" in ln]
    add("Alimentación", "Arranques tras corte o cuelgue (registro)", not golpes,
        f"{len(golpes)} en total; el último: {golpes[-1][:19]}" if golpes else "ninguno", warn=True)
    add("Sistema", "Watchdog (reinicia la Pi si se cuelga)",
        run(["systemctl", "show", "-p", "RuntimeWatchdogUSec", "--value"]) not in ("", "0", "infinity"))
    add("Sistema", "Journal persistente (logs tras un corte)", Path("/var/log/journal").is_dir())
    vig = read_text(ROOT / "vigilante.log").splitlines()
    add("Sistema", "Vigilante (arregla solo red, web, pantalla y LED cada minuto)", active("spotmicro-vigilante.timer"),
        f"último arreglo: {vig[-1]}" if vig else "sin arreglos necesarios")
    du = shutil.disk_usage("/")
    add("Sistema", "Espacio libre en la SD", du.free > 1e9, f"{du.free / 1e9:.1f} GB", warn=True)
    fs = run(["sudo", "-n", "/usr/sbin/tune2fs", "-l", "/dev/mmcblk0p2"])
    add("Sistema", "Sistema de archivos sin errores", "Filesystem state:         clean" in fs,
        next((ln.split(":", 1)[1].strip() for ln in fs.splitlines() if ln.startswith("Filesystem state")), "?"))
    ntp = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    add("Sistema", "Hora sincronizada", ntp == "yes", "sin internet la hora puede ir mal (la Pi no tiene reloj)", warn=True)
    pca = run(["/usr/sbin/i2cget", "-y", "1", "0x40", "0xfe"])
    add("Robot", "Placa de servos PCA9685 responde (0x40)", pca.startswith("0x"), f"PRESCALE={pca or 'sin respuesta'}")
    m = load_map()["canales"]
    assigned = {(v.get("pata"), v.get("articulacion")) for v in m.values() if v.get("pata")}
    notes = [f"canal {k}: {v['nota']}" for k, v in m.items() if v.get("nota") and v.get("pata")]
    add("Robot", "Mapa de servos (12 articulaciones)", len(assigned) == 12, f"{len(assigned)}/12" + (" · " + "; ".join(notes) if notes else ""),
        warn=True)
    conv = load_config().get("convencion")
    add("Robot", "Sentido de las articulaciones encontrado (rutina 'De pie' marcada ✓)", bool(conv), str(conv or "pendiente"), warn=True)
    add("Robot", "Rutinas guardadas", True, ", ".join(load_json(ROUTINES_FILE, {}).keys()) or "ninguna todavía")
    dirty = run(["git", "-C", str(ROOT), "status", "--porcelain"])
    last = run(["git", "-C", str(ROOT), "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M"])
    add("Copias", "Cambios guardados en git", not dirty, f"último: {last}" + (" · hay cambios sin guardar" if dirty else ""), warn=True)
    summary = {e: sum(1 for i in items if i["estado"] == e) for e in ("ok", "aviso", "fallo")}
    return {"items": items, "resumen": summary, "hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


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


# ---------- límite de consumo de la fuente de los servos ----------
I_MOVE, I_HOLD = 1.2, 0.3   # amperios estimados por servo MG996R moviéndose / sujetando
power_info = {"ultima_tanda": None}


def power_cfg():
    p = load_json(CONFIG_FILE, {}).get("potencia") or {}
    return {"limitar": p.get("limitar", True), "amperios": float(p.get("amperios", 5)),
            "permitir_sin_corriente": bool(p.get("permitir_sin_corriente", False))}


# ---------- memoria de posición: el último ángulo mandado a cada servo sobrevive a cortes y reinicios ----------
LAST_FILE = ROOT / "ultima_posicion.json"
last_known = {}          # canal -> último ángulo lógico mandado (aunque luego se soltara)
last_info = {"hora": None}


def load_last():
    d = load_json(LAST_FILE, {})
    for k, v in (d.get("angulos") or {}).items():
        last_known[int(k)] = v
    last_info["hora"] = d.get("hora")


def save_last():
    changed = False
    for ch, a in angles.items():
        if a is not None and last_known.get(ch) != a:
            last_known[ch] = a
            changed = True
    if changed:
        last_info["hora"] = datetime.now().isoformat(timespec="seconds")
        write_json(LAST_FILE, {"angulos": {str(k): v for k, v in sorted(last_known.items())}, "hora": last_info["hora"]})


def low_power_now():
    t = power["throttled"] if power["throttled"] is not None else read_throttled()
    return bool(t and t & 1)


def power_guard():
    """Freno: con la Pi en falta de corriente, mover servos la reinicia (pasó 4 veces el 11-10-2026)."""
    if low_power_now() and not power_cfg()["permitir_sin_corriente"]:
        raise ValueError("FALTA DE CORRIENTE en la Pi ahora mismo: mover servos puede apagarla (ya pasó). "
                         "Revisa el GND de la clema y el cargador/cable de la Pi, o marca arriba "
                         "'Permitir mover aunque falte corriente'.")


def max_movers(n_targets, n_held_others):
    """Cuántos servos pueden moverse a la vez sin pasar de los amperios de la fuente."""
    cfg = power_cfg()
    if not cfg["limitar"]:
        return n_targets
    k = (cfg["amperios"] - (n_held_others + n_targets) * I_HOLD) / (I_MOVE - I_HOLD)
    return max(1, min(n_targets, int(k)))


def move_many(targets, pause=0.03):
    """Mueve varios canales a la vez, 1 grado por servo en cada paso (alternando entre ellos),
    para que todas las patas vayan juntas y suaves. Con el límite de consumo activado, los reparte
    en tandas para no pasar de los amperios de la fuente."""
    targets = {ch: clamp(a) for ch, a in targets.items()}
    held_others = sum(1 for ch, a in angles.items() if a is not None and ch not in targets)
    k = max_movers(len(targets), held_others)
    power_info["ultima_tanda"] = k
    if k < len(targets):
        items = list(targets.items())
        for i in range(0, len(items), k):
            _move_together(dict(items[i:i + k]), pause)
        return
    _move_together(targets, pause)


moving = set()                       # canales que se están moviendo ahora (para el consumo en tiempo real)
traffic = {"rechazadas": 0, "ultima_rechazada": 0.0}
MOVE_PATHS = ("/api/test", "/api/test_many", "/api/angle", "/api/pose90", "/api/group", "/api/pose_go", "/api/lock_loose",
              "/api/leg_release", "/api/release")


def consumption():
    """Consumo estimado de los servos ahora mismo: suelto 0 · sujetando ~0,3 A · moviéndose ~1,2 A."""
    chans, total = {}, 0.0
    for k, m in load_map()["canales"].items():
        if not m.get("pata"):
            continue
        ch = int(k)
        est = "moviendo" if ch in moving else "sujeto" if angles[ch] is not None else "suelto"
        amps = {"moviendo": I_MOVE, "sujeto": I_HOLD, "suelto": 0.0}[est]
        total += amps
        chans[k] = {"pata": m["pata"], "art": m["articulacion"], "estado": est, "A": amps}
    pc = power_cfg()
    return {"canales": chans, "total_A": round(total, 2), "limite_A": pc["amperios"], "limitar": pc["limitar"],
            "falta_corriente": low_power_now(), "permitir_sin_corriente": pc["permitir_sin_corriente"],
            "rutina": routine["nombre"] if routine["running"] else "",
            "rechazadas_recientes": traffic["rechazadas"] if time.time() - traffic["ultima_rechazada"] < 8 else 0}


def _move_together(targets, pause):
    moving.update(targets)
    try:
        _move_together_inner(targets, pause)
    finally:
        moving.difference_update(targets)


def _move_together_inner(targets, pause):
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
    moving.add(ch)
    try:
        _move_to(ch, target, pause)
    finally:
        moving.discard(ch)


def _move_to(ch, target, pause=0.03):
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
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # el cliente cerró antes de recibir la respuesta (p. ej. cambió de página)

    def log_message(self, *args):
        pass

    def state(self):
        pc = power_cfg()
        potencia = dict(pc, a_la_vez_12=max_movers(12, 0), estimacion={"moviendo_A": I_MOVE, "sujetando_A": I_HOLD},
                        ultima_tanda=power_info["ultima_tanda"], falta_corriente_ahora=low_power_now())
        cfg = load_config()
        return {"angles": angles, "map": load_map()["canales"], "poses": load_poses(), "rutina": dict(routine),
                "potencia": potencia, "convencion": cfg.get("convencion"), "verificacion": cfg.get("verificacion", {}),
                "ultima": {"angulos": {str(k): v for k, v in last_known.items()}, "hora": last_info["hora"]},
                "limits": {"min": SAFE_MIN, "max": SAFE_MAX, "max_delta": MAX_DELTA}}

    STATIC_TYPES = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
                    ".gif": "image/gif", ".mp3": "audio/mpeg", ".cur": "image/x-icon", ".txt": "text/plain; charset=utf-8"}

    def static(self, path):
        f = (STATIC_DIR / path[len("/static/"):]).resolve()
        if STATIC_DIR.resolve() not in f.parents or not f.is_file():
            self._send(404, {"error": "no existe"})
            return
        data = f.read_bytes()
        try:
            self.send_response(200)
            self.send_header("Content-Type", self.STATIC_TYPES.get(f.suffix, "application/octet-stream"))
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._send(200, self.state())
        elif path == "/api/system":
            self._send(200, system_info())
        elif path == "/api/routines":
            self._send(200, routines_info())
        elif path == "/api/informe":
            self._send(200, report())
        elif path == "/api/consumo":
            self._send(200, consumption())
        elif path == "/api/conexion":
            self._send(200, connection_info(self.client_address[0], self.headers.get("Host", "")))
        elif path in ("/ayuda", "/ayuda.txt"):
            self._send(200, HELP_FILE.read_bytes() if HELP_FILE.exists() else b"sin ayuda", "text/plain; charset=utf-8")
        elif path.startswith("/static/"):
            self.static(path)
        else:
            self._send(404, {"error": "no existe"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            path = urlsplit(self.path).path
            if path == "/api/leg_check":  # "¿la pata está de verdad como la dibuja la Pi?"
                leg = body.get("pata")
                if leg not in LEGS:
                    raise ValueError("pata no válida")
                cfg = load_config()
                ver = cfg.setdefault("verificacion", {})
                if body.get("ok") is None:
                    ver.pop(leg, None)
                else:
                    ver[leg] = {"ok": bool(body["ok"]), "hora": datetime.now().isoformat(timespec="seconds"),
                                "angulos": {j: angles[ch] for j, ch in leg_channels(leg).items()}}
                write_json(CONFIG_FILE, cfg)
                log(f"pata {leg}: el dibujo {'COINCIDE' if body.get('ok') else 'NO coincide' if body.get('ok') is not None else 'sin comprobar'} con el robot")
                self._send(200, self.state())
                return
            if path == "/api/power":
                amps = float(body.get("amperios", 5))
                if not 1 <= amps <= 60:
                    raise ValueError("amperios entre 1 y 60")
                cfg = load_config()
                cfg["potencia"] = {"limitar": bool(body.get("limitar", True)), "amperios": amps,
                                   "permitir_sin_corriente": bool(body.get("permitir_sin_corriente", False))}
                write_json(CONFIG_FILE, cfg)
                log(f"límite de consumo: {'activado' if cfg['potencia']['limitar'] else 'DESACTIVADO'} · fuente {amps:g} A "
                    f"· {max_movers(12, 0)} servos a la vez")
                self._send(200, self.state())
                return
            if path in ("/api/screen", "/api/screen_boot", "/api/system_action"):
                self.route_system(body)  # no tocan servos: sin esperar al cerrojo de movimiento
                return
            if path.startswith("/api/routine") or path in ("/api/try_mark", "/api/convention_reset"):
                self.route_routines(path, body)
                return
            if path == "/api/off":
                routine["stop"] = True  # parar cualquier rutina antes de soltar
            elif routine["running"]:
                raise ValueError(f"hay una rutina en marcha ('{routine['nombre']}'): párala primero")
            if path in MOVE_PATHS and path not in ("/api/release", "/api/leg_release"):
                power_guard()
            if path in MOVE_PATHS:
                # si el robot ya se está moviendo, NO se encola: con varios botones a la vez se pasa de amperios
                if not lock.acquire(timeout=1.0):
                    traffic["rechazadas"] += 1
                    traffic["ultima_rechazada"] = time.time()
                    self._send(409, {"error": "el robot todavía se está moviendo: espera a que termine "
                                              "(no pulses varios botones a la vez)"})
                    return
                try:
                    self.route(body)
                finally:
                    lock.release()
                return
            with lock:
                self.route(body)
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log(f"ERROR {self.path}: {e!r}")
            self._send(500, {"error": repr(e)})

    def route_routines(self, p, b):
        if p == "/api/routine_run":
            if b.get("candidata"):
                cand = next((c for c in candidate_routines(load_config().get("convencion")) if c["id"] == b["candidata"]), None)
                if not cand:
                    raise ValueError("rutina de prueba desconocida")
                start_routine(cand["programa"], cand["nombre"])
            elif b.get("nombre") and not b.get("programa"):
                r = load_json(ROUTINES_FILE, {}).get(b["nombre"])
                if not r:
                    raise ValueError(f"no existe la rutina '{b['nombre']}'")
                start_routine(r["programa"], b["nombre"])
            else:
                start_routine(b.get("programa"), b.get("nombre") or "programa de bloques")
        elif p == "/api/routine_stop":
            routine["stop"] = True
            log("rutina: parar pedido")
        elif p == "/api/routine_save":
            name = str(b.get("nombre", "")).strip()[:40]
            if not name:
                raise ValueError("ponle un nombre a la rutina")
            if b.get("candidata"):
                cand = next((c for c in candidate_routines(load_config().get("convencion")) if c["id"] == b["candidata"]), None)
                if not cand:
                    raise ValueError("rutina de prueba desconocida")
                prog, origen, blocks = cand["programa"], f"prueba: {cand['nombre']}", None
            else:
                prog, origen, blocks = b.get("programa"), "bloques", b.get("bloques")
            if not isinstance(prog, list) or not prog:
                raise ValueError("programa vacío")
            count_steps(prog)
            saved = load_json(ROUTINES_FILE, {})
            saved[name] = {"programa": prog, "bloques": blocks, "origen": origen, "guardada": datetime.now().isoformat(timespec="seconds")}
            write_json(ROUTINES_FILE, saved)
            log(f"rutina '{name}' guardada ({origen})")
        elif p == "/api/routine_delete":
            saved = load_json(ROUTINES_FILE, {})
            if saved.pop(b.get("nombre", ""), None) is None:
                raise ValueError("no existe esa rutina")
            write_json(ROUTINES_FILE, saved)
            log(f"rutina '{b.get('nombre')}' borrada")
        elif p == "/api/routine_get":
            if b.get("candidata"):
                cand = next((c for c in candidate_routines(load_config().get("convencion")) if c["id"] == b["candidata"]), None)
                if not cand:
                    raise ValueError("rutina de prueba desconocida")
                self._send(200, {"programa": cand["programa"], "bloques": None, "nombre": cand["nombre"]})
                return
            r = load_json(ROUTINES_FILE, {}).get(b.get("nombre", ""))
            if not r:
                raise ValueError("no existe esa rutina")
            self._send(200, r)
            return
        elif p == "/api/try_mark":
            cid, mark = b.get("id", ""), b.get("mark", "")
            cands = {c["id"]: c for c in candidate_routines(load_config().get("convencion"))}
            if cid not in cands:
                raise ValueError("rutina de prueba desconocida")
            cfg = load_config()
            pruebas = cfg.setdefault("pruebas", {})
            if mark in ("bien", "mal"):
                pruebas[cid] = {"mark": mark, "hora": datetime.now().isoformat(timespec="seconds")}
            else:
                pruebas.pop(cid, None)
            write_json(CONFIG_FILE, cfg)
            if mark == "bien" and cands[cid].get("etapa") == 1:
                save_convention(cands[cid]["conv"])
                log(f"convención guardada desde '{cands[cid]['nombre']}': {cands[cid]['conv']} (+ posturas de pie/estirada/recogida)")
            log(f"prueba '{cid}' marcada: {mark or 'sin marca'}")
        elif p == "/api/convention_reset":
            cfg = load_config()
            cfg.pop("convencion", None)
            write_json(CONFIG_FILE, cfg)
            log("convención borrada: vuelta a la etapa 1")
        else:
            self._send(404, {"error": "no existe"})
            return
        self._send(200, routines_info())

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
        elif p == "/api/system_action" and b.get("accion") == "instalar-navegador":
            if system_info()["screen"]["instalando_navegador"]:
                raise ValueError("ya se está instalando")
            if not system_info()["net"]["gateway"]:
                raise ValueError("hace falta internet (router) para instalar el navegador")
            subprocess.Popen(["sudo", "-n", "systemd-run", f"--unit={INSTALL_UNIT}", "--description=LUIS LECI control: instalar navegador",
                              str(INSTALL_SCRIPT)])
            log("instalación del navegador lanzada desde la web")
        elif p == "/api/system_action":
            action = b.get("accion")
            if action not in ("apagar", "reiniciar"):
                raise ValueError("acción no válida")
            routine["stop"] = True
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
        elif p == "/api/lock_loose":  # sujetar los sueltos donde estaban (memoria) o a 90°, sin tocar los demás
            where = b.get("a", "ultima")
            order = interleaved({leg: {j: ch for j, ch in leg_channels(leg).items() if angles[ch] is None} for leg in LEGS})
            if order:
                move_many({ch: (last_known.get(ch, CENTER) if where == "ultima" else CENTER) for _, _, ch in order}, pause_of(b))
            log(f"sujetar los sueltos ({where}): {[f'{l}{JOINTS.index(j) + 1}' for l, j, _ in order] or 'ninguno suelto'}")
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
            # de dónde sale cada ángulo: el dibujo (copiar del robot), el servo sujeto o, si se pide, la memoria
            explicit = {int(k): clamp(v) for k, v in (b.get("angulos") or {}).items()}
            use_last = bool(b.get("usar_ultima"))
            poses, saved, sources = load_poses(), {}, {"dibujo": 0, "sujeto": 0, "memoria": 0}
            for leg in legs:
                pose = {}
                for j, ch in leg_channels(leg).items():
                    if ch in explicit:
                        pose[j] = explicit[ch]
                        sources["dibujo"] += 1
                    elif angles[ch] is not None:
                        pose[j] = angles[ch]
                        sources["sujeto"] += 1
                    elif use_last and ch in last_known:
                        pose[j] = last_known[ch]
                        sources["memoria"] += 1
                if pose:
                    poses.setdefault(leg, {})[name] = pose
                    saved[leg] = pose
            if not saved:
                raise ValueError("no hay ángulos que guardar: los servos están sueltos y no hay posición conocida")
            write_json(POSES_FILE, poses)
            log(f"postura '{name}' guardada ({sources}): {saved}")
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
        elif p == "/api/pose_delete":
            name, poses = b.get("nombre", ""), load_poses()
            found = False
            for leg in LEGS:
                found |= poses.get(leg, {}).pop(name, None) is not None
            if not found:
                raise ValueError(f"no hay postura '{name}'")
            write_json(POSES_FILE, poses)
            log(f"postura '{name}' borrada")
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
        save_last()  # memoria de posición en la SD (sobrevive a cortes)
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
    load_last()
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
