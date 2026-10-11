#!/usr/bin/python3
"""Pantalla HDMI de LUIS LECI control (tty1, servicio spotmicro-pantalla).

Panel de control en modo texto (ligero: nada de navegador): las 16 salidas de la PCA9685 en vivo
(brillan al moverse), alimentación, voltaje, temperatura, CPU, memoria, red, quién está conectado,
consumo estimado de los servos, rutina en marcha y actividad reciente. Un indicador late para que
se vea que está vivo. Teclado: flechas para elegir y mover una salida, y las teclas de siempre.
Al arrancar ofrece un menú: [1] este panel, [2] la página web (lenta en la Pi 2), [3] consola.
Todas las órdenes al robot pasan por la API de la web (http://127.0.0.1:8080).
"""
import curses
import json
import locale
import shutil
import subprocess
import textwrap
import threading
import time
import unicodedata
from collections import deque
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
ACTIONS_LOG = ROOT / "web" / "acciones.log"
API = "http://127.0.0.1:8080"
# 10x20 da 128x36 caracteres en 1280x720; los Uni2 tienen █ ░ ─ │ ● ▲ ▼ (comprobado el 11-10-2026)
FONTS = ["Uni2-TerminusBold20x10", "Lat15-TerminusBold20x10", "Uni2-TerminusBold24x12", "Lat15-TerminusBold24x12"]
MENU_SECONDS = 15
DIRECT_IP = "169.254.42.1"
I_MOVE, I_HOLD = 1.2, 0.3
JN = {"pectoral": 1, "hombro": 2, "codo": 3}

UTF8 = False


def t(s):
    """Texto seguro para la consola: sin tildes ni símbolos si el terminal no está en UTF-8."""
    if UTF8:
        return s
    s = s.replace("█", "#").replace("░", ".").replace("●", "*").replace("▲", "^").replace("▼", "v").replace("─", "-")
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


def sh(cmd, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def local_ips():
    """IPs de eth0 sin depender de la web (por si aún no ha arrancado)."""
    try:
        return [a["local"] for it in json.loads(sh(["ip", "-j", "-4", "addr", "show", "eth0"]) or "[]")
                for a in it.get("addr_info", [])]
    except Exception:  # noqa: BLE001
        return []


def browser_ok():
    return BROWSER.exists() and bool(shutil.which("cage")) and bool(shutil.which("chromium"))


def boot_id():
    return rd(Path("/proc/sys/kernel/random/boot_id"), "?")[:8]


def bar(frac, width, full="█", empty="░"):
    n = max(0, min(width, round(frac * width)))
    return full * n + empty * (width - n)


def spark(values, lo, hi):
    chars = " .:-=+*#"
    if not values:
        return ""
    return "".join(chars[max(0, min(7, int((v - lo) / max(0.1, hi - lo) * 7)))] for v in values)


class Data:
    """Recoge los datos en segundo plano para que la pantalla nunca se quede congelada."""

    def __init__(self):
        self.state = self.sys = self.rts = None
        self.extra = {}
        self.temps = deque(maxlen=48)
        self.prev = {}
        self.flash = {}      # canal -> (hasta, dirección)
        self.ok = 0.0        # última vez que la web respondió
        self._cpu = None
        threading.Thread(target=self.loop_fast, daemon=True).start()
        threading.Thread(target=self.loop_slow, daemon=True).start()

    def loop_fast(self):
        while True:
            try:
                st = api("/api/state", timeout=3)
                now = time.time()
                for ch, a in st.get("angles", {}).items():
                    old = self.prev.get(ch, "nuevo")
                    if old != "nuevo" and old != a:
                        d = "▲" if (a or 0) > (old or 0) else "▼"
                        self.flash[int(ch)] = (now + 2.5, d if a is not None else "suelto")
                    self.prev[ch] = a
                self.state, self.ok = st, now
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)

    def loop_slow(self):
        n = 0
        while True:
            try:
                self.sys = api("/api/system", timeout=6)
                if self.sys.get("temp_c"):
                    self.temps.append(float(self.sys["temp_c"]))
            except Exception:  # noqa: BLE001
                pass
            if n % 3 == 0:
                try:
                    self.rts = api("/api/routines", timeout=8)
                except Exception:  # noqa: BLE001
                    pass
                self.extra.update(self.local_stats())
            n += 1
            time.sleep(3)

    def local_stats(self):
        out = {}
        try:
            f = [int(x) for x in Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
            idle, total = f[3] + f[4], sum(f)
            if self._cpu:
                di, dt = idle - self._cpu[0], total - self._cpu[1]
                out["cpu"] = 100 * (1 - di / dt) if dt else 0
            self._cpu = (idle, total)
            mem = dict(ln.split(":") for ln in Path("/proc/meminfo").read_text().splitlines() if ":" in ln)
            tot, avail = int(mem["MemTotal"].split()[0]), int(mem["MemAvailable"].split()[0])
            out["mem"] = (tot - avail) / tot
            out["mem_mb"] = (tot - avail) // 1024, tot // 1024
            du = shutil.disk_usage("/")
            out["disk_free_gb"] = du.free / 1e9
            out["load"] = Path("/proc/loadavg").read_text().split()[0]
        except Exception:  # noqa: BLE001
            pass
        out["volt"] = sh(["vcgencmd", "measure_volts", "core"]).replace("volt=", "")
        out["svc"] = {s: sh(["systemctl", "is-active", u]) == "active" for s, u in
                      (("web", "spotmicro-web"), ("led", "spotmicro-led"), ("vigilante", "spotmicro-vigilante.timer"),
                       ("ssh", "ssh"))}
        peers = set()
        for ln in sh(["ss", "-Htn", "state", "established"]).splitlines():
            p = ln.split()
            if len(p) >= 4 and (p[2].endswith(":80") or p[2].endswith(":8080") or p[2].endswith(":22")):
                ip = p[3].rsplit(":", 1)[0].strip("[]")
                if not ip.startswith("127.") and ip != "::1":
                    peers.add(ip.replace("::ffff:", ""))
        out["peers"] = sorted(peers)
        out["pca"] = sh(["/usr/sbin/i2cget", "-y", "1", "0x40", "0xfe"]).startswith("0x")
        try:
            tail = ACTIONS_LOG.read_bytes()[-3000:].decode("utf-8", "ignore").replace("\x00", "").splitlines()
            out["activity"] = [ln for ln in tail if ln.strip()][-6:]
        except OSError:
            out["activity"] = []
        return out


class Panel:
    def __init__(self, scr):
        self.scr = scr
        self.d = Data()
        self.msg, self.msg_until = "", 0
        self.confirm = None  # (tecla, hasta)
        self.sel = 4         # salida elegida con las flechas
        self.busy = False
        self.last_full = 0

    # ---------- dibujo ----------
    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and 0 <= x < w - 1:
            try:
                self.scr.addnstr(y, x, t(s), w - 1 - x, attr)
            except curses.error:
                pass

    def box(self, y, x, hgt, wid, title):
        try:
            win = self.scr.derwin(hgt, wid, y, x)
            win.attron(curses.color_pair(6))
            win.box()
            win.attroff(curses.color_pair(6))
        except curses.error:
            return
        self.put(y, x + 2, f" {title} ", curses.color_pair(4) | curses.A_BOLD)

    def say(self, s, secs=6):
        self.msg, self.msg_until = s, time.time() + secs

    def draw(self):
        scr, d = self.scr, self.d
        scr.erase()
        h, w = scr.getmaxyx()
        G, Y, R, C, H, F = (curses.color_pair(i) for i in (1, 2, 3, 4, 5, 6))
        B, DIM, REV = curses.A_BOLD, curses.A_DIM, curses.A_REVERSE
        now = time.time()
        alive = now - d.ok < 5
        # cabecera con latido
        self.put(0, 0, " " * (w - 1), H)
        self.put(0, 1, "LUIS LECI control · SpotMicro", H | B)
        beat = int(now * 2) % 2 == 0
        self.put(0, 34, "● VIVO" if alive else "● SIN WEB", (G if alive else R) | B | (0 if beat else DIM))
        self.put(0, 44, "LED · · —", H)
        up = (d.sys or {}).get("uptime_s", 0) // 60
        self.put(0, max(56, w - 36), f"encendida {up // 60} h {up % 60:02d} min   {datetime.now():%H:%M:%S}", H | B)
        lw = 66
        if w < 120 or h < 34:  # pantalla pequeña: solo las salidas y lo esencial
            self.draw_outputs(2, 0, min(lw, w - 1), G, Y, R, B, DIM, REV, now)
            s = d.sys or {}
            urls = (s.get("net") or {}).get("urls") or [f"http://{DIRECT_IP}"]
            th = s.get("throttled") or 0
            self.put(21, 1, "web: " + "  ".join(urls), B)
            self.put(22, 1, "alimentación: " + ("FALTA CORRIENTE" if th & 1 else "OK"), (R if th & 1 else G) | B)
            self.draw_footer(h, w)
            scr.refresh()
            return
        self.draw_outputs(2, 0, lw, G, Y, R, B, DIM, REV, now)
        rx = lw + 1
        rw = w - rx - 1
        self.draw_net(2, rx, rw, G, Y, R, C, B)
        self.draw_power(9, rx, rw, G, Y, R, B, DIM)
        self.draw_robot(18, rx, rw, G, Y, R, B, now)
        self.draw_activity(21, 0, lw, B, DIM, Y)
        self.draw_footer(h, w)
        if now - self.last_full > 5:  # repintado completo de vez en cuando: borra mensajes del kernel
            scr.redrawwin()
            self.last_full = now
        scr.refresh()

    def draw_outputs(self, y, x, wid, G, Y, R, B, DIM, REV, now):
        d = self.d
        self.box(y, x, 19, wid, "16 SALIDAS PCA9685 · ↑↓ elegir  ←→ mover 5°  P probar  L soltar")
        st = d.state or {}
        m, ang = st.get("map", {}), st.get("angles", {})
        self.put(y + 1, x + 2, "can pata/art.       ángulo 0-180°   grados   pulso  estado", DIM | B)
        for ch in range(16):
            yy = y + 2 + ch
            info, a = m.get(str(ch), {}), ang.get(str(ch))
            if info.get("pata"):
                lab = f"{info['pata']}{JN.get(info['articulacion'], '?')} {info['articulacion']}"
            elif info.get("libre"):
                lab = "libre"
            else:
                lab = "sin mapear"
            if info.get("nota"):
                lab += " !"
            fl = d.flash.get(ch)
            moving = fl and now < fl[0]
            if a is None:
                barra, grados, pulso, estado = "·" * 14, "  —", "     —", "suelto"
            else:
                off, inv = int(info.get("offset", 0)), bool(info.get("invertir", False))
                phys = max(5, min(175, (180 - a if inv else a) + off))
                barra = bar(a / 180, 14)
                grados, pulso, estado = f"{a:>3}°", f"{round(500 + phys * 2000 / 180):>4}µs", "SUJETO"
            if moving:
                estado = "MOVIENDO " + fl[1] if fl[1] in ("▲", "▼") else "soltado"
            line = f" {ch:>2} {lab:<15} {barra}  {grados:>5} {pulso:>7}  {estado:<11}"
            if ch == self.sel:
                attr = REV | B
            elif moving:
                attr = (Y | B | REV) if now < fl[0] - 1.8 else (Y | B)
            elif info.get("nota"):
                attr = R
            elif info.get("libre") or not info:
                attr = DIM
            elif a is not None:
                attr = G
            else:
                attr = 0
            self.put(yy, x + 1, line[:wid - 2], attr)

    def draw_net(self, y, x, wid, G, Y, R, C, B):
        d, s = self.d, self.d.sys or {}
        self.box(y, x, 7, wid, "CONEXIÓN")
        net = s.get("net") or {}
        modes = {"router": ("ROUTER · con internet", G), "directo": ("CABLE DIRECTO al PC", C), "sin-cable": ("SIN CABLE DE RED", R)}
        label, col = modes.get(net.get("mode"), ("…", Y))
        self.put(y + 1, x + 2, label, col | B)
        urls = net.get("urls") or [f"http://{ip}" for ip in local_ips()] + [f"http://{DIRECT_IP}"]
        self.put(y + 2, x + 2, "web: " + "   ".join(urls))
        ssh_ip = next((a["ip"] for a in net.get("ipv4", []) if a["if"] == "eth0" and a["ip"] != DIRECT_IP), DIRECT_IP)
        self.put(y + 3, x + 2, f"ssh luislecinana@{ssh_ip}")
        peers = d.extra.get("peers", [])
        self.put(y + 4, x + 2, f"conectados ahora: {len(peers)}  " + " ".join(peers[:3]), (G | B) if peers else 0)
        self.put(y + 5, x + 2, f"IPv6 {s.get('ipv6_ll_fija', 'fe80::ba27:ebff:fe16:ec15')}")

    def draw_power(self, y, x, wid, G, Y, R, B, DIM):
        d, s, e = self.d, self.d.sys or {}, self.d.extra
        self.box(y, x, 9, wid, "ALIMENTACIÓN · SISTEMA")
        th = s.get("throttled") or 0
        uv = s.get("caidas_tension_arranque", 0)
        if th & 1:
            ptxt, pc = "FALTA CORRIENTE AHORA", R | B
        elif th & 0x10000 or uv:
            ptxt, pc = f"hubo caídas ({uv})", Y | B
        else:
            ptxt, pc = "OK", G | B
        self.put(y + 1, x + 2, "Pi 5 V:")
        self.put(y + 1, x + 10, ptxt, pc)
        self.put(y + 1, x + 34, f"núcleo {e.get('volt', '?')}")
        temps = list(d.temps)
        tnow = temps[-1] if temps else 0
        self.put(y + 2, x + 2, f"temp {tnow:4.1f} °C ")
        self.put(y + 2, x + 17, spark(temps[-(wid - 20):], 25, 70), Y if tnow > 60 else G)
        cpu = e.get("cpu", 0)
        self.put(y + 3, x + 2, f"CPU  {cpu:3.0f}% ")
        self.put(y + 3, x + 13, bar(cpu / 100, 20), R if cpu > 85 else G)
        self.put(y + 3, x + 35, f"carga {e.get('load', '?')}")
        mem = e.get("mem", 0)
        used, tot = e.get("mem_mb", (0, 0))
        self.put(y + 4, x + 2, f"RAM  {mem * 100:3.0f}% ")
        self.put(y + 4, x + 13, bar(mem, 20), Y if mem > 0.8 else G)
        self.put(y + 4, x + 35, f"{used}/{tot} MB")
        self.put(y + 5, x + 2, f"SD libre {e.get('disk_free_gb', 0):.1f} GB")
        svc = e.get("svc", {})
        xx = x + 22
        for name in ("web", "led", "vigilante", "ssh"):
            on = svc.get(name)
            self.put(y + 5, xx, f"● {name} ", G if on else R)
            xx += len(name) + 3
        energy = s.get("energia") or []
        golpes = sum(1 for ln in energy if "DE GOLPE" in ln)
        self.put(y + 6, x + 2, f"placa PCA9685: {'responde' if e.get('pca') else 'NO RESPONDE'}", G if e.get("pca") else R | B)
        self.put(y + 6, x + 32, f"cortes recientes: {golpes}", Y if golpes else 0)
        last = next((ln for ln in reversed(energy) if "ARRANQUE" not in ln), energy[-1] if energy else "")
        self.put(y + 7, x + 2, ("último: " + last[11:])[:wid - 4], DIM)

    def draw_robot(self, y, x, wid, G, Y, R, B, now):
        d, st = self.d, self.d.state or {}
        self.box(y, x, 12, wid, "ROBOT · CONSUMO · RUTINAS")
        ang = st.get("angles", {})
        held = sum(1 for a in ang.values() if a is not None)
        moving = sum(1 for fl in d.flash.values() if now < fl[0] - 1.5)
        pw = st.get("potencia") or {}
        est = held * I_HOLD + moving * (I_MOVE - I_HOLD)
        lim = pw.get("amperios", 5)
        self.put(y + 1, x + 2, f"servos sujetos {held}/12   moviéndose {moving}")
        self.put(y + 2, x + 2, f"consumo estimado {est:4.1f} A ")
        self.put(y + 2, x + 26, bar(est / lim if lim else 0, 18), R if est > lim else (Y if est > 0.8 * lim else G))
        self.put(y + 2, x + 46, f"límite {lim:g} A" if pw.get("limitar", True) else "SIN LÍMITE", 0 if pw.get("limitar", True) else Y | B)
        rt = st.get("rutina") or {}
        if rt.get("running"):
            self.put(y + 3, x + 2, f"▶ {rt['nombre']}", Y | B)
            self.put(y + 4, x + 4, bar(rt["paso"] / max(1, rt["total"]), 30) + f" {rt['paso']}/{rt['total']}  (X parar)", Y)
        else:
            fin = f"última: {rt.get('nombre')} ({rt.get('fin')})" if rt.get("fin") else "sin rutina en marcha"
            self.put(y + 3, x + 2, fin)
        names = list(((d.rts or {}).get("guardadas") or {}).keys())[:9]
        self.put(y + 5, x + 2, "rutinas (teclas 1-9):", B)
        for i, n in enumerate(names[:6]):
            self.put(y + 6 + i // 2, x + 4 + (i % 2) * ((wid - 6) // 2), f"{i + 1} {n}"[:(wid - 8) // 2])
        if not names:
            self.put(y + 6, x + 4, "ninguna guardada todavía (se crean desde la web)")
        conv = ((d.rts or {}).get("convencion"))
        self.put(y + 10, x + 2, "sentido de las patas: " + ("encontrado" if conv else "pendiente (web > Rutinas para probar)"),
                 G if conv else Y)

    def draw_activity(self, y, x, wid, B, DIM, Y):
        self.box(y, x, 8, wid, "ACTIVIDAD RECIENTE")
        acts = self.d.extra.get("activity", [])
        for i, ln in enumerate(acts[-6:]):
            self.put(y + 1 + i, x + 2, ln[11:][:wid - 4], Y if i == len(acts[-6:]) - 1 else DIM)

    def draw_footer(self, h, w):
        C, Y, B = curses.color_pair(4), curses.color_pair(2), curses.A_BOLD
        self.put(h - 4, 1, "S soltar todo  M todo a mitad  R recoger  E estirar  1-9 rutina  X parar  H ayuda", C)
        self.put(h - 3, 1, "W web en la tele (lenta)  C consola  T consola aparte (Alt+F1 vuelve)  A apagar  B reiniciar", C)
        if self.confirm and time.time() < self.confirm[1]:
            what = "APAGAR" if self.confirm[0] == "a" else "REINICIAR"
            self.put(h - 2, 1, f"¿Seguro que quieres {what} la Pi? Pulsa {self.confirm[0].upper()} otra vez.", Y | B)
        elif time.time() < self.msg_until:
            self.put(h - 2, 1, self.msg, Y | B)

    # ---------- acciones (en segundo plano: la pantalla sigue viva) ----------
    def action(self, label, path, body, timeout=120):
        if self.busy:
            self.say("Espera: todavía estoy con la orden anterior.")
            return

        def work():
            self.busy = True
            self.say(label + "…", 120)
            try:
                api(path, body, timeout=timeout)
                self.say(label + ": hecho")
            except Exception as e:  # noqa: BLE001
                detail = str(e)
                try:
                    detail = json.loads(e.read()).get("error", detail)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                self.say(f"{label}: no se pudo ({detail})", 10)
            finally:
                self.busy = False
        threading.Thread(target=work, daemon=True).start()

    def key(self, k):
        if k in (-1, curses.KEY_RESIZE):
            return None
        st = self.d.state or {}
        if k == curses.KEY_UP:
            self.sel = (self.sel - 1) % 16
            return None
        if k == curses.KEY_DOWN:
            self.sel = (self.sel + 1) % 16
            return None
        if k in (curses.KEY_LEFT, curses.KEY_RIGHT):
            cur = st.get("angles", {}).get(str(self.sel))
            a = max(30, min(150, (cur if cur is not None else 90) + (5 if k == curses.KEY_RIGHT else -5)))
            self.action(f"Salida {self.sel} a {a}°", "/api/angle", {"ch": self.sel, "angle": a, "speed": "lento"}, timeout=20)
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
        if c == "p":
            self.action(f"Probando la salida {self.sel}", "/api/test",
                        {"ch": self.sel, "delta": 10, "center": 90, "speed": "lento", "hold": False}, timeout=30)
        elif c == "l":
            self.action(f"Soltando la salida {self.sel}", "/api/release", {"ch": self.sel}, timeout=10)
        elif c == "s":
            self.busy = False
            self.action("Soltando todos los servos", "/api/off", {})
        elif c == "m":
            self.action("Todo a mitad (90°)", "/api/pose90", {"speed": "normal"})
        elif c == "r":
            self.action("Recogiendo las patas", "/api/pose_go", {"pata": "todas", "nombre": "recogida"})
        elif c == "e":
            self.action("Estirando las patas", "/api/pose_go", {"pata": "todas", "nombre": "estirada"})
        elif c == "x":
            self.busy = False
            self.action("Parando la rutina", "/api/routine_stop", {}, timeout=10)
        elif c.isdigit() and c != "0":
            names = list(((self.d.rts or {}).get("guardadas") or {}).keys())
            i = int(c) - 1
            if i < len(names):
                self.action(f"Rutina '{names[i]}'", "/api/routine_run", {"nombre": names[i]}, timeout=10)
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
    names = {"panel": "[1] Panel de control", "web": "[2] Página web", "consola": "[3] Consola normal"}
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
        p.put(4, 4, "[1]  Panel de control   (las 16 salidas en vivo, alimentación, red, rutinas y teclas)", B)
        web = "(navegador a pantalla completa; necesita ratón; LENTO en la Pi 2)" if browser_ok() else "(NO INSTALADO)"
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
    curses.init_pair(6, curses.COLOR_BLUE, -1)
    scr.timeout(250)
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
