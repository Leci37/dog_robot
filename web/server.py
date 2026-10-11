#!/home/luislecinana/robot/bin/python
"""Web de control del SpotMicro.

http://<ip-de-la-pi> (puerto 80) y :8080  ·  servicio systemd: spotmicro-web
Guarda el mapa en ../servo_map.json, las posturas en ../poses.json, un log de acciones en
acciones.log y un registro permanente de arranques y cortes de corriente en ../energia.log.
"""
import json
import math
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
SPEEDS = {"lento": 0.05, "normal": 0.03, "rapido": 0.012,  # segundos por grado
          "suave": 0.1}  # para levantar el cuerpo: las 4 patas juntas, muy despacio
# marcha de las rutinas (config.json "marcha"): multiplica las pausas por grado y las esperas; se cambia en marcha
MARCHAS = {1: ("1ª muy lenta", 2.0), 2: ("2ª lenta", 1.4), 3: ("3ª normal", 1.0), 4: ("4ª rápida", 0.6),
           5: ("5ª muy rápida", 0.35), 6: ("6ª turbo", 0.18)}  # 6ª: el usuario "más rápido aún"
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
# ---------- modo Toreto: máxima potencia (12-10, Luis: "como en Fast and Furious, no me importa si rompo algo") ----------
# Toreto: 1) movimientos sin interpolar: salto directo con una "patada" de unos grados más allá del objetivo (error
# grande = el servo mete toda la fuerza) y vuelta; 2) todo a la vez, sin tandas del límite de consumo.
# Nitro (aparte, opcional y con tiempo): refresco a 200 Hz en vez de 50. Un MG996R analógico empuja el motor en cada
# pulso: más fuerza y respuesta, pero se calienta y puede quemarse en minutos; por eso se apaga solo a los NITRO_S.
TORETO_HZ, NORMAL_HZ = 200, 50
TORETO_PATADA = 6            # grados de más al arrancar cada movimiento
TORETO_S_POR_GRADO = 0.003   # lo que tarda el servo (MG996R ~0,17 s/60° a 5 V): espera tras el salto
NITRO_S = 120
toreto = {"on": False, "nitro": False, "hz": NORMAL_HZ, "nitro_hasta": 0.0}
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


def undervoltage_count(boot):
    out = run(["journalctl", "-k", "-b", boot, "-q", "--no-pager", "-g", "Undervoltage detected"], timeout=10)
    return len(out.splitlines()) if out else 0


_apg_cache = {"t": 0, "data": None}


def outages():
    """Apagados de golpe: los arranques del journal persistente que no terminaron con APAGADO-LIMPIO
    (hora real del último registro y caídas de tensión antes de morir) + los anteriores al journal."""
    if _apg_cache["data"] and time.time() - _apg_cache["t"] < 60:
        return _apg_cache["data"]
    try:
        lines = ENERGY_LOG.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        lines = []
    clean = {ln.split("boot=")[1].split()[0] for ln in lines if "APAGADO-LIMPIO boot=" in ln}
    lst, seen = [], set()
    try:
        boots = json.loads(run(["journalctl", "--list-boots", "-o", "json", "--no-pager"], timeout=10) or "[]")
    except json.JSONDecodeError:
        boots = []
    for b in boots:
        bid = b.get("boot_id", "")[:8]
        seen.add(bid)
        if b.get("index") == 0 or bid in clean:
            continue
        n = undervoltage_count(b["boot_id"])
        hora = datetime.fromtimestamp(b.get("last_entry", 0) / 1e6).strftime("%Y-%m-%d %H:%M")
        lst.append({"hora": hora, "causa": "falta de corriente" if n else "corte total o cuelgue", "caidas": n})
    prev_id = None  # anteriores al journal persistente: solo sabemos que terminaron de golpe
    for ln in lines:
        if " ARRANQUE " not in ln:
            continue
        if "DE GOLPE" in ln and prev_id and prev_id not in seen:
            lst.append({"hora": ln[:16] + " (aprox.)", "causa": "sin detalle (antes del registro completo)", "caidas": 0})
        prev_id = ln.split("boot=")[1].split()[0].rstrip(";")
    lst.sort(key=lambda x: x["hora"])
    data = {"total": len(lst), "por_corriente": sum(1 for x in lst if x["causa"] == "falta de corriente"), "lista": lst[-20:]}
    _apg_cache.update(t=time.time(), data=data)
    return data


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
        if not clean:
            n = undervoltage_count("-1")
            how += ("; causa probable: FALTA DE CORRIENTE (%d caídas de tensión antes de apagarse)" % n if n else
                    "; causa probable: corte total o cuelgue (sin caídas de tensión registradas)")
    else:
        how = "desconocido (primer registro)"
    t = read_throttled()
    up = float(Path("/proc/uptime").read_text().split()[0])
    energy_log(f"ARRANQUE boot={bid}; la Pi lleva {up:.0f} s encendida; el arranque anterior terminó: {how}; "
               f"throttled={hex(t) if t is not None else '?'}")


def on_sigterm(signum, frame):
    if toreto["nitro"]:  # al volver a arrancar la librería pone 50 Hz: que los pulsos sigan siendo los mismos
        try:
            set_nitro(False)
        except Exception:  # noqa: BLE001
            pass
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
        "apagones": outages(),
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

routine = {"running": False, "nombre": "", "paso": 0, "total": 0, "error": "", "stop": False, "fin": "",
           "detalle": "", "lista": [], "bucle": False, "vuelta": 1}  # detalle/lista: lo que hace ahora y los pasos con
# nombre (barra de "Bloquear todo"); bucle/vuelta: "repetir sin parar" (12-10) y en qué vuelta va


class RoutineStop(Exception):
    pass


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_config():
    return load_json(CONFIG_FILE, {})


def gear():
    n = load_config().get("marcha", 3)
    return n if n in MARCHAS else 3


def gear_factor():
    return MARCHAS[gear()][1]


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
            wait_s(float(st.get("s", 0.5)) * gear_factor())
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
            if op == "bloquear":
                lock_step(st, ctx)
            else:
                with lock:  # cada paso con el cerrojo de servos; entre pasos la web sigue respondiendo
                    run_move(st, ctx)
                    save_last()
        routine["paso"] += 1


def lock_step(st, ctx):
    """Un paso de "Bloquear todo": sujeta uno (o varios) en su ángulo, lo apunta en la memoria de posición,
    espera un poco para que se vea y, en el modo bloquea-y-suelta, lo vuelve a soltar."""
    routine["detalle"] = st.get("texto", "")
    targets = {int(o["ch"]): clamp(o["angulo"]) for o in st.get("objetivos", [])}
    with lock:
        if targets:
            move_many(targets, ctx["pause"])
        save_last()
    wait_s(st.get("s", 0))
    if st.get("soltar"):
        with lock:
            for ch in targets:
                release(ch)


def plan_lock_all(b):
    """"Bloquear todo y apuntar": deja los servos sujetos justo en el ángulo que la Pi tiene apuntado (el que ya
    sujetan o, si están sueltos, el último que se les mandó; sin memoria, 90°) y lo guarda en la memoria, para que
    lo que cree la Pi y el robot de verdad coincidan antes de guardar una postura.
    Si la fuente aguanta, todos a la vez; si no, uno a uno, pata a pata (pectoral, hombro, codo) con una pausa
    de 0,3-0,5 s para que se vea en la barra; y si ni siquiera hay corriente para dejarlos todos sujetos (o se pide
    soltar), bloquea-apunta-suelta cada uno. Los servos que ya estaban sujetos no se sueltan nunca."""
    secs = max(0.2, min(2.0, float(b.get("segundos", 0.4))))
    objs = []
    for leg in LEGS:
        chs = leg_channels(leg)
        for art in JOINTS:
            if art not in chs:
                continue
            ch = chs[art]
            if angles[ch] is not None:
                a, src = angles[ch], "ya sujeto"
            elif ch in last_known:
                a, src = last_known[ch], "memoria"
            else:
                a, src = CENTER, "sin memoria: 90°"
            objs.append({"ch": ch, "angulo": clamp(a), "origen": src, "tag": f"{leg}{JOINTS.index(art) + 1} {art}"})
    if not objs:
        raise ValueError("no hay servos en el mapa")
    loose = [o for o in objs if o["origen"] != "ya sujeto"]
    pc = power_cfg()
    hold_all = not pc["limitar"] or len(objs) * I_HOLD + (I_MOVE - I_HOLD) <= pc["amperios"]
    soltar = bool(b.get("soltar")) or not hold_all
    lista = [f"{o['tag']} · {o['angulo']}° ({o['origen']})" for o in objs]
    if not soltar and max_movers(len(loose), len(objs) - len(loose)) >= len(loose):
        prog = [{"op": "bloquear", "objetivos": loose, "s": secs,
                 "texto": f"bloqueando los {len(loose)} sueltos a la vez" if loose else "los 12 ya estaban sujetos: apuntando"}]
        return prog, "Bloquear todo (a la vez)", ["todos a la vez: " + ", ".join(o["tag"].split()[0] for o in objs)]
    prog = [{"op": "bloquear", "objetivos": [o], "s": secs, "soltar": soltar and o["origen"] != "ya sujeto",
             "texto": f"bloqueando {txt}"} for o, txt in zip(objs, lista)]
    return prog, "Bloquear todo (uno a uno" + (", bloquea y suelta)" if soltar else ")"), lista


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
        move_many(targets, ctx["pause"] * gear_factor(), juntos=op == "set" and bool(st.get("juntos")))


def start_routine(program, name, lista=None, loop=False):
    if routine["running"]:
        raise ValueError(f"ya se está ejecutando '{routine['nombre']}': párala primero")
    if not isinstance(program, list) or not program:
        raise ValueError("programa vacío")
    routine.update(running=True, nombre=name, paso=0, total=count_steps(program), error="", stop=False, fin="",
                   detalle="", lista=lista or [], bucle=bool(loop), vuelta=1)
    ctx = {"pause": SPEEDS["normal"], "conv": load_config().get("convencion") or {}}

    def worker():
        try:
            while True:  # con "repetir sin parar", vuelve a empezar hasta que se pulse Parar / Soltar todo
                run_steps(program, ctx)
                if not loop:
                    break
                wait_s(0.2)
                routine.update(vuelta=routine["vuelta"] + 1, paso=0)
            routine["fin"] = "terminada"
        except RoutineStop:
            routine["fin"] = "parada"
        except Exception as e:  # noqa: BLE001
            routine["error"] = str(e)
            routine["fin"] = "error"
        finally:
            routine["running"] = False
            log(f"rutina '{name}': {routine['fin']}{' - ' + routine['error'] if routine['error'] else ''}")

    log(f"rutina '{name}' empieza ({routine['total']} pasos{', repitiendo sin parar' if loop else ''})")
    threading.Thread(target=worker, daemon=True).start()


# ---------- rutinas para probar (generadas) ----------
# Patas de longitud parecida: para agacharse con la pezuña bajo la cadera, el codo se dobla beta y el
# hombro beta/2. El 11-10-2026 el usuario probó las 8 combinaciones de sentido (signo del hombro sh, del codo
# sk, delanteras igual o invertidas fi) y la buena fue la variante 6: hombro −, codo +, delanteras invertidas.
# La etapa 1 son ahora la 6 y sus variantes (baja, alta, inclinada, pectorales); al marcar ✓ una se guarda la
# convención con su altura y la etapa 2 genera rutinas (de pie, tumbarse, sentarse, andar…).

CONV6 = {"sh": -1, "sk": 1, "fi": True}
VARIANTES_6 = [  # (id, nombre, descripción, agachado delanteras, agachado traseras, pectorales)
    ("6", "De pie 6", "la ganadora (agachado 40)", 40, 40, 0),
    ("6.1", "De pie 6.1 · baja", "más agachado (50): más estable, los codos hacen más fuerza", 50, 50, 0),
    ("6.2", "De pie 6.2 · alta", "patas más rectas (30): los codos hacen menos fuerza", 30, 30, 0),
    ("6.3", "De pie 6.3 · inclinada adelante", "delanteras 48, traseras 32 (morro abajo)", 48, 32, 0),
    ("6.4", "De pie 6.4 · inclinada atrás", "delanteras 32, traseras 48 (morro arriba)", 32, 48, 0),
    ("6.5", "De pie 6.5 · pectorales +10", "apoyo más ancho o más estrecho (la 6.6 hace lo contrario)", 40, 40, 10),
    ("6.6", "De pie 6.6 · pectorales −10", "lo contrario que la 6.5", 40, 40, -10),
    # 12-10-2026, el usuario: "la 2, con un poco de la 5" -> la elegida para andar
    ("6.7", "De pie 6.7 · alta + pectorales +5", "la 6.2 con un poco de la 6.5 (agachado 30, pectorales +5)", 30, 30, 5),
]
FAVORITAS_6 = ["6.7", "6", "6.1", "6.2", "6.5"]  # las 5 mejores según el usuario (12-10): botones arriba en la web
TUCK = 52  # agachado de "recogido": codos lejos de los topes 30/150 (con los topes hubo apagones)
ALTA = 22  # agachado de las marchas "altas": patas más rectas, el codo hace menos fuerza (el usuario: "desde arriba va mejor")

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


def base_stance(conv, legs=None):
    """La postura de pie de la variante elegida (agachado de delanteras y traseras, pectorales)."""
    f, r, p = conv.get("delanteras", 40), conv.get("traseras", 40), conv.get("pectoral", 0)
    return [o for leg in (legs or LEGS) for o in stance(conv, f if leg in FRONT else r, [leg], p)]


def S(objs, juntos=False):
    return {"op": "set", "objetivos": objs} | ({"juntos": True} if juntos else {})


def no_pec(objs):
    return [o for o in objs if o["art"] != "pectoral"]


def stand_program(conv):
    """Levantarse sin que una pata cargue sola con su esquina: 1) pectorales (casi sin carga), 2) recoger las
    4 patas a la vez (pezuñas bajo los hombros, el cuerpo baja parejo), 3) estirarlas las 4 a la vez, 1° cada
    vez y muy despacio, hasta la postura. Con el límite de 5 A, antes iba servo a servo y no podía."""
    target = base_stance(conv)
    tuck = min(55, max(conv.get("delanteras", 40), conv.get("traseras", 40)) + 12)
    return [{"op": "velocidad", "v": "normal"}, S([o for o in target if o["art"] == "pectoral"]),
            {"op": "velocidad", "v": "lento"}, S(no_pec(stance(conv, tuck)), juntos=True), {"op": "esperar", "s": 1},
            {"op": "velocidad", "v": "suave"}, S(no_pec(target), juntos=True), {"op": "esperar", "s": 1.5}]


def juntos_multi(prog):
    """Los pasos que mueven varias patas cargan con el cuerpo: van juntos. Los de una pata (levantar un pie), en tandas."""
    for s in prog:
        if s.get("op") == "repetir":
            juntos_multi(s["pasos"])
        elif s.get("op") == "set" and len({o["pata"] for o in s["objetivos"]}) > 1:
            s["juntos"] = True
    return prog


def tope_warnings(prog):
    """Articulaciones a las que la calibración lleva más allá del recorrido del servo: se quedan en el tope."""
    chans, out = {leg: leg_channels(leg) for leg in LEGS}, set()

    def scan(steps):
        for s in steps:
            scan(s.get("pasos", []))
            for o in s.get("objetivos", []) if s.get("op") == "set" else []:
                ch = chans[o["pata"]].get(o["art"])
                if ch is not None:
                    off, inv = CAL.get(ch, (0, False))
                    a = clamp(o["angulo"])
                    if not PHYS_MIN <= (180 - a if inv else a) + off <= PHYS_MAX:
                        out.add(f"{o['pata']}{JOINTS.index(o['art']) + 1}")
    scan(prog)
    return sorted(out)


def _gait(conv):
    """Lo que comparten las marchas: agachado de cada pata en la de pie elegida y atajos para los objetivos."""
    k0 = {L: conv.get("delanteras" if L in FRONT else "traseras", 40) for L in LEGS}  # codo
    h0 = {L: k0[L] / 2 for L in LEGS}                                                # hombro (pie bajo la cadera)
    hom = lambda L, d: {"pata": L, "art": "hombro", "angulo": dev_angle(L, "hombro", d, conv)}  # noqa: E731
    cod = lambda L, d: {"pata": L, "art": "codo", "angulo": dev_angle(L, "codo", d, conv)}      # noqa: E731
    return k0, h0, hom, cod


def walk_program(conv, step_l, step_r, cycles=3, lift=16, sway=0, fast=False):
    """Paso a paso (creep): una pata cada vez y las otras 3 siempre apoyadas, en el orden D A C B (al revés hacia
    atrás). Cada pata sube (hombro y codo a la vez: el pie sale vertical), avanza en el aire y baja; luego el cuerpo
    avanza con las 4 juntas. Con `sway`, antes de levantar una pata el cuerpo se aparta hacia el otro lado con los
    pectorales (supone que pectoral + abre la pata: si se cae hacia la pata levantada, es al revés). Con `fast`, el pie
    sube y avanza en un solo movimiento (en diagonal) y todo va a velocidad rápida: menos pasos y más ritmo."""
    k0, h0, hom, cod = _gait(conv)
    p0 = conv.get("pectoral", 0)
    step = {L: step_l if L in ("A", "D") else step_r for L in LEGS}
    order = ["D", "A", "C", "B"] if step_l + step_r >= 0 else ["B", "C", "A", "D"]
    hd = {L: h0[L] - step[L] / 2 + i * step[L] / 4 for i, L in enumerate(order)}  # escalonadas: le toca a la de más atrás

    def pecs(side):  # side +1: pies hacia la izquierda, cuerpo a la derecha (para levantar una izquierda)
        return [{"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", p0 + (side if L in ("A", "D") else -side) * sway, conv)}
                for L in LEGS]
    prog = [{"op": "velocidad", "v": "rapido" if fast else "normal"}, S(base_stance(conv), juntos=True),
            {"op": "esperar", "s": 0.5}, S([hom(L, hd[L]) for L in LEGS], juntos=True)]
    cycle = []
    for L in order:
        if sway:
            cycle.append(S(pecs(1 if L in ("A", "D") else -1), juntos=True))
        dest = h0[L] + step[L] / 2
        if fast:
            cycle.append(S([hom(L, dest + lift / 2), cod(L, k0[L] + lift)], juntos=True))  # sube y avanza a la vez
        else:
            cycle += [S([hom(L, hd[L] + lift / 2), cod(L, k0[L] + lift)], juntos=True),   # sube
                      S([hom(L, dest + lift / 2)], juntos=True)]                           # avanza en el aire
        cycle.append(S([hom(L, dest), cod(L, k0[L])], juntos=True))                         # baja
        hd[L] = dest
        for x in LEGS:
            hd[x] -= step[x] / 4
        cycle.append(S([hom(x, hd[x]) for x in LEGS], juntos=True))                    # el cuerpo avanza
    prog.append({"op": "repetir", "n": cycles, "pasos": cycle})
    prog.append(S(base_stance(conv), juntos=True))
    return prog


def trot_program(conv, step=12, cycles=4, lift=16, fast=False):
    """Trote: las diagonales A+C y B+D se turnan. Una sube, avanza en el aire mientras la otra empuja el cuerpo
    hacia delante, y baja. Solo 2 patas apoyadas: más rápido pero menos estable que el paso a paso. Con `fast`,
    subir, avanzar y empujar van en un solo movimiento y a velocidad rápida (el trote aguanta mejor con ritmo)."""
    k0, h0, hom, cod = _gait(conv)
    cycle = []
    for up, down in ((["A", "C"], ["B", "D"]), (["B", "D"], ["A", "C"])):
        swing = [hom(L, h0[L] + step / 2 + lift / 2) for L in up] + [hom(L, h0[L] - step / 2) for L in down]
        if fast:
            cycle.append(S(swing + [cod(L, k0[L] + lift) for L in up], juntos=True))
        else:
            cycle += [S([o for L in up for o in (hom(L, h0[L] - step / 2 + lift / 2), cod(L, k0[L] + lift))], juntos=True),
                      S(swing, juntos=True)]
        cycle.append(S([o for L in up for o in (hom(L, h0[L] + step / 2), cod(L, k0[L]))], juntos=True))
    return [{"op": "velocidad", "v": "rapido" if fast else "normal"}, S(base_stance(conv), juntos=True),
            {"op": "esperar", "s": 0.5}, {"op": "repetir", "n": cycles, "pasos": cycle}, S(base_stance(conv), juntos=True)]


def phase_gait(conv, fases, duty=0.6, step=12, lift=16, ticks=12, cycles=3, v="normal", press=0,
               swing="arco", stance="seno", hip=None):
    """Marcha por fases (como los SpotMicro de GitHub y los aires del caballo): cada pata tiene su fase en el ciclo
    (0-1). Mientras apoya (fracción `duty`) el pie va de delante a atrás empujando el cuerpo; en el aire vuelve
    adelante trazando un arco (seno) de altura `lift`. Cada tick = un paso `set` con las 12 a la vez. `press`: pisada
    firme, el pie apoyado empuja un poco hacia el suelo (la "penetration depth" de spot_mini_mini) para no resbalar.
    `swing="limpio"`: el pie sube antes de avanzar y baja después (no arrastra al despegar ni al posarse).
    `stance="empuje"`: la pisada aprieta más al final del apoyo, justo antes de despegar (impulso).
    `hip` {pata: fracción}: cuánto de la subida hace el hombro (0,5 normal; más = la pata sube más con el hombro,
    p. ej. B, cuyo codo con el ajuste +50 llega a su tope)."""
    k0, h0, hom, cod = _gait(conv)
    cycle = []
    for t in range(ticks):
        objs = []
        for L in LEGS:
            p = (t / ticks - fases[L]) % 1.0
            sl = step[L] if isinstance(step, dict) else step  # paso distinto por pata = girar
            if p < duty:                                   # apoyada: empuja de delante a atrás
                q = p / duty
                x = sl / 2 - sl * q
                z = -press * (1.4 * q * q if stance == "empuje" else math.sin(math.pi * q))
            else:                                          # en el aire: vuelve adelante en arco
                s = (p - duty) / (1 - duty)
                if swing == "limpio":                      # avanza sobre todo a media altura; sube rápido
                    x, z = -sl / 2 + sl * s * s * (3 - 2 * s), lift * math.sin(math.pi * s) ** 0.6
                else:
                    x, z = -sl / 2 + sl * s, lift * math.sin(math.pi * s)
            objs += [hom(L, h0[L] + x + z * (hip or {}).get(L, 0.5)), cod(L, k0[L] + z)]
        cycle.append(S(objs, juntos=True))
    return [{"op": "velocidad", "v": v}, S(base_stance(conv), juntos=True), {"op": "esperar", "s": 0.5},
            {"op": "repetir", "n": cycles, "pasos": cycle}, S(base_stance(conv), juntos=True)]


# 10 marchas "creativas": aires del caballo y de otros animales (fase de cada pata A B C D, apoyo, paso, altura...)
CREATIVAS = [
    ("paso-caballo", "Paso de caballo", "4 tiempos, secuencia lateral D→A→C→B, siempre 3 apoyadas pero sin pausas",
     dict(fases={"D": 0, "A": 0.25, "C": 0.5, "B": 0.75}, duty=0.75, ticks=16)),
    ("tolt", "Tölt (caballo islandés)", "4 tiempos rápidos y suaves, pie bajo: corre sin dar botes",
     dict(fases={"D": 0, "A": 0.25, "C": 0.5, "B": 0.75}, duty=0.7, step=10, lift=10, v="rapido", cycles=4)),
    ("ambladura", "Ambladura (camello)", "Laterales a la vez: A+D y luego B+C (se balancea de lado)",
     dict(fases={"A": 0, "D": 0, "B": 0.5, "C": 0.5}, duty=0.55)),
    ("trote-eliptico", "Trote elíptico (SpotMicroAI)", "Diagonales con el pie en arco continuo, como los SpotMicro de GitHub",
     dict(fases={"A": 0, "C": 0, "B": 0.5, "D": 0.5}, duty=0.55, lift=18, v="rapido", cycles=5)),
    ("piaffe", "Piaffe (doma)", "Trote en el sitio levantando mucho las patas, despacio y solemne",
     dict(fases={"A": 0, "C": 0, "B": 0.5, "D": 0.5}, duty=0.5, step=0, lift=22, v="lento", cycles=4)),
    ("passage", "Passage (doma)", "Trote elevado que avanza poco a poco, como flotando",
     dict(fases={"A": 0, "C": 0, "B": 0.5, "D": 0.5}, duty=0.5, step=8, lift=22, v="lento", cycles=4)),
    ("medio-galope", "Medio galope", "3 tiempos: D, luego A+C, luego B (mano derecha delante)",
     dict(fases={"D": 0, "A": 0.33, "C": 0.33, "B": 0.66}, duty=0.6, v="rapido", cycles=4)),
    ("galope", "Galope", "4 tiempos rápidos: D, C, A, B, con un momento casi en el aire. Dinámico: puede tambalear",
     dict(fases={"D": 0, "C": 0.1, "A": 0.5, "B": 0.6}, duty=0.45, step=14, v="rapido", cycles=5)),
    ("brinco", "Brinco (conejo)", "Delanteras juntas y traseras juntas, como un conejo o un perro corriendo",
     dict(fases={"A": 0, "B": 0, "C": 0.5, "D": 0.5}, duty=0.5, step=10, v="rapido", cycles=4)),
    ("bote-gacela", "Bote de gacela (pronk)", "Las 4 a la vez: se agacha y se estira con ritmo, avanzando un poco. Gasta mucha corriente",
     dict(fases={L: 0 for L in LEGS}, duty=0.5, step=6, lift=10, v="rapido", cycles=4)),
]


GALOPE = {"D": 0, "C": 0.1, "A": 0.5, "B": 0.6}      # la que más avanzó (3 cm, 12-10)
CAMELLO = {"A": 0, "D": 0, "B": 0.5, "C": 0.5}       # fue 1 cm hacia atrás


def nuevas_routines(c):
    """15 rutinas verdes (12-10, tras probar): variantes del galope y del camello, y cosas curiosas. Las de 3 patas
    apartan antes el cuerpo de la pata que levantan (dar la pata se caía hacia la esquina B)."""
    k0, h0, hom, cod = _gait(c)
    p0 = c.get("pectoral", 0)
    pec = lambda L, s: {"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", p0 + (s if L in ("A", "D") else -s), c)}  # noqa: E731
    base = S(base_stance(c), juntos=True)
    alta = c | {"delanteras": ALTA, "traseras": ALTA}

    def shift(back, left, extra=None):
        """Aparta el cuerpo: `back` > 0 lo lleva atrás (pies adelante), `left` > 0 a la izquierda (supone pectoral +
        abre). `extra` {pata: (hombro, codo)} añade a cada pata (p. ej. estirar una atrás del todo)."""
        out = []
        for L in LEGS:
            dh, dk = (extra or {}).get(L, (0, 0))
            out += [hom(L, h0[L] + back + dh), cod(L, k0[L] + dk), pec(L, -left)]
        return S(out, juntos=True)

    def give_paw(leg, front_mate, diag):
        back, left = 8, (8 if leg in RIGHT else -8)
        sup = shift(back, left, {front_mate: (10, 0), diag: (-18, -12)})  # la otra delantera adelante, la diagonal estirada atrás
        return [{"op": "velocidad", "v": "lento"}, base, sup, {"op": "esperar", "s": 0.6},
                S([hom(leg, 50), cod(leg, 15)], juntos=True),
                {"op": "repetir", "n": 3, "pasos": [S([pec(leg, 12 if leg in ("A", "D") else -12)]),
                                                    S([pec(leg, -12 if leg in ("A", "D") else 12)])]},
                S([hom(leg, h0[leg] + back), cod(leg, k0[leg])], juntos=True), {"op": "esperar", "s": 0.3}, base]

    pis = [{"op": "velocidad", "v": "lento"}, base, shift(-6, 8, {"C": (0, 6)}), {"op": "esperar", "s": 0.6},
           S([pec("D", 30), cod("D", k0["D"] + 22), hom("D", h0["D"] - 6)], juntos=True), {"op": "esperar", "s": 2},
           S([pec("D", -8), cod("D", k0["D"]), hom("D", h0["D"] - 6)], juntos=True), base]
    estirarse = [{"op": "velocidad", "v": "lento"}, base,
                 S(no_pec(stance(c, 50, ["A", "B"])) + no_pec(stance(c, 18, ["C", "D"])) + [hom(L, 25 + 15) for L in ("A", "B")], juntos=True),
                 {"op": "esperar", "s": 2}, base,
                 S(no_pec(stance(c, 18, ["A", "B"])) + [o for L in ("C", "D") for o in (hom(L, h0[L] - 18), cod(L, k0[L] - 8))], juntos=True),
                 {"op": "esperar", "s": 2}, base]
    menear = [{"op": "velocidad", "v": "rapido"}, base, {"op": "repetir", "n": 6, "pasos": [
        S([pec(L, 12) for L in ("C", "D")], juntos=True), S([pec(L, -12) for L in ("C", "D")], juntos=True)]}, base]
    baile = [{"op": "velocidad", "v": "normal"}, base, {"op": "repetir", "n": 4, "pasos": [
        S([pec(L, 10) for L in LEGS] + no_pec(stance(c, k0["A"] + 10)), juntos=True),
        S([pec(L, -10) for L in LEGS] + no_pec(stance(c, max(10, k0["A"] - 6))), juntos=True)]},
        {"op": "velocidad", "v": "rapido"}, {"op": "repetir", "n": 3, "pasos": [
            S([pec(L, 12) for L in ("C", "D")], juntos=True), S([pec(L, -12) for L in ("C", "D")], juntos=True)]}, base]
    def tiro(step, cycles=4, lift=16):
        """Caballo de tiro (idea del usuario): las que quedan de pie empujan y proyectan el resto. 1) las delanteras
        se estiran adelante en el aire, 2) apoyan, 3) las 4 empujan a la vez (el cuerpo avanza: 'tiro'), 4) las traseras
        vuelven adelante de una en una, siempre con 3 apoyadas. Con `step` negativo, hacia atrás."""
        cyc = []
        for L in ("A", "B"):  # delanteras de una en una: alcanzan
            cyc += [S([hom(L, h0[L] + lift / 2), cod(L, k0[L] + lift)], juntos=True),
                    S([hom(L, h0[L] + step + lift / 2)], juntos=True),
                    S([hom(L, h0[L] + step), cod(L, k0[L])], juntos=True)]
        cyc.append(S([hom(L, h0[L] + (0 if L in FRONT else -step)) for L in LEGS], juntos=True))  # tiro: todo empuja
        for L in ("D", "C"):  # traseras recuperan
            cyc += [S([hom(L, h0[L] - step + lift / 2), cod(L, k0[L] + lift)], juntos=True),
                    S([hom(L, h0[L] + lift / 2)], juntos=True),
                    S([hom(L, h0[L]), cod(L, k0[L])], juntos=True)]
        return [{"op": "velocidad", "v": "normal"}, base, {"op": "repetir", "n": cycles, "pasos": cyc}, base]

    def lateral(w, cycles=5, lift=14):
        """De lado con balanceo (el usuario: 'balancearse a los lados lo mueve un poco a la derecha'): trote lateral.
        Una diagonal sube y recoloca sus pies al otro lado mientras la otra empuja de lado con los pectorales.
        w > 0: a la derecha si pectoral + abre (sin confirmar; si va al revés, es la otra)."""
        cyc = []
        for up, down in ((["A", "C"], ["B", "D"]), (["B", "D"], ["A", "C"])):
            cyc += [S([o for L in up for o in (hom(L, h0[L] + lift / 2), cod(L, k0[L] + lift), pec(L, -w))]
                      + [pec(L, w) for L in down], juntos=True),
                    S([o for L in up for o in (hom(L, h0[L]), cod(L, k0[L]))], juntos=True)]
        return [{"op": "velocidad", "v": "rapido"}, base, {"op": "repetir", "n": cycles, "pasos": cyc}, base]

    potente = [{"op": "velocidad", "v": "rapido"}, base, {"op": "repetir", "n": 5, "pasos": [
        S([pec(L, 20) for L in LEGS], juntos=True), S([pec(L, -20) for L in LEGS], juntos=True)]}, base]

    g = lambda **kw: phase_gait(c, **({"fases": GALOPE, "duty": 0.45, "step": 14, "v": "rapido", "cycles": 5} | kw))  # noqa: E731
    return [
        ("galope-largo", "Galope largo", "El galope con zancada de 18 y pie más alto", g(step=18, lift=18)),
        ("galope-rapido", "Galope rápido", "Más aire (apoyo 40%) y menos pasos por ciclo: más ritmo", g(duty=0.4, ticks=10, cycles=6)),
        ("galope-alto", "Galope alto", f"El galope con las patas más rectas ({ALTA}): menos fuerza en los codos",
         phase_gait(alta, fases=GALOPE, duty=0.45, step=14, v="rapido", cycles=5)),
        ("galope-turbo", "Galope turbo", f"El galope alto (el favorito) lo más rápido posible: más aire y menos pasos por ciclo",
         phase_gait(alta, fases=GALOPE, duty=0.4, step=14, ticks=8, v="rapido", cycles=7)),
        ("galope-otra-mano", "Galope a la otra mano", "Empieza por la trasera derecha: C, D, B, A",
         g(fases={"C": 0, "D": 0.1, "B": 0.5, "A": 0.6})),
        ("galope-izq", "Galope girando a la izquierda", "Zancada corta a la izquierda y larga a la derecha",
         g(step={"A": 6, "D": 6, "B": 18, "C": 18})),
        ("galope-der", "Galope girando a la derecha", "Zancada larga a la izquierda y corta a la derecha",
         g(step={"A": 18, "D": 18, "B": 6, "C": 6})),
        ("camello-adelante", "Camello al revés", "El camello iba hacia atrás: aquí con el paso invertido, a ver si va adelante",
         phase_gait(c, fases=CAMELLO, duty=0.55, step=-12, cycles=4)),
        ("camello-rapido", "Camello rápido", "Laterales a la vez con más aire y más ritmo (paso invertido)",
         phase_gait(c, fases=CAMELLO, duty=0.45, step=-14, v="rapido", cycles=5)),
        ("canguro", "Saltos de canguro", "Traseras juntas empujan fuerte, luego delanteras: brinco con más aire",
         phase_gait(c, fases={"C": 0, "D": 0, "A": 0.45, "B": 0.45}, duty=0.4, step=14, lift=14, v="rapido", cycles=5)),
        ("caballo-tiro", "Caballo de tiro", "Las delanteras alcanzan adelante, las 4 empujan a la vez y las traseras recuperan: "
         "las que están de pie proyectan el cuerpo", tiro(16)),
        ("caballo-tiro-atras", "Caballo de tiro hacia atrás", "Lo mismo al revés (por si va mejor hacia atrás)", tiro(-16)),
        ("balanceo-potente", "Balanceo potente", "El balanceo a los lados con el doble de recorrido (20) y rápido, 5 veces", potente),
        ("lado-derecha", "De lado a la derecha", "Diagonales que se recolocan mientras las otras empujan de lado (si va a la izquierda, dímelo)",
         lateral(10)),
        ("lado-izquierda", "De lado a la izquierda", "Lo mismo hacia el otro lado", lateral(-10)),
        ("pata-b", "Dar la pata B (estable)", "Aparta el cuerpo atrás-izquierda, A adelante y D estirada atrás; luego da la B",
         give_paw("B", "A", "D")),
        ("pata-a", "Dar la pata A (estable)", "Lo mismo en espejo: cuerpo atrás-derecha, B adelante, C estirada atrás",
         give_paw("A", "B", "C")),
        ("pis", "Hacer pis", "Aparta el cuerpo y levanta la trasera izquierda (D) hacia el lado, como un perro", pis),
        ("desperezarse", "Desperezarse", "Delanteras estiradas y bajas con el culo en alto, y luego al revés", estirarse),
        ("menear", "Menear el culete", "Las traseras de lado a lado, rápido", menear),
        ("baile", "Baile", "De lado a lado subiendo y bajando, y un meneo final", baile),
    ]


CURIOSAS = {"balanceo-potente", "baile", "menear", "pis", "desperezarse"}  # azules, sin estrellas (12-10)
# quitadas de la lista el 12-10 (el usuario: "las 20 % menos bonitas" y "quita las viejas" de dar la pata): las de 1-2
# estrellas, las dar la pata de pie (se caían hacia delante) y variantes viejas de andar que no avanzaban. El código sigue.
RETIRADAS = {"galope-turbo", "galope-otra-mano", "caballo-tiro", "galope-izq", "pie-ayuda-d",
             "pata-b", "pata-a", "saludar-b",
             "andar-alto-rapido", "andar-pasitos", "andar-largo", "trote-alto", "bote-gacela"}


def plata_routines(c):
    """Las 6 ganadoras (plata, 12-10), sacadas de las estrellas del usuario: galope a la derecha 5, galope alto 4,
    galope largo/rápido, camello, canguro (gira mucho a la derecha) y caballo de tiro atrás 3; turbo, otra mano y
    caballo de tiro 1. Se cruzan las mejores (altura alta + galope) y se añade la pisada firme de spot_mini_mini.
    Dar la pata se caía hacia delante (el peso va delante): aquí se sienta antes, como un perro."""
    k0, h0, hom, cod = _gait(c)
    p0 = c.get("pectoral", 0)
    pec = lambda L, s: {"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", p0 + (s if L in ("A", "D") else -s), c)}  # noqa: E731
    alta = c | {"delanteras": ALTA, "traseras": ALTA}
    g = lambda **kw: phase_gait(alta, **({"fases": GALOPE, "duty": 0.45, "step": 14, "press": 3, "v": "rapido", "cycles": 5} | kw))  # noqa: E731
    def pata_sentado(leg, culo=52, del_=18, alto=50, codo=15, atras=0, lado=6):
        """Dar la pata sentado (el éxito del 12-10). `culo`: agachado de las traseras (más = culo más abajo y el peso
        más atrás); `del_`: el de las delanteras; `alto`/`codo`: hombro y codo de la pata que da (más hombro = más arriba
        y adelante, menos codo = más estirada); `atras`: pies delanteros adelante (cuerpo atrás); `lado`: cuerpo hacia el
        lado contrario de la pata."""
        other = "A" if leg == "B" else "B"
        side = -lado if leg in RIGHT else lado                      # el cuerpo, lejos de la pata que da
        wave = 12 if leg in ("A", "D") else -12
        sit = no_pec(stance(c, culo, ["C", "D"])) + no_pec(stance(c, del_, ["A", "B"]))
        if atras:
            sit = [o for o in sit if not (o["pata"] in FRONT and o["art"] == "hombro")] + \
                  [hom(L, del_ / 2 + atras) for L in FRONT]
        return [{"op": "velocidad", "v": "lento"}, S(base_stance(c), juntos=True), S(sit, juntos=True),
                {"op": "esperar", "s": 0.8}, S([pec(L, side) for L in LEGS], juntos=True),
                S([hom(other, del_ / 2 + atras + 4)], juntos=True),  # la otra delantera, un poco adelante: apoyo
                S([hom(leg, min(60, alto)), cod(leg, codo)], juntos=True),
                {"op": "repetir", "n": 3, "pasos": [S([pec(leg, side + wave)]), S([pec(leg, side - wave)])]},
                S([o for o in sit if o["pata"] == leg], juntos=True), S([pec(L, 0) for L in LEGS], juntos=True),
                {"op": "esperar", "s": 0.5}, S(base_stance(c), juntos=True)]

    PATAS = [  # (versión, descripción, parámetros): la 1 es la que funcionó; las demás, más arriba y adelante
        ("1", "la original (la que funcionó)", {}),
        ("2", "pata más arriba y adelante (hombro 58, casi estirada)", {"alto": 58, "codo": 8}),
        ("3", "culo más abajo (58) y pata arriba", {"culo": 58, "del_": 14, "alto": 58, "codo": 8}),
        ("4", "culo abajo, cuerpo atrás y la pata lo más arriba posible", {"culo": 58, "del_": 14, "alto": 60, "codo": 2,
                                                                          "atras": 8, "lado": 9}),
    ]
    patas = []
    for leg in ("B", "A"):
        for v, desc, kw in PATAS:
            rid = "plata-pata-sentado" if (leg, v) == ("B", "1") else f"pata-{leg.lower()}-sentado-{v}"  # conserva su estrella
            patas.append((rid, f"Dar la pata {leg} sentado · {v}", f"Sentado: {desc}", pata_sentado(leg, **kw), "pata"))

    # 6 agresivas del galope plata a la derecha (la mejor, 12-10): "que ande, fuerte"
    F = [  # (id, nombre, desc, parámetros de phase_gait)
        ("zancada", "zancada", "zancadas más largas (izq 22, der 10) y pie más alto", {"step": {"A": 22, "D": 22, "B": 10, "C": 10}, "lift": 18, "press": 4}),
        ("aire", "más aire", "más tiempo en el aire (apoyo 38 %) y pie a 20", {"duty": 0.38, "step": {"A": 20, "D": 20, "B": 8, "C": 8}, "lift": 20}),
        ("ritmo", "ritmo", "más pasos por segundo (9 por ciclo) y pisada fuerte", {"ticks": 9, "press": 5, "cycles": 7}),
        ("recto", "más recto", "menos giro (izq 20, der 14): para que avance más que gire", {"step": {"A": 20, "D": 20, "B": 14, "C": 14}, "lift": 18, "press": 4}),
        ("pisoton", "pisotón", "pisada muy firme (7): empuja contra el suelo para no resbalar", {"step": {"A": 20, "D": 20, "B": 10, "C": 10}, "press": 7}),
        ("todo", "a tope", "todo junto: zancada 24/12, pie a 22, mucho aire y ritmo. Gasta mucha corriente",
         {"step": {"A": 24, "D": 24, "B": 12, "C": 12}, "lift": 22, "duty": 0.38, "press": 6, "ticks": 10, "cycles": 6}),
    ]
    fuertes = [(f"fuerte-{k}", f"Galope fuerte · {n}", f"Galope plata a la derecha, {d}",
                g(**({"step": {"A": 18, "D": 18, "B": 6, "C": 6}} | kw)), "fuerte") for k, n, d, kw in F]
    return fuertes + patas + [(rid, n, d, p, "ganadora") for rid, n, d, p in [
        ("plata-galope", "Galope plata", "Galope alto (4★) con pisada firme y las derechas un poco más largas para no torcerse",
         g(step={"A": 13, "D": 13, "B": 15, "C": 15})),
        ("plata-galope-der", "Galope plata a la derecha", "Cruce de las dos mejores: galope a la derecha (5★) a la altura del galope alto (4★)",
         g(step={"A": 18, "D": 18, "B": 6, "C": 6})),
        ("plata-galope-suave", "Galope plata suave", "Galope alto más apoyado (50 %), pie más bajo y a velocidad normal: menos tirones de corriente",
         g(duty=0.5, lift=14, press=4, v="normal")),
        ("plata-canguro", "Canguro plata", "Saltos de canguro (iba bien pero muy girado a la derecha), altos y con las derechas más largas",
         g(fases={"C": 0, "D": 0, "A": 0.45, "B": 0.45}, duty=0.4, step={"A": 10, "D": 10, "B": 16, "C": 16}, lift=14)),
        ("plata-camello", "Camello plata", "El camello al revés (3★) a la altura alta y con pisada firme",
         phase_gait(alta, fases=CAMELLO, duty=0.55, step=-12, press=3, cycles=4)),
    ]]


def oro_routines(c):
    """6 de oro (12-10): mejoras de la mejor, "Galope fuerte · ritmo" (5★): galope a la derecha alto, zancada izq 18 /
    der 6, pisada firme 5 y ritmo rápido (9 puntos por ciclo). Aprendido con las estrellas: la zancada 18/6 va mejor que
    más larga (22/10: 2★) o más recta (20/14: 3★); pisar fuerte ayuda (ritmo 5: 5★, pisotón 7: 4★); más tiempo en el
    aire no (más aire: 2★, turbo: 1★). Cada una cambia UNA cosa para saber qué ayuda; la 6 junta las que prometen."""
    alta = c | {"delanteras": ALTA, "traseras": ALTA}
    best = {"fases": GALOPE, "duty": 0.45, "step": {"A": 18, "D": 18, "B": 6, "C": 6}, "lift": 16, "press": 5,
            "ticks": 9, "v": "rapido", "cycles": 7}
    g = lambda **kw: phase_gait(alta, **(best | kw))  # noqa: E731
    return [
        ("oro-limpio", "Oro · despegue limpio", "El pie sube antes de avanzar y baja después: no arrastra al despegar ni al posarse",
         g(swing="limpio")),
        ("oro-empuje", "Oro · empuje final", "La pisada aprieta al final del apoyo, justo antes de despegar: impulso hacia delante",
         g(stance="empuje")),
        ("oro-b-hombro", "Oro · B sube con el hombro", "La B (codo en su tope por el ajuste +50) levanta con el hombro: no arrastra",
         g(hip={"B": 1.0})),
        ("oro-pisoton", "Oro · ritmo y pisotón", "Las dos que mejor fueron juntas: el ritmo (5★) con la pisada del pisotón (7)",
         g(press=7)),
        ("oro-aire", "Oro · aire con ritmo", "Por si la buena era 'más aire': su pie a 20 y apoyo 40 %, con la zancada y el ritmo de la mejor",
         g(lift=20, duty=0.4)),
        ("oro-todo", "Oro · todo lo bueno", "Despegue limpio + empuje final + B con el hombro + pisada 6",
         g(swing="limpio", stance="empuje", hip={"B": 1.0}, press=6)),
    ]


def amarillas_routines(c):
    """3 'de pie' suaves (12-10: muy poca potencia y la pata D siempre se queda abajo y hay que darle un toquecito).
    Todas acaban con el toquecito a D: la estira de golpe 8° más y la devuelve."""
    k0, h0, hom, cod = _gait(c)
    p0 = c.get("pectoral", 0)
    pec = lambda L, s: {"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", p0 + (s if L in ("A", "D") else -s), c)}  # noqa: E731
    f, r = c.get("delanteras", 40), c.get("traseras", 40)
    tuck = min(55, max(f, r) + 12)
    at = lambda fb, rb, legs=None: no_pec(base_stance(c | {"delanteras": fb, "traseras": rb}, legs))  # noqa: E731
    toque_d = [{"op": "velocidad", "v": "rapido"}, S([cod("D", k0["D"] - 8), hom("D", h0["D"] - 4)], juntos=True),
               {"op": "esperar", "s": 0.3}, {"op": "velocidad", "v": "lento"}, S(base_stance(c, ["D"]), juntos=True)]
    pecs = S([o for o in base_stance(c) if o["art"] == "pectoral"])
    recoger = [{"op": "velocidad", "v": "lento"}, pecs, S(at(tuck, tuck), juntos=True), {"op": "esperar", "s": 1}]

    escalera = []  # subir a escalones de ~3° con descanso: los servos recuperan entre escalón y escalón
    n = max(1, round((tuck - min(f, r)) / 3))
    for i in range(1, n + 1):
        escalera += [S(at(tuck - (tuck - f) * i / n, tuck - (tuck - r) * i / n), juntos=True), {"op": "esperar", "s": 0.4}]

    # ayudando a D: el cuerpo se aparta de D (pectorales a la derecha) y D va 6° más estirada que las demás al subir
    ayuda = [S([pec(L, 6) for L in LEGS], juntos=True)]
    for i in range(1, 6):
        fb, rb = tuck - (tuck - f) * i / 5, tuck - (tuck - r) * i / 5
        ayuda += [S(at(fb, rb, ["A", "B", "C"]) + at(f, max(r, rb - 6), ["D"]), juntos=True), {"op": "esperar", "s": 0.3}]
    ayuda += [S(base_stance(c), juntos=True)]

    # "de Iván" (idea del usuario): delanteras un poco, traseras un poco, alternando hasta arriba: solo 4 servos cada vez
    ivan, n = [], max(1, round((tuck - min(f, r)) / 4))
    for i in range(1, n + 1):
        ivan += [S(at(tuck - (tuck - f) * i / n, r, ["A", "B"]), juntos=True), {"op": "esperar", "s": 0.3},
                 S(at(f, tuck - (tuck - r) * i / n, ["C", "D"]), juntos=True), {"op": "esperar", "s": 0.3}]

    dos_tiempos = [S(at(tuck, r, ["C", "D"]) + at(tuck, tuck, ["A", "B"]), juntos=True), {"op": "esperar", "s": 0.8},
                   S(at(f, r), juntos=True)]
    return [
        ("pie-suave", "De pie suave (escalones)", "Recoge y sube a escalones de 3° descansando entre ellos; al final, toquecito a D",
         recoger + [{"op": "velocidad", "v": "suave"}] + escalera + toque_d),
        ("pie-ayuda-d", "De pie ayudando a la D", "Aparta el cuerpo de D (adelante-derecha) y D sube por delante de las demás; "
         "luego toquecito a D", recoger + [{"op": "velocidad", "v": "suave"}] + ayuda + toque_d),
        ("pie-dos-tiempos", "De pie en dos tiempos", "Primero las traseras (morro abajo), descansa, luego las delanteras; "
         "toquecito a D", recoger + [{"op": "velocidad", "v": "suave"}] + dos_tiempos + toque_d),
        ("pie-ivan", "De pie de Iván", "Delanteras un poco, traseras un poco, alternando hasta arriba: nunca suben las 4 a la vez; "
         "toquecito a D", recoger + [{"op": "velocidad", "v": "suave"}] + ivan + toque_d),
    ]


def crab_program(conv, step=8, cycles=3, lift=16):
    """De lado (cangrejo): paso a paso, pero cada pie se mueve de lado con el pectoral y luego el cuerpo se desplaza
    con los 4 pectorales. Hacia la izquierda si pectoral + abre la pata (sin confirmar; si no, va a la derecha)."""
    k0, h0, hom, cod = _gait(conv)
    p0 = conv.get("pectoral", 0)
    order = ["D", "A", "C", "B"]
    lat = {L: -step / 2 + i * step / 4 for i, L in enumerate(order)}  # desplazamiento de cada pie hacia la izquierda
    pec = lambda L, s: {"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", p0 + (s if L in ("A", "D") else -s), conv)}  # noqa: E731
    prog = [{"op": "velocidad", "v": "normal"}, S(base_stance(conv), juntos=True), {"op": "esperar", "s": 0.5},
            S([pec(L, lat[L]) for L in LEGS], juntos=True)]
    cycle = []
    for L in order:
        cycle += [S([hom(L, h0[L] + lift / 2), cod(L, k0[L] + lift)], juntos=True),    # sube
                  S([pec(L, step / 2)], juntos=True),                                  # se mueve de lado en el aire
                  S([hom(L, h0[L]), cod(L, k0[L])], juntos=True)]                      # baja
        lat[L] = step / 2
        for x in LEGS:
            lat[x] -= step / 4
        cycle.append(S([pec(x, lat[x]) for x in LEGS], juntos=True))                   # el cuerpo se desplaza
    prog.append({"op": "repetir", "n": cycles, "pasos": cycle})
    prog.append(S(base_stance(conv), juntos=True))
    return prog


def march_program(conv, n=4, lift=16):
    """Marcar el paso: levanta en diagonal A+C y luego B+D, sin avanzar."""
    k0, h0, hom, cod = _gait(conv)
    up = lambda legs: S([o for L in legs for o in (hom(L, h0[L] + lift / 2), cod(L, k0[L] + lift))], juntos=True)  # noqa: E731
    return [S(base_stance(conv), juntos=True), {"op": "repetir", "n": n, "pasos": [
        up(["A", "C"]), S(base_stance(conv, ["A", "C"]), juntos=True),
        up(["B", "D"]), S(base_stance(conv, ["B", "D"]), juntos=True)]}]


# Códigos de rutina (12-10, para la tele y el teclado del HDMI): fijos y por familias, para poder aprendérselos.
# 10 (o P) = la de pie elegida. Las que no estén aquí cogen un código libre a partir del 100.
CODIGOS = {
    "plata-galope": 11, "plata-galope-der": 12, "plata-galope-suave": 13, "plata-canguro": 14, "plata-camello": 15,
    "plata-pata-sentado": 16, "bajar": 17,
    "de-pie-6.7": 18, "de-pie-6": 19, "de-pie-6.1": 20, "de-pie-6.2": 21, "de-pie-6.5": 22, "de-pie-6.3": 23,
    "de-pie-6.4": 24, "de-pie-6.6": 25, "pie-suave": 26, "pie-ayuda-d": 27, "pie-dos-tiempos": 28, "pie-ivan": 29,
    "galope-alto": 30, "galope-der": 31, "galope-largo": 32, "galope-rapido": 33, "galope-izq": 34, "galope-otra-mano": 35,
    "galope-turbo": 36, "galope": 37, "medio-galope": 38,
    "camello-adelante": 40, "camello-rapido": 41, "canguro": 42, "caballo-tiro": 43, "caballo-tiro-atras": 44,
    "lado-derecha": 45, "lado-izquierda": 46,
    "andar": 50, "andar-balanceo": 51, "andar-rapido": 52, "andar-alto": 53, "andar-alto-rapido": 54, "andar-pasitos": 55,
    "andar-largo": 56, "andar-atras": 57, "paseo": 58, "andar-lado": 59,
    "trote": 60, "trote-rapido": 61, "trote-alto": 62, "paso-caballo": 63, "tolt": 64, "ambladura": 65,
    "trote-eliptico": 66, "piaffe": 67, "passage": 68, "brinco": 69, "bote-gacela": 70, "girar-izq": 71, "girar-der": 72,
    "marcar-paso": 73,
    "balanceo-potente": 80, "baile": 81, "menear": 82, "pis": 83, "desperezarse": 84, "pata-b": 85, "pata-a": 86,
    "saludar-b": 87, "balanceo": 88, "reverencia": 89,
    "de-pie-alto": 90, "de-pie-bajo": 91, "tumbarse": 92, "levantarse": 93, "sentarse": 94, "flexiones": 95,
    # 12-10: galope fuerte (6 agresivas del galope plata a la derecha) y dar la pata sentado (A 96-99; B 16 y 47-49)
    "fuerte-zancada": 74, "fuerte-aire": 75, "fuerte-ritmo": 76, "fuerte-recto": 77, "fuerte-pisoton": 78, "fuerte-todo": 79,
    "pata-a-sentado-1": 96, "pata-a-sentado-2": 97, "pata-a-sentado-3": 98, "pata-a-sentado-4": 99,
    "pata-b-sentado-2": 47, "pata-b-sentado-3": 48, "pata-b-sentado-4": 49,
    # 12-10: las de oro (mejoras del galope fuerte · ritmo)
    "oro-limpio": 101, "oro-empuje": 102, "oro-b-hombro": 103, "oro-pisoton": 104, "oro-aire": 105, "oro-todo": 106,
}
CODIGO_DE_PIE = 10


def candidate_routines(conv_saved):
    """Todas las rutinas generadas, cada una con su `codigo` (fijo, ver CODIGOS)."""
    cands = _candidate_routines(conv_saved)
    free, used = 100, set(CODIGOS.values())
    for c in cands:
        if c["id"] in CODIGOS:
            c["codigo"] = CODIGOS[c["id"]]
        else:
            while free in used:  # no repetir un código fijo (p. ej. las de oro, 101-106)
                free += 1
            c["codigo"] = free
            free += 1
    return cands


def home_id(cands, cfg=None):
    """La de pie elegida (la marcada ✓); si no hay, la 6.7."""
    pr = (cfg or load_config()).get("pruebas", {})
    return next((c["id"] for c in cands if c.get("etapa") == 1 and c.get("conv") and pr.get(c["id"], {}).get("mark") == "bien"),
                "de-pie-6.7")


# 12-10, Luis: "las dos primeras de dar la pata nuevas van muy bien, y el galope fuerte también: dentro del HDMI".
FIJAS_TELE = ["plata-pata-sentado", "pata-b-sentado-2", "fuerte-ritmo"]  # códigos 16, 47 y 76


def popular_ids(cands, cfg):
    """Populares para la tele (panel HDMI y /lite): primero las fijas (las que mejor van), luego por estrellas de mayor
    a menor (3 o más) y al final las de plata aún sin valorar. Hasta 16."""
    known = {c["id"] for c in cands}
    val = {k: v.get("estrellas", 0) for k, v in cfg.get("valoraciones", {}).items()}
    ids = [i for i in FIJAS_TELE if i in known]
    ids += [k for k, n in sorted(val.items(), key=lambda kv: -kv[1]) if n >= 3 and k in known and k not in ids]
    ids += [c["id"] for c in cands if c.get("plata") and c["id"] not in val and c["id"] not in ids]
    return ids[:16]


def by_code(code, cands):
    """'10', 'P' o '0' = la de pie elegida; si no, la rutina con ese código."""
    code = str(code).strip().upper()
    if code in ("P", "0", str(CODIGO_DE_PIE)):
        hid = home_id(cands)
        return next(c for c in cands if c["id"] == hid)
    cand = next((c for c in cands if str(c.get("codigo")) == code), None)
    if not cand:
        raise ValueError(f"no hay ninguna rutina con el código {code}")
    return cand


def _candidate_routines(conv_saved):
    """Etapa 1: la 6 de 'de pie' y sus variantes (+ bajar despacio). Etapa 2 (con convención): rutinas útiles."""
    cands = []
    for vid, name, desc, front, rear, pec in VARIANTES_6:
        conv = CONV6 | {"variante": vid, "delanteras": front, "traseras": rear, "pectoral": pec}
        prog = stand_program(conv)
        tope = tope_warnings([S(base_stance(conv))])  # solo la postura final: al recoger, B3 (+45) ya se queda en su tope
        cands.append({"id": f"de-pie-{vid}", "etapa": 1, "nombre": name, "conv": conv, "programa": prog,
                      "favorita": FAVORITAS_6.index(vid) + 1 if vid in FAVORITAS_6 else 0,
                      "desc": f"{desc} · recoge las 4 patas y se levanta con las 4 a la vez"
                              + (f" · ⚠ {', '.join(tope)} llega a su tope y no sigue (recolocar el horn)" if tope else "")})
    cands.append({"id": "bajar", "etapa": 1, "ayuda": True, "nombre": "Bajar despacio",
                  "desc": "recoge las 4 patas a la vez hasta tumbarse: para repetir una prueba sin dejarlo caer",
                  "programa": [{"op": "velocidad", "v": "lento"},
                               S(no_pec(stance(conv_saved or CONV6, TUCK)), juntos=True)]})
    if not conv_saved:
        return cands
    c = conv_saved
    b0 = (c.get("delanteras", 40) + c.get("traseras", 40)) / 2
    st = lambda beta, **kw: S(stance(c, beta, **kw))  # noqa: E731
    st0 = S(base_stance(c))  # la de pie elegida
    walk = lambda l, r, n=3: walk_program(c, l, r, cycles=n)  # noqa: E731
    alta = c | {"delanteras": ALTA, "traseras": ALTA}  # patas más rectas: el codo hace menos fuerza
    two = [  # primero las de andar (las que se están probando, también en la barra de arriba), luego girar y el resto
        ("andar", "Andar · paso a paso", "Una pata cada vez, las otras 3 apoyadas (la más segura), 3 ciclos", walk(12, 12)),
        ("andar-balanceo", "Andar · con balanceo", "Paso a paso apartando el cuerpo de la pata que levanta (más estable si "
         "se cae hacia ella; si se cae más, el balanceo va al revés: dímelo)", walk_program(c, 12, 12, sway=10)),
        ("andar-rapido", "Andar · rápido", "Paso a paso con más ritmo: el pie sube y avanza de una vez, a velocidad rápida, 4 ciclos",
         walk_program(c, 12, 12, cycles=4, lift=14, fast=True)),
        ("andar-alto", "Andar · alto", f"Paso a paso con las patas más rectas (agachado {ALTA}): el codo hace menos fuerza, 3 ciclos",
         walk_program(alta, 12, 12)),
        ("andar-alto-rapido", "Andar · alto y rápido", f"Las dos cosas: patas más rectas ({ALTA}) y con ritmo, 4 ciclos",
         walk_program(alta, 12, 12, cycles=4, lift=14, fast=True)),
        ("andar-pasitos", "Andar · pasitos rápidos", "Pasos cortos (8) y bajos, rápidos: cada movimiento pide menos fuerza, 5 ciclos",
         walk_program(c, 8, 8, cycles=5, lift=12, fast=True)),
        ("andar-largo", "Andar · paso largo", "Pasos de 18 y pie más alto, 3 ciclos", walk_program(c, 18, 18, lift=18)),
        ("trote", "Andar · trote lento", "Diagonales A+C y B+D a la vez: más rápido, menos estable, 4 ciclos", trot_program(c)),
        ("trote-rapido", "Andar · trote rápido", "El trote con ritmo (sube, avanza y empuja de una vez): con ritmo se cae menos, 6 ciclos",
         trot_program(c, cycles=6, lift=14, fast=True)),
        ("trote-alto", "Andar · trote alto", f"Trote rápido con las patas más rectas ({ALTA}), 6 ciclos",
         trot_program(alta, cycles=6, lift=14, fast=True)),
        ("andar-lado", "Andar · de lado", "Como un cangrejo, con los pectorales (hacia la izquierda si pectoral + abre la pata), 3 ciclos",
         crab_program(c)),
        ("andar-atras", "Andar · hacia atrás", "Paso a paso hacia atrás, 3 ciclos", walk(-12, -12)),
        ("paseo", "Andar · paseo", "Ruta: adelante, gira a la izquierda, adelante, gira a la derecha (2 ciclos cada tramo)",
         walk(12, 12, 2) + walk(-12, 12, 2) + walk(12, 12, 2) + walk(12, -12, 2)),
        ("girar-izq", "Girar a la izquierda", "Izquierdas hacia atrás, derechas hacia delante, 3 ciclos", walk(-12, 12)),
        ("girar-der", "Girar a la derecha", "Izquierdas hacia delante, derechas hacia atrás, 3 ciclos", walk(12, -12)),
        ("marcar-paso", "Marcar el paso (en el sitio)", "Levanta en diagonal A+C y luego B+D, 4 veces", march_program(c)),
        ("de-pie-alto", "De pie alto", f"Patas más rectas (agachado {b0 - 10:g})", [st(b0 - 10)]),
        ("de-pie-bajo", "De pie bajo", f"Más agachado ({min(55, b0 + 12):g})", [st(min(55, b0 + 12))]),
        ("tumbarse", "Tumbarse", f"Despacio hasta recogido ({TUCK})",
         [{"op": "velocidad", "v": "lento"}, st0, st(TUCK)]),
        ("levantarse", "Levantarse", "Recoge las 4 patas y se levanta con las 4 a la vez, despacio", stand_program(c)),
        ("sentarse", "Sentarse", "Traseras muy dobladas, delanteras casi rectas",
         [{"op": "velocidad", "v": "lento"}, st0,
          S(stance(c, 55, legs=["C", "D"]) + stance(c, 20, legs=["A", "B"]))]),
        ("reverencia", "Estirarse (reverencia)", "Delanteras dobladas, traseras rectas, y vuelta",
         [{"op": "velocidad", "v": "lento"}, st0,
          S(stance(c, 55, legs=["A", "B"]) + stance(c, 25, legs=["C", "D"])), {"op": "esperar", "s": 1.5}, st0]),
        ("flexiones", "Flexiones", "Sube y baja 3 veces",
         [st0, {"op": "repetir", "n": 3, "pasos": [st(55), st(30)]}, st0]),
        ("balanceo", "Balancearse a los lados", "Pectorales a un lado y al otro, 3 veces",
         [st0, {"op": "repetir", "n": 3, "pasos": [
             S([{"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", 12 if L in ("A", "D") else -12, c)} for L in LEGS]),
             S([{"pata": L, "art": "pectoral", "angulo": dev_angle(L, "pectoral", -12 if L in ("A", "D") else 12, c)} for L in LEGS])]},
          st0]),
        ("saludar-b", "Saludar con la pata B", "Levanta la delantera derecha y la mueve",
         [st0, S([{"pata": "B", "art": "hombro", "angulo": dev_angle("B", "hombro", 55, c)},
                  {"pata": "B", "art": "codo", "angulo": dev_angle("B", "codo", 20, c)}]),
          {"op": "repetir", "n": 3, "pasos": [
              S([{"pata": "B", "art": "pectoral", "angulo": dev_angle("B", "pectoral", 15, c)}]),
              S([{"pata": "B", "art": "pectoral", "angulo": dev_angle("B", "pectoral", -15, c)}])]},
          st0]),
    ]
    for rid, name, desc, prog in two:
        cands.append({"id": rid, "etapa": 2, "nombre": name, "desc": desc, "programa": juntos_multi(prog),
                      "andar": rid.startswith(("andar", "trote", "paseo", "girar"))})
    for rid, name, desc, kw in CREATIVAS:
        cands.append({"id": rid, "etapa": 2, "nombre": name, "desc": desc, "creativa": True,
                      "programa": phase_gait(c, **kw)})
    for rid, name, desc, prog in nuevas_routines(c):  # las verdes (las curiosas, azules y sin estrellas)
        kind = {"curiosa": True, "color": "azul"} if rid in CURIOSAS else {"nueva": True}
        cands.append({"id": rid, "etapa": 2, "nombre": name, "desc": desc, "programa": prog} | kind)
    for rid, name, desc, prog in oro_routines(c):  # mejoras de la mejor (fila dorada en la caja de plata)
        cands.append({"id": rid, "etapa": 2, "nombre": name, "desc": desc, "plata": True, "oro": True, "color": "oro",
                      "grupo": "oro", "programa": prog})
    for rid, name, desc, prog, grupo in plata_routines(c):  # caja de plata: ganadoras, galope fuerte, dar la pata sentado
        cands.append({"id": rid, "etapa": 2, "nombre": name, "desc": desc, "plata": True, "color": "plata", "grupo": grupo,
                      "programa": prog})
    for rid, name, desc, prog in amarillas_routines(c):  # de pie suaves: amarillas; la de Iván, rosa
        cands.append({"id": rid, "etapa": 2, "nombre": name, "desc": desc, "suave": True, "programa": prog,
                      "color": "rosa" if rid == "pie-ivan" else "amarillo"})
    return [x for x in cands if x["id"] not in RETIRADAS]


def save_convention(conv):
    cfg = load_config()
    cfg["convencion"] = conv
    write_json(CONFIG_FILE, cfg)
    poses = load_poses()  # posturas básicas para los botones Recoger/Estirar y la tecla R/E del panel
    for name, objs in (("de pie", base_stance(conv)), ("estirada", stance(conv, 10)), ("recogida", stance(conv, TUCK))):
        for o in objs:
            poses.setdefault(o["pata"], {}).setdefault(name, {})[o["art"]] = o["angulo"]
    write_json(POSES_FILE, poses)


def routines_info():
    cfg = load_config()
    conv = cfg.get("convencion")
    saved = load_json(ROUTINES_FILE, {})
    cands = candidate_routines(conv)
    return {"guardadas": {k: {"origen": v.get("origen", ""), "pasos": count_steps(v["programa"]),
                              "bloques": bool(v.get("bloques"))} for k, v in saved.items()},
            "candidatas": [{k: v for k, v in c.items() if k != "programa"} | {"pasos": count_steps(c["programa"])}
                           for c in cands],
            "convencion": conv, "pruebas": cfg.get("pruebas", {}), "ejecutando": dict(routine),
            "marcha": gear(), "marchas": {str(k): v[0] for k, v in MARCHAS.items()},
            "valoraciones": {k: v["estrellas"] for k, v in cfg.get("valoraciones", {}).items()},
            "home": home_id(cands, cfg), "populares": popular_ids(cands, cfg), "codigo_de_pie": CODIGO_DE_PIE,
            "toreto": toreto["on"], "nitro": toreto["nitro"], "toreto_hz": toreto["hz"],
            "nitro_quedan_s": max(0, round(toreto["nitro_hasta"] - time.time())) if toreto["nitro"] else 0}


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
            "permitir_sin_corriente": bool(p.get("permitir_sin_corriente", True))}


# ---------- LED de vida en la placa (un LED en un canal libre de la PCA9685) ----------
# El LED rojo de la placa va directo a VCC y no se puede controlar. Un LED con resistencia en un canal libre sí:
# hace el mismo corto-corto-largo que el LED verde de la Pi (sistema/led_vivo.py), sincronizado por la hora.
LED_PATTERN = [(1, 0.15), (0, 0.15), (1, 0.15), (0, 0.15), (1, 0.6), (0, 0.9)]
LED_PERIOD = sum(d for _, d in LED_PATTERN)


def led_phase_on(t=None):
    t = (time.time() if t is None else t) % LED_PERIOD
    for on, d in LED_PATTERN:
        if t < d:
            return bool(on)
        t -= d
    return False


def led_cfg():
    c = load_json(CONFIG_FILE, {}).get("led_placa") or {}
    return {"activo": bool(c.get("activo")), "canal": int(c.get("canal", 9))}


def board_led_loop():
    last, cfg, cfg_t = None, led_cfg(), 0.0
    while True:
        if time.time() - cfg_t > 2:
            new = led_cfg()
            if last is not None and new["canal"] != cfg["canal"]:
                try:
                    kit._pca.channels[cfg["canal"]].duty_cycle = 0
                except Exception:  # noqa: BLE001
                    pass
                last = None
            cfg, cfg_t = new, time.time()
        if cfg["activo"]:
            on = led_phase_on()
            if on != last:
                try:
                    kit._pca.channels[cfg["canal"]].duty_cycle = 0xFFFF if on else 0
                except Exception:  # noqa: BLE001
                    pass
                last = on
        elif last is not None:
            try:
                kit._pca.channels[cfg["canal"]].duty_cycle = 0
            except Exception:  # noqa: BLE001
                pass
            last = None
        time.sleep(0.02)


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


def move_many(targets, pause=0.03, juntos=False):
    """Mueve varios canales a la vez, 1 grado por servo en cada paso (alternando entre ellos),
    para que todas las patas vayan juntas y suaves. Con el límite de consumo activado, los reparte
    en tandas para no pasar de los amperios de la fuente, salvo con `juntos`: al levantar o bajar el
    cuerpo, en tandas cada pata carga sola con su esquina y su servo no puede (se queda a medias)."""
    targets = {ch: clamp(a) for ch, a in targets.items()}
    held_others = sum(1 for ch, a in angles.items() if a is not None and ch not in targets)
    k = len(targets) if juntos or toreto["on"] else max_movers(len(targets), held_others)
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
            "progreso": {k: routine[k] for k in ("running", "nombre", "paso", "total", "detalle", "lista", "fin", "error")},
            "rechazadas_recientes": traffic["rechazadas"] if time.time() - traffic["ultima_rechazada"] < 8 else 0}


def _move_together(targets, pause):
    moving.update(targets)
    try:
        _move_together_inner(targets, pause)
    finally:
        moving.difference_update(targets)


def _move_together_inner(targets, pause):
    if toreto["on"]:
        _move_toreto(targets)
        return
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


def _move_toreto(targets):
    """Toreto: todos a la vez de un salto, pasándose TORETO_PATADA grados en el sentido del movimiento (el servo ve un
    error grande y mete toda la fuerza), espera lo que tarda en llegar y deja cada uno en su sitio."""
    delta = {ch: (targets[ch] - angles[ch]) if angles[ch] is not None else 0 for ch in targets}
    dmax = max((abs(d) for d in delta.values()), default=0)
    for ch, a in targets.items():
        d = delta[ch]
        write(ch, clamp(a + (TORETO_PATADA if d > 0 else -TORETO_PATADA)) if d else a)
    time.sleep(max(0.03, (dmax + TORETO_PATADA) * TORETO_S_POR_GRADO))
    for ch, a in targets.items():
        write(ch, a)
        angles[ch] = a


def set_toreto(on):
    """Modo Toreto sí/no (salto con patada y todo a la vez). Al quitarlo se quita también el Nitro."""
    toreto["on"] = bool(on)
    if not on and toreto["nitro"]:
        set_nitro(False)


def set_nitro(on):
    """Nitro: cambia el refresco de la PCA9685 (200 Hz / 50 Hz), recalcula el pulso de los 16 canales (la librería
    calcula el ancho de pulso con la frecuencia al configurar el rango) y vuelve a mandar los que sujetan."""
    kit._pca.frequency = TORETO_HZ if on else NORMAL_HZ
    for ch in range(16):
        kit.servo[ch].set_pulse_width_range(MIN_US, MAX_US)
    for ch, a in angles.items():
        if a is not None:
            write(ch, a)
    toreto.update(nitro=bool(on), hz=round(kit._pca.frequency), nitro_hasta=time.time() + NITRO_S if on else 0.0)


def nitro_watch():
    """El Nitro se apaga solo a los NITRO_S segundos (los MG996R analógicos se calientan a 200 Hz)."""
    while True:
        time.sleep(2)
        if toreto["nitro"] and time.time() > toreto["nitro_hasta"]:
            with lock:
                set_nitro(False)
            log(f"Nitro apagado solo tras {NITRO_S} s (vuelta a {NORMAL_HZ} Hz)")


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
    if toreto["on"] and cur is not None:
        _move_toreto({ch: target})
        return
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
                "toreto": toreto["on"], "nitro": toreto["nitro"], "toreto_hz": toreto["hz"],
                "nitro_quedan_s": max(0, round(toreto["nitro_hasta"] - time.time())) if toreto["nitro"] else 0,
                "led_placa": led_cfg(), "led_patron": {"pasos": LED_PATTERN, "periodo": LED_PERIOD, "hora_pi": time.time()},
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
        elif path in ("/lite", "/lite.html"):  # versión ligera para el navegador de la tele
            self._send(200, (WEB / "lite.html").read_bytes(), "text/html; charset=utf-8")
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
            if path == "/api/board_led":  # LED de vida en un canal libre de la placa
                ch, on = int(body.get("canal", 9)), bool(body.get("activo"))
                if not 0 <= ch <= 15:
                    raise ValueError("canal fuera de rango 0-15")
                data = load_map()
                m = data["canales"].get(str(ch), {})
                if on and m.get("pata"):
                    raise ValueError(f"el canal {ch} tiene un servo ({m['pata']} {m['articulacion']}): el LED va en un canal libre")
                old = led_cfg()
                for k, v in data["canales"].items():  # quitar la marca del canal anterior
                    if v.get("led"):
                        data["canales"][k] = {"libre": True}
                if on:
                    data["canales"][str(ch)] = {"libre": True, "led": True, "nota": "LED de vida (corto-corto-largo)"}
                save_map(data)
                cfg = load_config()
                cfg["led_placa"] = {"activo": on, "canal": ch}
                write_json(CONFIG_FILE, cfg)
                log(f"LED de vida en la placa: {'canal ' + str(ch) if on else 'apagado'} (antes {old})")
                self._send(200, self.state())
                return
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
            if path == "/api/toreto":  # Toreto sí/no (se guarda) y Nitro sí/no (200 Hz, no se guarda, se apaga solo)
                on = bool(body.get("activo", toreto["on"]))
                nitro = bool(body.get("nitro", toreto["nitro"])) and on
                with lock:
                    set_toreto(on)
                    if nitro != toreto["nitro"]:
                        set_nitro(nitro)
                cfg = load_config()
                cfg["toreto"] = on
                write_json(CONFIG_FILE, cfg)
                log(f"MODO TORETO {'ACTIVADO (salto con patada de ' + str(TORETO_PATADA) + '° y todo a la vez)' if on else 'desactivado'}"
                    + (f" · NITRO {TORETO_HZ} Hz durante {NITRO_S} s" if toreto["nitro"] else f" · {toreto['hz']} Hz"))
                self._send(200, self.state())
                return
            if path == "/api/power":
                amps = float(body.get("amperios", 5))
                if not 1 <= amps <= 60:
                    raise ValueError("amperios entre 1 y 60")
                cfg = load_config()
                cfg["potencia"] = {"limitar": bool(body.get("limitar", True)), "amperios": amps,
                                   "permitir_sin_corriente": bool(body.get("permitir_sin_corriente", True))}
                write_json(CONFIG_FILE, cfg)
                log(f"límite de consumo: {'activado' if cfg['potencia']['limitar'] else 'DESACTIVADO'} · fuente {amps:g} A "
                    f"· {max_movers(12, 0)} servos a la vez")
                self._send(200, self.state())
                return
            if path in ("/api/screen", "/api/screen_boot", "/api/system_action"):
                self.route_system(body)  # no tocan servos: sin esperar al cerrojo de movimiento
                return
            if path.startswith("/api/routine") or path in ("/api/try_mark", "/api/convention_reset", "/api/lock_all", "/api/home"):
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
            if b.get("codigo") not in (None, ""):  # desde la tele / el teclado del HDMI: por código (10 o P = de pie)
                cand = by_code(b["codigo"], candidate_routines(load_config().get("convencion")))
                start_routine(cand["programa"], cand["nombre"], loop=bool(b.get("repetir")))
                log(f"rutina por código {b['codigo']}: '{cand['nombre']}'")
            elif b.get("candidata"):
                cand = next((c for c in candidate_routines(load_config().get("convencion")) if c["id"] == b["candidata"]), None)
                if not cand:
                    raise ValueError("rutina de prueba desconocida")
                start_routine(cand["programa"], cand["nombre"], loop=bool(b.get("repetir")))
            elif b.get("nombre") and not b.get("programa"):
                r = load_json(ROUTINES_FILE, {}).get(b["nombre"])
                if not r:
                    raise ValueError(f"no existe la rutina '{b['nombre']}'")
                start_routine(r["programa"], b["nombre"], loop=bool(b.get("repetir")))
            else:
                start_routine(b.get("programa"), b.get("nombre") or "programa de bloques")
        elif p == "/api/lock_all":
            power_guard()
            prog, name, lista = plan_lock_all(b)
            start_routine(prog, name, lista)
        elif p == "/api/home":  # botón/tecla "De pie": para lo que esté en marcha y se pone en la de pie elegida
            if routine["running"]:
                routine["stop"] = True
                end = time.time() + 15
                while routine["running"] and time.time() < end:
                    time.sleep(0.1)
            cands = candidate_routines(load_config().get("convencion"))
            cand = next(c for c in cands if c["id"] == home_id(cands))
            start_routine(cand["programa"], cand["nombre"])
            log(f"de pie (botón P): '{cand['nombre']}'")
        elif p == "/api/routine_stop":
            routine["stop"] = True
            log("rutina: parar pedido")
        elif p == "/api/routine_rate":  # valoración del usuario, 0-6 estrellas (config.json "valoraciones")
            rid, n = str(b.get("id", "")), int(b.get("estrellas", 0))
            if not 0 <= n <= 6:
                raise ValueError("de 0 a 6 estrellas")
            if not any(c["id"] == rid for c in candidate_routines(load_config().get("convencion"))):
                raise ValueError("rutina desconocida")
            cfg = load_config()
            cfg.setdefault("valoraciones", {})[rid] = {"estrellas": n, "hora": datetime.now().isoformat(timespec="seconds")}
            write_json(CONFIG_FILE, cfg)
            log(f"valoración: '{rid}' {n}/6 estrellas")
        elif p == "/api/routine_speed":  # marcha 1-5; vale también con una rutina en marcha (se nota en el paso siguiente)
            n = int(b.get("marcha", 3))
            if n not in MARCHAS:
                raise ValueError("la marcha va de 1 a 6")
            cfg = load_config()
            cfg["marcha"] = n
            write_json(CONFIG_FILE, cfg)
            log(f"marcha de las rutinas: {MARCHAS[n][0]}")
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
            if mark == "bien" and cands[cid].get("conv"):
                for other, c in cands.items():  # solo una "de pie" buena: la última marcada
                    if other != cid and c.get("conv") and pruebas.get(other, {}).get("mark") == "bien":
                        pruebas.pop(other)
                write_json(CONFIG_FILE, cfg)
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
            if not isinstance(b.get("angle"), (int, float)):  # p. ej. un botón que no es de canal (pasó con las estrellas)
                raise ValueError("ángulo no válido: no se mueve nada")
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
            if action in ("zero", "invert") and angles[ch] is None:
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
            elif action == "nudge":   # corre el ajuste ±N°: todas las posturas y rutinas se corren igual en ese servo
                d = int(b.get("delta", 0))
                if not -30 <= d <= 30:
                    raise ValueError("el ajuste se corre como mucho 30° cada vez")
                new = max(-60, min(60, off + d))
                step = 1 if new > off else -1
                for o in range(off + step, new + step, step) if angles[ch] is not None else ():
                    CAL[ch] = (o, inv)  # si está sujeto, se ve moverse poco a poco
                    write(ch, angles[ch])
                    time.sleep(SPEEDS["lento"])
                off = new
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
                if data["canales"].get(key, {}).get("led") and led_cfg()["activo"]:
                    raise ValueError(f"el canal {ch} tiene el LED de vida: desactívalo antes de poner un servo")
                old = data["canales"].get(key, {})
                entry = {"pata": leg, "articulacion": joint}
                for f in ("offset", "invertir"):  # la calibración es del servo que está en este canal
                    if f in old:
                        entry[f] = old[f]
                if old.get("nota") and old.get("pata") == leg and old.get("articulacion") == joint:
                    entry["nota"] = old["nota"]
                tag = f"{leg}{JOINTS.index(joint) + 1}"
                # si esa pata/articulación estaba en otro canal, el servo se ha cambiado de conector:
                # el canal viejo queda libre y, si este canal no tenía calibración propia, se trae la del viejo
                for k, m in list(data["canales"].items()):
                    if k != key and m.get("pata") == leg and m.get("articulacion") == joint:
                        if "offset" not in old and "invertir" not in old:
                            for f in ("offset", "invertir"):
                                if f in m:
                                    entry[f] = m[f]
                        data["canales"][k] = {"libre": True, "nota": f"antes {tag} (pasado al canal {ch})"}
                        log(f"mapa: canal {k} libre: {tag} se ha pasado al canal {ch}")
                data["canales"][key] = entry
                log(f"mapa: canal {ch} = {tag} {joint}")
            save_map(data)
            load_cal()  # el ajuste de cada canal puede haber cambiado
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
    if load_config().get("toreto"):
        set_toreto(True)  # el Nitro (200 Hz) nunca se enciende solo al arrancar
        log("modo Toreto activo desde el arranque (sin Nitro)")
    threading.Thread(target=nitro_watch, daemon=True).start()
    boot_check()
    threading.Thread(target=power_monitor, daemon=True).start()
    threading.Thread(target=board_led_loop, daemon=True).start()
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
