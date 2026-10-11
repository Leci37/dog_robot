#!/usr/bin/python3
"""Pruebas del servidor de LUIS LECI control contra el simulador de servos (puerto 8099).

Uso (en la Pi, desde una copia del proyecto):
  FAKE_LOG=/tmp/fake.log SPOTMICRO_PORTS=8099 SPOTMICRO_FAST=1 PYTHONPATH=pruebas/fake python3 web/server.py &
  python3 pruebas/prueba_servidor.py
"""
import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = os.environ.get("BASE", "http://127.0.0.1:8099")  # otro puerto si hay dos pruebas a la vez
FAKE_LOG = os.environ.get("FAKE_LOG", "/tmp/fake.log")
fails = []


def call(path, body=None, expect=200):
    req = Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=60) as r:
            data, code = r.read(), r.status
    except HTTPError as e:
        data, code = e.read(), e.code
    if code != expect:
        fails.append(f"{path}: código {code} (esperaba {expect}) {data[:200]!r}")
    try:
        return json.loads(data)
    except ValueError:
        return data


def check(cond, what):
    print(("OK   " if cond else "FALLO") + " " + what)
    if not cond:
        fails.append(what)


def wait_routine(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        st = call("/api/state")["rutina"]
        if not st["running"]:
            return st
        time.sleep(0.2)
    return {"fin": "timeout"}


page = call("/")
check(b"LUIS LECI control" in page, "la página carga con el nombre de la app")
blk = call("/static/blockly/blockly_compressed.js")
check(isinstance(blk, bytes) and len(blk) > 500000, "Blockly se sirve desde la Pi")
check(isinstance(call("/static/blockly/msg/es.js"), bytes), "Blockly en español")
call("/static/../server.py", expect=404)
check(True, "no deja salir de /static")
st = call("/api/state")
check(all(m.get("articulacion") in (None, "pectoral", "hombro", "codo") for m in st["map"].values()), "mapa con nombres pectoral/hombro/codo")
sysi = call("/api/system")
check("net" in sysi and "screen" in sysi, "/api/system responde")
con = call("/api/conexion")
check(con["via"] and con["ssh"]["usuario"], f"/api/conexion: entra {con['via']}")
check("contrasena" in con["ssh"] and "luislecinana" not in con["ssh"]["contrasena"], "no muestra la contraseña en claro")
check(b"AYUDA" in call("/ayuda"), "/ayuda responde")
lite = call("/lite")
check(isinstance(lite, bytes) and b"lite" in lite and b"blockly" not in lite.lower(), "versión lite (sin Blockly) en /lite")
ap = call("/api/system").get("apagones", {})
check({"total", "por_corriente", "lista"} <= set(ap), f"registro de apagados de golpe: {ap.get('total')} ({ap.get('por_corriente')} por corriente)")

# --- freno por falta de corriente, consumo, órdenes simultáneas, memoria de posición ---
cons = call("/api/consumo")
check("total_A" in cons and len(cons["canales"]) == 12, f"/api/consumo: {cons['total_A']} A, {len(cons['canales'])} servos")
call("/api/power", {"limitar": True, "amperios": 5, "permitir_sin_corriente": False})
if cons["falta_corriente"]:
    r = call("/api/angle", {"ch": 2, "angle": 95}, expect=400)
    check("CORRIENTE" in str(r), "con falta de corriente real, el freno no deja mover")
else:
    print("      (la Pi no tiene falta de corriente ahora: el freno no se puede comprobar de verdad)")
call("/api/power", {"limitar": True, "amperios": 5, "permitir_sin_corriente": True})  # el resto de pruebas, con permiso
call("/api/off", {})
call("/api/angle", {"ch": 2, "angle": 100})
check(call("/api/consumo")["total_A"] > 0, "un servo sujeto ya cuenta en el consumo")
import threading
res = {}
th = threading.Thread(target=lambda: res.update(a=call("/api/pose90", {"speed": "lento"})))
th.start(); time.sleep(0.4)
r = call("/api/angle", {"ch": 3, "angle": 100}, expect=409)
check("moviendo" in str(r), "una segunda orden mientras se mueve se rechaza (409), no se encola")
th.join()
check(call("/api/consumo")["rechazadas_recientes"] >= 1, "el aviso de 'muchos botones a la vez' se activa")
call("/api/angle", {"ch": 2, "angle": 110})
call("/api/off", {})
st = call("/api/state")
check(st["ultima"]["angulos"].get("2") == 110, "memoria de posición: recuerda el último ángulo aunque esté suelto")
st = call("/api/pose_save", {"pata": "todas", "nombre": "memo", "usar_ultima": True})
check(st["poses"]["A"]["memo"]["hombro"] == 110, "guardar con la última posición conocida")
chA = {m["articulacion"]: k for k, m in st["map"].items() if m.get("pata") == "A"}  # el codo de A cambió de canal (0 -> 1)
st = call("/api/pose_save", {"pata": "todas", "nombre": "dibujo", "angulos": {chA["pectoral"]: 95, chA["hombro"]: 120, chA["codo"]: 70}})
check(st["poses"]["A"]["dibujo"] == {"pectoral": 95, "hombro": 120, "codo": 70}, "guardar lo dibujado (copiar del robot)")
st = call("/api/lock_loose", {"a": "ultima"})
check(sum(1 for v in st["angles"].values() if v is not None) == 12 and st["angles"]["2"] == 110,
      "sujetar los sueltos donde estaban (12 sujetos, A2 en 110)")
call("/api/off", {})

# --- Bloquear todo y apuntar (barra de progreso): uno a uno con 5 A, bloquea-y-suelta con 3 A, a la vez con 20 A ---
call("/api/angle", {"ch": 2, "angle": 112})
call("/api/off", {})
call("/api/lock_all", {"segundos": 0.3})
time.sleep(0.6)
pr = call("/api/consumo")["progreso"]
check(pr["running"] and "uno a uno" in pr["nombre"] and pr["total"] == 12 and len(pr["lista"]) == 12
      and pr["detalle"].startswith("bloqueando"), f"bloquear todo con 5 A: uno a uno con barra ({pr['paso']}/{pr['total']}: {pr['detalle']})")
call("/api/angle", {"ch": 3, "angle": 100}, expect=400)
check(True, "mientras bloquea no deja mover a mano")
fin = wait_routine(60)
st = call("/api/state")
check(fin["fin"] == "terminada" and sum(1 for v in st["angles"].values() if v is not None) == 12 and st["angles"]["2"] == 112,
      f"bloquear todo: {fin.get('fin')}, 12 sujetos y A2 en su memoria (112)")
st = call("/api/pose_save", {"pata": "todas", "nombre": "bloqueada"})
check(st["poses"]["A"]["bloqueada"]["hombro"] == 112 and sum(len(p.get("bloqueada", {})) for p in st["poses"].values()) == 12,
      "después de bloquear, la postura se guarda con las 12")
call("/api/off", {})
call("/api/power", {"limitar": True, "amperios": 3, "permitir_sin_corriente": True})
call("/api/lock_all", {"segundos": 0.2})
fin = wait_routine(60)
st = call("/api/state")
check(fin["fin"] == "terminada" and "suelta" in fin["nombre"] and all(v is None for v in st["angles"].values())
      and len(st["ultima"]["angulos"]) >= 12, f"con 3 A: {fin['nombre']} (quedan sueltos y todo apuntado)")
call("/api/power", {"limitar": True, "amperios": 20, "permitir_sin_corriente": True})
call("/api/lock_all", {})
fin = wait_routine(60)
check(fin["fin"] == "terminada" and "a la vez" in fin["nombre"] and fin["total"] == 1, f"con 20 A: {fin['nombre']}")
call("/api/power", {"limitar": True, "amperios": 5, "permitir_sin_corriente": True})
call("/api/off", {})
call("/api/pose_delete", {"nombre": "bloqueada"})

# --- mapa: pasar un servo a otro conector deja libre el canal viejo ---
m0 = call("/api/state")["map"]
old = next(k for k, m in m0.items() if m.get("pata") == "D" and m.get("articulacion") == "codo")
new = next(str(c) for c in (8, 7, 1) if m0.get(str(c), {}).get("libre") and not m0[str(c)].get("led"))  # un canal libre
st = call("/api/map", {"ch": int(new), "pata": "D", "articulacion": "codo"})
check(st["map"][old].get("libre") and "antes D3" in st["map"][old].get("nota", "") and st["map"][new].get("pata") == "D",
      f"mapa: D3 del canal {old} al {new}, el {old} queda libre con nota")
call("/api/map", {"ch": int(old), "pata": "D", "articulacion": "codo"})
st = call("/api/state")
check(st["map"][old].get("pata") == "D" and st["map"][new].get("libre"), "mapa: D3 vuelve a su canal")
for k in (old, new):  # dejar el mapa como estaba
    if k not in m0:
        call("/api/map", {"ch": int(k), "clear": True})
    elif m0[k].get("libre"):
        call("/api/map", {"ch": int(k), "libre": True})
shape = lambda mp: {k: (m.get("pata"), m.get("articulacion"), bool(m.get("libre"))) for k, m in mp.items()}
m1 = call("/api/state")["map"]
check(shape(m1) == shape(m0) and m1[old].get("offset") == m0[old].get("offset"), "mapa restaurado (con su calibración)")

# --- LED de vida en la placa ---
st = call("/api/board_led", {"activo": True, "canal": 9})
check(st["led_placa"] == {"activo": True, "canal": 9} and st["map"]["9"].get("led"), "LED de vida activado en el canal 9")
call("/api/board_led", {"activo": True, "canal": 2}, expect=400)
check(True, "no deja poner el LED en un canal con servo")
call("/api/map", {"ch": 9, "pata": "A", "articulacion": "codo"}, expect=400)
check(True, "no deja poner un servo en el canal del LED")
time.sleep(4.5)  # el servidor relee la configuración del LED cada 2 s
leds = [ln for ln in open(FAKE_LOG) if ln.startswith("led9 ")]
check(len(leds) >= 2, f"el LED del canal 9 parpadea ({len(leds)} cambios en 4,5 s)")
st = call("/api/board_led", {"activo": False, "canal": 9})
check(not st["led_placa"]["activo"] and not st["map"]["9"].get("led"), "LED de vida desactivado y canal libre otra vez")

# --- ajuste fino de un servo (calibración ±N°): el ángulo lógico no cambia, el físico sí ---
call("/api/off", {})
m0 = call("/api/state")["map"]
chB3 = next(k for k, m in m0.items() if m.get("pata") == "B" and m.get("articulacion") == "codo")
off0 = m0[chB3].get("offset", 0)
call("/api/angle", {"ch": int(chB3), "angle": 100})
st = call("/api/calibrate", {"ch": int(chB3), "action": "nudge", "delta": 5})
check(st["map"][chB3]["offset"] == off0 + 5 and st["angles"][chB3] == 100, f"B3 (canal {chB3}): ajuste {off0} -> +5 sin cambiar su ángulo lógico")
st = call("/api/calibrate", {"ch": int(chB3), "action": "nudge", "delta": -5})
check(st["map"][chB3]["offset"] == off0, "ajuste devuelto a como estaba")
call("/api/calibrate", {"ch": int(chB3), "action": "nudge", "delta": 40}, expect=400)
check(True, "no deja correr el ajuste más de 30° de golpe")
call("/api/off", {})

call("/api/convention_reset", {})
rt = call("/api/routines")
e1 = [c for c in rt["candidatas"] if c["etapa"] == 1]
check(len([c for c in e1 if not c.get("ayuda")]) == 8 and any(c["id"] == "bajar" for c in e1) and not rt["convencion"],
      "etapa 1: la 6 y sus 7 variantes (con la 6.7), más 'Bajar despacio'")
check(all("variante" not in c["nombre"] for c in e1), "ya no están las 8 variantes viejas de 'De pie'")
cid = "de-pie-6"
call("/api/routine_run", {"candidata": cid})
time.sleep(0.3)
fin = wait_routine()
check(fin["fin"] == "terminada", f"ejecuta '{cid}': {fin.get('fin')} {fin.get('error', '')}")
check(call("/api/state")["potencia"]["ultima_tanda"] == 8, "al levantarse, hombros y codos de las 4 patas van juntos (8) aunque la fuente sea de 5 A")
call("/api/routine_run", {"candidata": "bajar"})
check(wait_routine()["fin"] == "terminada", "bajar despacio")
call("/api/routine_run", {"candidata": "de-pie-6.2"})
check(wait_routine()["fin"] == "terminada", "de pie 6.2 (alta)")
rt = call("/api/try_mark", {"id": "de-pie-6", "mark": "bien"})
rt = call("/api/try_mark", {"id": "de-pie-6.2", "mark": "bien"})
check({k: rt["convencion"][k] for k in ("sh", "sk", "fi")} == {"sh": -1, "sk": 1, "fi": True} and rt["convencion"]["variante"] == "6.2",
      f"✓ en la 6.2 guarda la convención de la 6 con su altura: {rt['convencion']}")
check(rt["pruebas"].get("de-pie-6.2", {}).get("mark") == "bien" and "de-pie-6" not in rt["pruebas"], "solo queda una de pie marcada ✓")
stage2 = [c for c in rt["candidatas"] if c["etapa"] == 2]
check(len(stage2) >= 12, f"etapa 2: {len(stage2)} rutinas")
walks = [c["id"] for c in stage2 if c.get("andar")]
check(len(walks) >= 12 and stage2[0]["id"] == "andar" and {"andar-rapido", "andar-alto", "trote-rapido", "andar-lado"} <= set(walks),
      f"rutinas de andar (van primero): {len(walks)}")
check(len([c for c in stage2 if c.get("creativa")]) == 10, "10 marchas creativas (caballo y compañía)")
favs = sorted((c for c in rt["candidatas"] if c.get("favorita")), key=lambda c: c["favorita"])
check([c["id"] for c in favs] == ["de-pie-6.7", "de-pie-6", "de-pie-6.1", "de-pie-6.2", "de-pie-6.5"], "las 5 de pie favoritas para la barra de arriba")
rt = call("/api/routine_speed", {"marcha": 5})
check(rt["marcha"] == 5 and rt["marchas"]["1"].startswith("1ª"), f"marcha 5: {rt['marchas']['5']}")
call("/api/routine_speed", {"marcha": 9}, expect=400)
check(True, "marcha fuera de 1-5 rechazada")
call("/api/routine_run", {"programa": [{"op": "repetir", "n": 50, "pasos": [{"op": "esperar", "s": 30}]}], "nombre": "larga"})
time.sleep(0.3)
rt = call("/api/routine_speed", {"marcha": 1})
check(rt["marcha"] == 1 and rt["ejecutando"]["running"], "la marcha se cambia con una rutina en marcha")
call("/api/routine_stop", {})
wait_routine(10)
poses = call("/api/state")["poses"]
check(all(n in poses.get("A", {}) for n in ("de pie", "estirada", "recogida")), "posturas básicas guardadas")
for c in stage2:
    call("/api/routine_run", {"candidata": c["id"]})
    fin = wait_routine()
    check(fin["fin"] == "terminada", f"rutina '{c['nombre']}': {fin.get('fin')} {fin.get('error', '')}")
prog = [{"op": "velocidad", "v": "rapido"}, {"op": "articulacion", "pata": "todas", "art": "codo", "angulo": 110},
        {"op": "repetir", "n": 2, "pasos": [{"op": "articulacion_rel", "pata": "A", "art": "hombro", "delta": 10},
                                             {"op": "esperar", "s": 0.1}]},
        {"op": "pata", "pata": "B", "pectoral": 95, "hombro": 100, "codo": 120},
        {"op": "set", "objetivos": [{"pata": "C", "art": "codo", "angulo": 140}]},
        {"op": "postura", "nombre": "de pie"}, {"op": "mitad"}, {"op": "soltar", "pata": "D"}, {"op": "soltar"}]
call("/api/routine_run", {"programa": prog, "nombre": "prueba bloques"})
fin = wait_routine()
check(fin["fin"] == "terminada", f"programa de bloques: {fin.get('fin')} {fin.get('error', '')}")
call("/api/routine_save", {"nombre": "prueba", "programa": prog, "bloques": {"blocks": {}}})
check("prueba" in call("/api/routines")["guardadas"], "guarda rutina")
check(call("/api/routine_get", {"nombre": "prueba"})["programa"] == prog, "lee rutina")
call("/api/routine_save", {"nombre": "andar", "candidata": "andar"})
call("/api/routine_run", {"programa": [{"op": "rutina", "nombre": "andar"}, {"op": "rutina", "nombre": "prueba"}], "nombre": "anidada"})
fin = wait_routine()
check(fin["fin"] == "terminada", f"rutina dentro de rutina: {fin.get('fin')} {fin.get('error', '')}")
LARGA = [{"op": "repetir", "n": 50, "pasos": [{"op": "esperar", "s": 30}]}]  # ~15 s incluso en modo rápido
call("/api/routine_run", {"programa": LARGA, "nombre": "larga"})
time.sleep(0.5)
call("/api/angle", {"ch": 2, "angle": 100}, expect=400)
check(True, "con una rutina en marcha no deja mover a mano")
call("/api/routine_stop", {})
fin = wait_routine(10)
check(fin["fin"] == "parada", f"parar rutina: {fin.get('fin')}")
call("/api/routine_run", {"programa": LARGA, "nombre": "larga2"})
time.sleep(0.5)
call("/api/off", {})
fin = wait_routine(10)
check(fin["fin"] == "parada", "Soltar todo para la rutina")
call("/api/routine_run", {"programa": [{"op": "volar"}], "nombre": "mala"})
fin = wait_routine(10)
check(fin["fin"] == "error", f"paso desconocido da error controlado: {fin.get('error')}")
call("/api/routine_run", {"programa": []}, expect=400)
check(True, "programa vacío rechazado")
for n in ("prueba", "andar"):
    call("/api/routine_delete", {"nombre": n})
check(not call("/api/routines")["guardadas"], "borra rutinas")
call("/api/convention_reset", {})
call("/api/routine_speed", {"marcha": 3})

st = call("/api/power", {"limitar": True, "amperios": 5, "permitir_sin_corriente": True})
check(st["potencia"]["a_la_vez_12"] == 1, f"límite 5 A: {st['potencia']['a_la_vez_12']} servo a la vez")
call("/api/off", {})
call("/api/pose90", {"speed": "rapido"})
check(call("/api/state")["potencia"]["ultima_tanda"] == 1, "con 5 A 'todo a mitad' va en tandas de 1")
st = call("/api/power", {"limitar": True, "amperios": 20, "permitir_sin_corriente": True})
check(st["potencia"]["a_la_vez_12"] == 12, f"límite 20 A: {st['potencia']['a_la_vez_12']} a la vez")
call("/api/group", {"articulacion": "codo", "angle": 100, "speed": "rapido"})
check(call("/api/state")["potencia"]["ultima_tanda"] == 4, "con 20 A el grupo de 4 codos va junto")
call("/api/power", {"limitar": False, "amperios": 5, "permitir_sin_corriente": True})
call("/api/pose90", {"speed": "rapido"})
check(call("/api/state")["potencia"]["ultima_tanda"] == 12, "sin límite: los 12 a la vez")
call("/api/power", {"amperios": 0.5}, expect=400)
call("/api/power", {"limitar": True, "amperios": 5, "permitir_sin_corriente": True})

st = call("/api/leg_check", {"pata": "C", "ok": True})
check(st["verificacion"]["C"]["ok"] is True, "pata C marcada como 'coincide con el dibujo'")
st = call("/api/leg_check", {"pata": "A", "ok": False})
check(st["verificacion"]["A"]["ok"] is False, "pata A marcada como 'no coincide'")
call("/api/leg_check", {"pata": "Z", "ok": True}, expect=400)
st = call("/api/leg_check", {"pata": "A", "ok": None})
check("A" not in st["verificacion"], "borra la marca de la pata A")
call("/api/leg_check", {"pata": "C", "ok": None})
check("convencion" in st and "verificacion" in st, "el estado lleva convención y verificación para el robot en vivo")

rep = call("/api/informe")
check(rep["resumen"]["ok"] > 5, f"informe: {rep['resumen']}")
for it in rep["items"]:
    print(f"      [{it['estado']:5}] {it['area']}: {it['item']} — {it['detalle']}")

vals = [float(ln.split()[1]) for ln in open(FAKE_LOG) if not ln.startswith("led") and ln.split()[1] != "None"]
# ángulos físicos: los lógicos 30-150 más la calibración de cada servo (B3 +45), siempre dentro de 5-175
check(vals and min(vals) >= 5 and max(vals) <= 175, f"todos los ángulos enviados entre 5 y 175 ({min(vals)}–{max(vals)}, {len(vals)} órdenes)")

print("\nRESULTADO:", "TODO BIEN" if not fails else f"{len(fails)} FALLOS")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
