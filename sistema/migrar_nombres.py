#!/usr/bin/python3
"""Migra servo_map.json y poses.json a la nomenclatura 2 (por número de articulación):
   1 hombro -> pectoral · 2 rodilla -> hombro · 3 tobillo -> codo.
Uso: migrar_nombres.py [carpeta del proyecto]   (no hace nada si ya está migrado)."""
import json
import sys
from pathlib import Path

OLD = ["hombro", "rodilla", "tobillo"]
NEW = ["pectoral", "hombro", "codo"]
root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "spotmicro"
mpath = root / "servo_map.json"
data = json.loads(mpath.read_text())
if data.get("nomenclatura") == 2:
    print("ya estaba con la nomenclatura 2 (pectoral/hombro/codo)")
    sys.exit(0)
for v in data["canales"].values():
    if "articulacion" in v:
        v["articulacion"] = NEW[OLD.index(v["articulacion"])]
data["nomenclatura"] = 2
data["_nota"] = ("Canal PCA9685 -> pata y articulación. Vista desde arriba, cabeza delante: A delantera izq, "
                 "B delantera der, C trasera der, D trasera izq. Articulaciones (del cuerpo al suelo): 1 pectoral "
                 "(abre/cierra al lado), 2 hombro (pata adelante/atrás), 3 codo (dobla la parte de abajo).")
mpath.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
ppath = root / "poses.json"
if ppath.exists():
    poses = json.loads(ppath.read_text())
    poses.pop("_candidatas", None)  # marcas de las posturas candidatas antiguas (ya no existen)
    for leg, named in poses.items():
        for name, pose in named.items():
            named[name] = {NEW[OLD.index(k)]: v for k, v in pose.items()}
    ppath.write_text(json.dumps(poses, indent=2, ensure_ascii=False) + "\n")
for k, v in sorted(data["canales"].items(), key=lambda x: int(x[0])):
    print(k, v)
print("migrado a pectoral/hombro/codo")
