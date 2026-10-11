"""Genera el esquema de conexiones del SpotMicro (LUIS LECI control).

Salidas:
  1) esquema.svg autónomo (colores fijos, fondo blanco): lo sirve la Pi en /static/esquema.svg y va en la documentación.
  2) el mismo SVG con clases para el artefacto (los colores los pone la página según el tema).
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape as e

OUT_SVG = Path(sys.argv[1])
OUT_INLINE = Path(sys.argv[2])
W, H = 1000, 1290

# canal -> (etiqueta, articulación, estado, nota corta)
CH = {
    0: ("A3", "codo", "bad", "no va"),
    1: ("—", "libre", "free", "era B3"),
    2: ("A2", "hombro", "check", ""),
    3: ("B2", "hombro", "check", ""),
    4: ("A1", "pectoral", "check", ""),
    5: ("B1", "pectoral", "check", ""),
    6: ("B3", "codo", "warn", "girado"),
    7: ("—", "libre", "free", ""),
    8: ("—", "libre", "free", ""),
    9: ("—", "libre", "free", ""),
    10: ("C1", "pectoral", "ok", ""),
    11: ("D1", "pectoral", "ok", ""),
    12: ("C2", "hombro", "ok", ""),
    13: ("D2", "hombro", "ok", ""),
    14: ("C3", "codo", "ok", ""),
    15: ("D3", "codo", "ok", ""),
}
LEGS = {  # pata: (nombre, y, sentido x, [(articulación, canal)])
    "A": ("A · delantera izquierda", 900, -1, [("pectoral", 4), ("hombro", 2), ("codo", 0)]),
    "B": ("B · delantera derecha", 900, 1, [("pectoral", 5), ("hombro", 3), ("codo", 6)]),
    "D": ("D · trasera izquierda", 1100, -1, [("pectoral", 11), ("hombro", 13), ("codo", 15)]),
    "C": ("C · trasera derecha", 1100, 1, [("pectoral", 10), ("hombro", 12), ("codo", 14)]),
}
LEG_NOTE = {
    "A": [("bad", "El codo (canal 0) no responde: la placa manda señal,"), ("bad", "falla el servo o su cable.")],
    "B": [("warn", "Codo en el canal 6 (antes en el 1). A 90° queda"), ("warn", "muy estirado: horn girado. Calibrar o recolocar.")],
    "D": [("ok", "Bien: a 90° queda recta y coordinada.")],
    "C": [("ok", "Bien: a 90° queda recta y coordinada.")],
}

LIGHT_CSS = """
.bg{fill:#ffffff}.t{fill:#1b2733;font:14px 'IBM Plex Sans',system-ui,'Segoe UI',sans-serif}
.ts{fill:#5b6b7a;font:12px 'IBM Plex Sans',system-ui,'Segoe UI',sans-serif}
.tb{fill:#1b2733;font:600 14px 'IBM Plex Sans',system-ui,'Segoe UI',sans-serif}
.th{fill:#1b2733;font:600 19px 'Barlow Condensed','Arial Narrow',system-ui,sans-serif;letter-spacing:.02em}
.tm{fill:#1b2733;font:600 13px 'IBM Plex Mono',ui-monospace,Consolas,monospace}
.box{fill:#f3f6f8;stroke:#8a98a6;stroke-width:1}
.pcb{fill:#e6efe9;stroke:#3f7a5a;stroke-width:1.2}.pi{fill:#e8f1e6;stroke:#4f8a3a;stroke-width:1.2}
.psu{fill:#f4efe6;stroke:#8a6d3b;stroke-width:1.2}.body{fill:#eef1f4;stroke:#8a98a6;stroke-width:1.2}
.warnbox{fill:#fdf1dc;stroke:#c27c12;stroke-width:1}.dangerbox{fill:#fbe4e4;stroke:#c0392b;stroke-width:1}
.vplus{stroke:#d0312d}.gnd{stroke:#2b2b2b}.sda{stroke:#1f6fd1}.scl{stroke:#c99400}.vcc{stroke:#ef7d1a}
.sig{fill:#ef8a1a}.pv{fill:#d0312d}.pg{fill:#6b4a2b}.pinoff{fill:#c9d1d8}.pinon{fill:#1b2733}
.ok{fill:#e3f4ea;stroke:#2f8f5b}.check{fill:#e7f0fb;stroke:#3c6fb4}.warn{fill:#fdf1dc;stroke:#c27c12}
.bad{fill:#fbe4e4;stroke:#c0392b}.free{fill:#eef0f2;stroke:#a3acb5}
.okt{fill:#1f6b43}.checkt{fill:#2b558f}.warnt{fill:#8a560a}.badt{fill:#a12a1f}.freet{fill:#6b7681}
.limb{stroke:#9aa6b1}.arrow{stroke:#5b6b7a;fill:none}
.pinlon{fill:#ffffff;font:600 11px 'IBM Plex Mono',ui-monospace,Consolas,monospace}
.pinloff{fill:#5b6b7a;font:11px 'IBM Plex Mono',ui-monospace,Consolas,monospace}
"""


def text(x, y, s, cls="t", anchor="start"):
    return f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{e(s)}</text>'


def build():
    p = []
    a = p.append
    a(f'<rect class="bg" x="0" y="0" width="{W}" height="{H}"/>')

    # ---------- 1. alimentación y control ----------
    a(text(30, 40, "ALIMENTACIÓN Y CONTROL", "th"))
    a('<rect class="psu" x="30" y="70" width="210" height="84" rx="8"/>')
    a(text(45, 96, "Cargador de la Pi", "tb"))
    a(text(45, 118, "5,1 V · 2,5 A (oficial)", "t"))
    a(text(45, 138, "cable micro USB corto y grueso", "ts"))
    a('<line x1="240" y1="112" x2="296" y2="112" class="vplus" stroke-width="3"/>')
    a(text(268, 104, "USB", "ts", "middle"))

    a('<rect class="pi" x="300" y="60" width="270" height="190" rx="10"/>')
    a(text(316, 86, "Raspberry Pi 2 Model B", "tb"))
    a(text(316, 106, "I2C bus 1 · Ethernet · HDMI", "ts"))
    # cabecera GPIO: 2 filas x 6 columnas (pin 1 abajo a la izquierda)
    gx, gy = 330, 128
    a(text(gx - 2, gy - 6, "GPIO (pines 1-12)", "ts"))
    for col in range(6):
        for row in range(2):
            pin = col * 2 + (2 if row == 0 else 1)
            cx, cy = gx + 12 + col * 26, gy + 12 + row * 26
            on = pin in (1, 3, 5, 6)
            a(f'<rect x="{cx - 9}" y="{cy - 9}" width="18" height="18" rx="3" class="{"pinon" if on else "pinoff"}"/>')
            a(f'<text x="{cx}" y="{cy + 4}" class="{"pinlon" if on else "pinloff"}" text-anchor="middle">{pin}</text>')
    a(text(gx, gy + 76, "2 y 4 = 5 V: no usar para la placa", "ts"))
    a(text(316, 228, "Ethernet → PC directo (169.254.42.1) o router", "ts"))
    a(text(316, 244, "HDMI → tele: menú, panel, web o consola", "ts"))

    a('<rect class="pcb" x="660" y="60" width="310" height="190" rx="10"/>')
    a(text(676, 86, "Placa de servos PCA9685", "tb"))
    a(text(676, 106, "dirección I2C 0x40 · 50 Hz · 16 canales", "ts"))
    wires = [("vcc", "VCC", "pin 1 · 3,3 V", 130), ("sda", "SDA", "pin 3", 152), ("scl", "SCL", "pin 5", 174), ("gnd", "GND", "pin 6", 196)]
    for cls, name, pin, y in wires:
        a(f'<line x1="570" y1="{y}" x2="660" y2="{y}" class="{cls}" stroke-width="3"/>')
        a(text(676, y + 5, f"{name}", "tm"))
        a(text(716, y + 5, f"← {pin}", "ts"))
    a(text(820, 196 + 5, "OE y V+ lateral: libres", "ts"))
    # clema de potencia
    a('<rect x="840" y="222" width="110" height="24" rx="3" class="box"/>')
    a(text(895, 239, "clema V+ | GND", "ts", "middle"))

    a('<rect class="psu" x="660" y="320" width="310" height="92" rx="8"/>')
    a(text(676, 346, "Fuente de los servos", "tb"))
    a(text(676, 368, "ahora 5 A → límite activado en la web", "t"))
    a(text(676, 390, "para de pie/andar: 6 V · 15-20 A, cables 1,5 mm²", "ts"))
    a('<line x1="868" y1="320" x2="868" y2="248" class="vplus" stroke-width="5"/>')
    a('<line x1="922" y1="320" x2="922" y2="248" class="gnd" stroke-width="5"/>')
    a(text(856, 290, "V+", "tm", "end"))
    a(text(934, 290, "GND", "tm"))
    # condensador
    a('<line x1="868" y1="270" x2="884" y2="270" class="vplus" stroke-width="1.5"/><line x1="906" y1="270" x2="922" y2="270" class="gnd" stroke-width="1.5"/>')
    a('<line x1="884" y1="262" x2="884" y2="278" class="gnd" stroke-width="2"/><line x1="906" y1="262" x2="906" y2="278" class="gnd" stroke-width="2"/>')
    a(text(895, 308, "1000 µF", "ts", "middle"))

    a('<rect class="warnbox" x="30" y="278" width="600" height="72" rx="8"/>')
    a(text(46, 302, "El GND de la fuente de los servos va SIEMPRE a la clema, bien apretado.", "tb"))
    a(text(46, 322, "Si hace mal contacto, la corriente de los servos vuelve por la Pi (pin 6) y el", "t"))
    a(text(46, 340, "cable micro USB, y lo quema. Pasó el 11-10-2026 a las 14:11.", "t"))
    a('<rect class="dangerbox" x="30" y="362" width="600" height="50" rx="8"/>')
    a(text(46, 384, "Nunca: el cable Schuko en la clema · la Pi desde el USB del ordenador ·", "tb"))
    a(text(46, 402, "los servos desde la Pi · tocar conectores con todo encendido.", "tb"))

    # ---------- 2. canales de la PCA9685 ----------
    a(text(30, 466, "CANALES DE LA PCA9685 (lo que hay conectado ahora)", "th"))
    a(text(30, 488, "Cada conector, de dentro hacia el borde: señal (naranja) · V+ (rojo) · GND (marrón/negro).", "ts"))
    x0, cw = 40, 58
    for ch in range(16):
        x = x0 + ch * cw
        lab, art, st, note = CH[ch]
        a(text(x + 24, 516, str(ch), "tm", "middle"))
        a(f'<rect x="{x + 4}" y="524" width="40" height="52" rx="4" class="box"/>')
        for i, cls in enumerate(("sig", "pv", "pg")):
            a(f'<rect x="{x + 17}" y="{530 + i * 15}" width="14" height="11" rx="2" class="{cls}"/>')
        a(f'<rect x="{x + 1}" y="584" width="46" height="{58 if note else 44}" rx="5" class="{st}" stroke-width="1.2"/>')
        a(text(x + 24, 603, lab, f"tm {st}t", "middle"))
        a(text(x + 24, 620, art, f"ts {st}t", "middle"))
        if note:
            a(text(x + 24, 636, note, f"ts {st}t", "middle"))
    legend = [("ok", "bien (traseras C y D)"), ("check", "funciona, por revisar (delanteras)"), ("warn", "girado"),
              ("bad", "no responde"), ("free", "libre")]
    lx = 40
    for st, txt in legend:
        a(f'<rect x="{lx}" y="664" width="16" height="16" rx="3" class="{st}" stroke-width="1.2"/>')
        a(text(lx + 22, 677, txt, "ts"))
        lx += 22 + len(txt) * 6.4 + 26

    # ---------- 3. robot visto desde arriba ----------
    a(text(30, 728, "EL ROBOT VISTO DESDE ARRIBA (cabeza delante)", "th"))
    a('<rect class="body" x="390" y="800" width="220" height="360" rx="18"/>')
    a(text(500, 975, "cuerpo", "ts", "middle"))
    a('<line x1="500" y1="796" x2="500" y2="758" class="arrow" stroke-width="2"/>')
    a('<path d="M490 768 L500 754 L510 768" class="arrow" stroke-width="2"/>')
    a(text(518, 770, "cabeza", "ts"))
    for leg, (title, y, sx, joints) in LEGS.items():
        base = 390 if sx < 0 else 610
        xs = [base + sx * (34 + i * 82) for i in range(3)]
        a(f'<line x1="{base}" y1="{y}" x2="{xs[-1]}" y2="{y}" class="limb" stroke-width="10" stroke-linecap="round"/>')
        tx = xs[-1] if sx < 0 else xs[0] - 24
        a(text(min(xs) - 24, y - 40, title, "tb"))
        for (art, ch), x in zip(joints, xs):
            st = CH[ch][2]
            a(f'<circle cx="{x}" cy="{y}" r="24" class="{st}" stroke-width="2"/>')
            a(text(x, y + 5, str(ch), f"tm {st}t", "middle"))
            a(text(x, y + 42, art, "ts", "middle"))
        for i, (st, line) in enumerate(LEG_NOTE[leg]):
            a(text(min(xs) - 24, y + 66 + i * 17, line, f"ts {st}t"))

    a('<rect class="warnbox" x="30" y="1196" width="940" height="76" rx="8"/>')
    a(text(46, 1220, "Delanteras: el cableado sigue otro orden que las traseras (A 4·2·0 y B 5·3·6 frente a C 10·12·14 y D 11·13·15).", "t"))
    a(text(46, 1240, "Puede haber algún cable cruzado: compruébalo con Probar, canal a canal. Para el codo de A, pasa su conector al canal 7:", "t"))
    a(text(46, 1258, "si así se mueve, fallaba el canal 0; si no, falla el servo o su cable. Si mueves conectores, hazlo sin corriente.", "t"))
    return "\n".join(p)


body = build()
svg_attrs = f'viewBox="0 0 {W} {H}" role="img" aria-labelledby="esq-t esq-d"'
title = '<title id="esq-t">Esquema de conexiones del SpotMicro</title><desc id="esq-d">Raspberry Pi, placa PCA9685, fuentes de alimentación y los 12 servos con su canal y su estado.</desc>'
OUT_SVG.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {svg_attrs} width="{W}" height="{H}">{title}<style>{LIGHT_CSS}</style>\n{body}\n</svg>\n', encoding="utf-8")
OUT_INLINE.write_text(f'<svg class="esq" {svg_attrs}>{title}\n{body}\n</svg>\n', encoding="utf-8")
print("ok", OUT_SVG, OUT_INLINE)
