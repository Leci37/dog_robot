#!/usr/bin/python3
"""Pantalla HDMI del SpotMicro (tty1, servicio spotmicro-pantalla).

Al arrancar la Pi ofrece un menú: [1] panel del robot, [2] página web en la pantalla (navegador
kiosco, necesita ratón) o [3] consola normal (la pantalla negra con login de siempre).
El panel muestra cómo conectarse desde el PC, el estado de la alimentación y teclas para
manejar el robot. Lo que se ve también se cambia desde la web: escribe pantalla/modo.
Todas las órdenes al robot pasan por la API de la web (http://127.0.0.1:8080).
"""
import curses
import json
import locale
import subprocess
import textwrap
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
SCREEN_DIR = ROOT / "pantalla"
MODE = SCREEN_DIR / "modo"            # panel | web | consola (lo pide la web o el teclado)
BOOT = SCREEN_DIR / "arranque"        # opción por defecto del menú al arrancar
STATUS = SCREEN_DIR / "estado"        # lo que se muestra ahora (lo lee la web)
MENU_SEEN = SCREEN_DIR / "menu_visto"  # boot_id del último arranque en que ya salió el menú
BROWSER = SCREEN_DIR / "navegador.sh"
BROWSER_LOG = Path("/tmp/spotmicro-kiosko.log")
HELP = ROOT / "AYUDA.md"
API = "http://127.0.0.1:8080"
FONTS = ["Lat15-TerminusBold24x12", "Uni2-TerminusBold24x12", "Lat15-Terminus24x12"]
MENU_SECONDS = 15
DIRECT_IP = "169.254.42.1"

UTF8 = False


def t(s):
    """Texto seguro para la consola: sin tildes si el terminal no está en UTF-8."""
    if UTF8:
        return s
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def rd(path, default=""):
    try:
        return path.read_text().strip() or default
    except OSError:
        return default


def wr(path, value):
    try:
        SCREEN_DIR.mkdir(exist_ok=True)
        path.write_text(value + "\n")
    except OSError:
        pass


def api(path, body=None, timeout=4):
    data = None if body is None else json.dumps(body).encode()
    req = Request(API + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def local_ips():
    """IPs de eth0 sin depender de la web (por si aún no ha arrancado)."""
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "eth0"], capture_output=True, text=True, timeout=3).stdout
        return [a["local"] for it in json.loads(out or "[]") for a in it.get("addr_info", [])]
    except Exception:  # noqa: BLE001
        return []


def browser_ok():
    return BROWSER.exists() and all(
        subprocess.run(["sh", "-c", f"command -v {p}"], capture_output=True).returncode == 0 for p in ("cage", "chromium"))


def boot_id():
    return rd(Path("/proc/sys/kernel/random/boot_id"), "?")[:8]


class Panel:
    def __init__(self, scr):
        self.scr = scr
        self.sys = None
        self.state = None
        self.last_fetch = 0
        self.msg = ""
        self.msg_until = 0
        self.confirm = None  # (tecla, hasta)
        self.rts = None

    # ---------- dibujo ----------
    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w - 1:
            try:
                self.scr.addnstr(y, x, t(s), w - 1 - x, attr)
            except curses.error:
                pass

    def say(self, s, secs=6):
        self.msg, self.msg_until = s, time.time() + secs

    def fetch(self):
        if time.time() - self.last_fetch < 2:
            return
        self.last_fetch = time.time()
        try:
            self.sys = api("/api/system")
            self.state = api("/api/state")
            if time.time() - getattr(self, "last_rt", 0) > 6:
                self.rts = api("/api/routines", timeout=8)
                self.last_rt = time.time()
        except Exception:  # noqa: BLE001
            self.sys = self.state = None

    def draw(self):
        self.fetch()
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        G, Y, R, C, H = (curses.color_pair(i) for i in (1, 2, 3, 4, 5))
        B = curses.A_BOLD
        self.put(0, 0, " " * (w - 1), H)
        self.put(0, 1, "LUIS LECI control · pantalla del robot", H | B)
        self.put(0, max(1, w - 10), datetime.now().strftime("%H:%M:%S"), H)
        y = 2
        s = self.sys
        if not s:
            self.put(y, 1, "La web del robot no responde todavía (arrancando o parada).", Y | B)
            y += 1
            ips = local_ips()
            self.put(y, 1, "IPs de la Pi: " + (", ".join(ips) if ips else "ninguna (¿cable de red conectado?)"))
            y += 1
            self.put(y, 1, f"Con cable directo prueba: http://{DIRECT_IP}   ·   SSH: ssh luislecinana@{DIRECT_IP}")
            y += 2
        else:
            net = s["net"]
            modes = {"router": ("ROUTER (con internet)", G), "directo": ("CABLE DIRECTO al PC (sin router)", C),
                     "sin-cable": ("SIN CABLE DE RED", R)}
            label, col = modes.get(net["mode"], (net["mode"], Y))
            self.put(y, 1, "CONEXIÓN", B)
            self.put(y, 15, label, col | B)
            y += 1
            self.put(y, 3, "Abre en el navegador del PC:")
            y += 1
            for u in net["urls"]:
                note = "(cable directo)" if DIRECT_IP in u else "(red del router)"
                self.put(y, 6, f"{u:<28} {note}", B)
                y += 1
            ssh_ip = next((a["ip"] for a in net["ipv4"] if a["if"] == "eth0" and a["ip"] != DIRECT_IP), DIRECT_IP)
            self.put(y, 3, f"SSH:  ssh luislecinana@{ssh_ip}      IPv6 fija: {s['ipv6_ll_fija']}")
            y += 2
            th = s.get("throttled") or 0
            if th & 1:
                ptxt, pcol = "FALTA CORRIENTE AHORA: cambia cargador o cable", R | B
            elif th & 0x10000 or s.get("caidas_tension_arranque"):
                ptxt, pcol = f"HUBO CAÍDAS desde que arrancó ({s.get('caidas_tension_arranque', 0)})", Y | B
            else:
                ptxt, pcol = "OK", G | B
            self.put(y, 1, "ALIMENTACIÓN", B)
            self.put(y, 15, ptxt, pcol)
            y += 1
            mins = s["uptime_s"] // 60
            self.put(y, 3, f"temperatura {s.get('temp_c', '?')} °C · encendida hace {mins // 60} h {mins % 60} min")
            y += 2
            held = sum(1 for a in (self.state or {}).get("angles", {}).values() if a is not None)
            self.put(y, 1, "ROBOT", B)
            self.put(y, 15, f"{held} servos sujetos" if held else "servos sueltos (sin fuerza)")
            rt = (self.state or {}).get("rutina") or {}
            if rt.get("running"):
                self.put(y, 42, f"RUTINA: {rt['nombre']} (paso {rt['paso']}/{rt['total']})  X = parar", Y | B)
            y += 1
            pw = (self.state or {}).get("potencia") or {}
            if pw:
                self.put(y, 1, "CONSUMO", B)
                self.put(y, 15, f"límite {pw['amperios']:g} A · {pw['a_la_vez_12']} servo(s) a la vez" if pw.get("limitar")
                         else "SIN LÍMITE (solo con fuente de 15-20 A)", 0 if pw.get("limitar") else Y | B)
                y += 1
            names = list(((self.rts or {}).get("guardadas") or {}).keys())[:9]
            self.put(y, 1, "RUTINAS", B)
            self.put(y, 15, "   ".join(f"{i + 1} {n}" for i, n in enumerate(names)) if names
                     else "(ninguna guardada: se crean desde la web)")
            y += 1
            scr_mode = s["screen"]
            web_txt = "W = web aquí" if scr_mode["navegador_instalado"] else "navegador no instalado"
            self.put(y, 1, "PANTALLA", B)
            self.put(y, 15, f"panel   ({web_txt} · C = consola normal)")
            y += 2
        self.put(y, 0, "-" * (w - 1), C)
        y += 1
        self.put(y, 1, "TECLAS", B)
        self.put(y, 9, "S soltar todo   M bloquear a mitad (90°)   R recoger   E estirar   1-9 rutina   X parar rutina")
        y += 1
        self.put(y, 9, "W web en esta pantalla   C consola (en vez del panel)   T consola aparte (Alt+F1 vuelve)")
        y += 1
        self.put(y, 9, "H ayuda   A apagar   B reiniciar")
        y += 1
        self.put(y, 0, "-" * (w - 1), C)
        y += 1
        if s and s.get("energia"):
            self.put(y, 1, "ÚLTIMOS EVENTOS (arranques y alimentación)", B)
            y += 1
            for line in s["energia"][-max(1, h - y - 3):]:
                self.put(y, 3, line, R if ("GOLPE" in line or "FALTA" in line) else 0)
                y += 1
        if self.confirm and time.time() < self.confirm[1]:
            key = self.confirm[0]
            what = "APAGAR" if key == "a" else "REINICIAR"
            self.put(h - 2, 1, f"¿Seguro que quieres {what} la Pi? Pulsa {key.upper()} otra vez para confirmar.", Y | B)
        elif time.time() < self.msg_until:
            self.put(h - 2, 1, self.msg, Y | B)
        scr.redrawwin()  # repinta todo: borra mensajes del kernel escritos encima
        scr.refresh()

    # ---------- acciones ----------
    def action(self, label, path, body, timeout=120):
        self.say(label + "…", 120)
        self.draw()
        try:
            api(path, body, timeout=timeout)
            self.say(label + ": hecho")
        except Exception as e:  # noqa: BLE001
            detail = getattr(e, "read", lambda: b"")()
            try:
                detail = json.loads(detail).get("error", "")
            except Exception:  # noqa: BLE001
                detail = str(e)
            self.say(f"{label}: no se pudo ({detail})", 10)

    def key(self, k):
        if k in (-1, curses.KEY_RESIZE):
            return None
        try:
            c = chr(k).lower()
        except ValueError:
            return None
        if c in ("a", "b"):
            if self.confirm and self.confirm[0] == c and time.time() < self.confirm[1]:
                self.confirm = None
                self.action("Apagando la Pi" if c == "a" else "Reiniciando la Pi", "/api/system_action",
                            {"accion": "apagar" if c == "a" else "reiniciar"}, timeout=10)
            else:
                self.confirm = (c, time.time() + 5)
            return None
        self.confirm = None
        if c == "s":
            self.action("Soltando todos los servos", "/api/off", {})
        elif c == "m":
            self.action("Bloqueando todo a mitad (90°)", "/api/pose90", {"speed": "normal"})
        elif c == "r":
            self.action("Recogiendo las patas", "/api/pose_go", {"pata": "todas", "nombre": "recogida"})
        elif c == "e":
            self.action("Estirando las patas", "/api/pose_go", {"pata": "todas", "nombre": "estirada"})
        elif c == "x":
            self.action("Parando la rutina", "/api/routine_stop", {}, timeout=10)
        elif c.isdigit() and c != "0":
            names = list(((self.rts or {}).get("guardadas") or {}).keys())
            i = int(c) - 1
            if i < len(names):
                self.action(f"Ejecutando la rutina '{names[i]}'", "/api/routine_run", {"nombre": names[i]}, timeout=10)
            else:
                self.say(f"No hay rutina {c}.")
        elif c == "w":
            return "web"
        elif c == "c":
            return "consola"
        elif c == "t":
            subprocess.run(["sudo", "-n", "chvt", "2"])  # logind abre un login en tty2; Alt+F1 vuelve aquí
        elif c == "h":
            self.help()
        return None

    def help(self):
        text = rd(HELP, "No hay ayuda (falta AYUDA.md).")
        top = 0
        while True:
            h, w = self.scr.getmaxyx()
            lines = []
            for para in text.splitlines():
                lines += textwrap.wrap(para, w - 3) or [""]
            self.scr.erase()
            self.put(0, 1, "AYUDA  (flechas / RePág / AvPág para moverte · H o Q para volver)", curses.color_pair(5) | curses.A_BOLD)
            for i, line in enumerate(lines[top:top + h - 2]):
                self.put(i + 1, 1, line)
            self.scr.redrawwin()
            self.scr.refresh()
            k = self.scr.getch()
            if k in (ord("h"), ord("q"), ord("H"), ord("Q"), 27):
                return
            if k in (curses.KEY_DOWN, ord("j")):
                top = min(top + 1, max(0, len(lines) - h + 2))
            elif k in (curses.KEY_UP, ord("k")):
                top = max(0, top - 1)
            elif k in (curses.KEY_NPAGE, ord(" ")):
                top = min(top + h - 3, max(0, len(lines) - h + 2))
            elif k == curses.KEY_PPAGE:
                top = max(0, top - h + 3)
            if rd(MODE, "panel") != "panel":
                return


def menu(p):
    """Menú del arranque. Devuelve 'panel', 'web' o 'consola'."""
    default = rd(BOOT, "panel")
    if default not in ("panel", "web", "consola"):
        default = "panel"
    names = {"panel": "[1] Panel del robot", "web": "[2] Página web del robot", "consola": "[3] Consola normal"}
    end = time.time() + MENU_SECONDS
    scr = p.scr
    wr(MODE, "menu")  # si la web pide otro modo mientras tanto, lo veremos cambiar
    while True:
        asked = rd(MODE, "menu")
        if asked in ("panel", "web", "consola"):
            return asked
        left = int(end - time.time())
        if left < 0:
            return default
        scr.erase()
        h, w = scr.getmaxyx()
        H, B, C = curses.color_pair(5), curses.A_BOLD, curses.color_pair(4)
        p.put(0, 0, " " * (w - 1), H)
        p.put(0, 1, "LUIS LECI control", H | B)
        p.put(2, 2, "¿Qué quieres ver en esta pantalla?", B)
        p.put(4, 4, "[1]  Panel del robot   (cómo conectarse, alimentación, rutinas y teclas)", B)
        web = "(navegador a pantalla completa; necesita ratón; va lento en la Pi 2)"
        if not browser_ok():
            web = "(NO INSTALADO)"
        p.put(6, 4, f"[2]  Página web del robot   {web}", B)
        p.put(8, 4, "[3]  Consola normal   (la pantalla negra con login; también con Ctrl+Alt+F2 en cualquier momento)", B)
        p.put(11, 4, f"Si no pulsas nada, en {left:2d} s:  {names[default]}", C | B)
        p.put(12, 4, "(La opción por defecto se cambia desde la web: sección Sistema > Pantalla HDMI)")
        ips = [ip for ip in local_ips() if ip != DIRECT_IP]
        p.put(15, 4, "Para entrar desde el PC:", B)
        p.put(16, 6, f"cable directo:  http://{DIRECT_IP}")
        p.put(17, 6, "con router:     " + ("   ".join(f"http://{ip}" for ip in ips) if ips else "(sin router ahora)"))
        scr.redrawwin()
        scr.refresh()
        k = scr.getch()
        if k in (ord("1"), ord("p"), 10, 13):
            return "panel" if k in (ord("1"), ord("p")) else default
        if k in (ord("2"), ord("w")):
            return "web"
        if k in (ord("3"), ord("c")):
            return "consola"


def run_browser(p):
    if not browser_ok():
        wr(MODE, "panel")
        p.say("El navegador no está instalado: la página web en la pantalla no está disponible.", 10)
        return
    wr(STATUS, "web (abriendo)")
    p.scr.erase()
    p.put(2, 2, "Abriendo la página web en la pantalla… (en la Pi 2 tarda 20-60 s)", curses.A_BOLD)
    p.put(4, 2, "Para volver a este panel: Alt+F4 en el navegador, o el botón 'Panel' en la web del PC.")
    p.scr.refresh()
    curses.endwin()
    with BROWSER_LOG.open("ab") as lf:
        proc = subprocess.Popen([str(BROWSER)], stdout=lf, stderr=lf)
        wr(STATUS, "web")
        while proc.poll() is None:
            if rd(MODE, "panel") != "web":
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                break
            time.sleep(1)
    code = proc.returncode
    if rd(MODE, "panel") == "web":  # se cerró solo (Alt+F4) o falló al abrir
        wr(MODE, "panel")
        if code not in (0, -15):
            p.say(f"El navegador se cerró con error ({code}). Detalles en {BROWSER_LOG}", 15)
    p.scr.clear()
    p.scr.refresh()


def go_console():
    wr(MODE, "consola")
    wr(STATUS, "consola")
    curses.endwin()
    # systemd para este panel (Conflicts=getty@tty1) y pone el login normal en tty1
    subprocess.run(["sudo", "-n", "systemctl", "--no-block", "start", "getty@tty1.service"])
    time.sleep(60)


def main(scr):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    for i, col in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_CYAN), 1):
        curses.init_pair(i, col, -1)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)
    scr.timeout(500)
    p = Panel(scr)
    if rd(MENU_SEEN) != boot_id():  # primera vez en este arranque de la Pi: menú
        wr(MENU_SEEN, boot_id())
        wr(STATUS, "menu")
        wr(MODE, menu(p))
    elif rd(MODE, "panel") not in ("panel", "web"):  # nos han vuelto a arrancar (p. ej. desde la web)
        wr(MODE, "panel")
    while True:
        mode = rd(MODE, "panel")
        if mode == "web":
            run_browser(p)
            continue
        if mode == "consola":
            go_console()
            return
        wr(STATUS, "panel")
        p.draw()
        nxt = p.key(scr.getch())
        if nxt:
            wr(MODE, nxt)


def on_sigterm(signum, frame):
    """systemd nos para (p. ej. al pasar a la consola normal): salir limpio, no como fallo."""
    try:
        curses.endwin()
    except curses.error:
        pass
    raise SystemExit(0)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, on_sigterm)
    for font in FONTS:
        if subprocess.run(["setfont", f"/usr/share/consolefonts/{font}.psf.gz"], capture_output=True).returncode == 0:
            break
    locale.setlocale(locale.LC_ALL, "")
    UTF8 = locale.getpreferredencoding(False).upper().replace("-", "") == "UTF8"
    curses.wrapper(main)
