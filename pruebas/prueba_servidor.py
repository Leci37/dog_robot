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

BASE = "http://127.0.0.1:8099"
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
st = call("/api/pose_save", {"pata": "todas", "nombre": "dibujo", "angulos": {"4": 95, "2": 120, "0": 70}})
check(st["poses"]["A"]["dibujo"] == {"pectoral": 95, "hombro": 120, "codo": 70}, "guardar lo dibujado (copiar del robot)")
st = call("/api/lock_loose", {"a": "ultima"})
check(sum(1 for v in st["angles"].values() if v is not None) == 12 and st["angles"]["2"] == 110,
      "sujetar los sueltos donde estaban (12 sujetos, A2 en 110)")
call("/api/off", {})

# --- LED de vida en la placa ---
st = call("/api/board_led", {"activo": True, "canal": 9})
check(st["led_placa"] == {"activo": True, "canal": 9} and st["map"]["9"].get("led"), "LED de vida activado en el canal 9")
call("/api/board_led", {"activo": True, "canal": 2}, expect=400)
check(True, "no deja poner el LED en un canal con servo")
call("/api/map", {"ch": 9, "pata": "A", "articulacion": "codo"}, expect=400)
check(True, "no deja poner un servo en el canal del LED")
time.sleep(2.5)
leds = [ln for ln in open(FAKE_LOG) if ln.startswith("led9 ")]
check(len(leds) >= 2, f"el LED del canal 9 parpadea ({len(leds)} cambios en 2,5 s)")
st = call("/api/board_led", {"activo": False, "canal": 9})
check(not st["led_placa"]["activo"] and not st["map"]["9"].get("led"), "LED de vida desactivado y canal libre otra vez")

call("/api/convention_reset", {})
rt = call("/api/routines")
check(len([c for c in rt["candidatas"] if c["etapa"] == 1]) == 8 and not rt["convencion"], "etapa 1: 8 variantes de 'De pie'")
cid = rt["candidatas"][0]["id"]
call("/api/routine_run", {"candidata": cid})
fin = wait_routine()
check(fin["fin"] == "terminada", f"ejecuta '{cid}': {fin.get('fin')} {fin.get('error', '')}")
rt = call("/api/try_mark", {"id": cid, "mark": "bien"})
check(rt["convencion"] == {"sh": 1, "sk": 1, "fi": False}, "✓ en De pie guarda la convención")
stage2 = [c for c in rt["candidatas"] if c["etapa"] == 2]
check(len(stage2) >= 12, f"etapa 2: {len(stage2)} rutinas")
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
call("/api/routine_save", {"nombre": "andar", "candidata": "andar-corto"})
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
check(vals and min(vals) >= 30 and max(vals) <= 150, f"todos los ángulos enviados entre 30 y 150 ({min(vals)}–{max(vals)}, {len(vals)} órdenes)")

print("\nRESULTADO:", "TODO BIEN" if not fails else f"{len(fails)} FALLOS")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
