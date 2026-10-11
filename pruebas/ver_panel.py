#!/usr/bin/env python3
"""Ejecuta pantalla/panel.py en una terminal virtual de 128x36 (como la tele con letra 10x20) y la
imprime como texto, para revisar el panel sin mirar la tele. Necesita `pyte` (pip install pyte).
Uso: ~/robot/bin/python pruebas/ver_panel.py [segundos] [ruta del panel]
No pulsa teclas que muevan servos (solo flecha abajo para probar la selección)."""
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

import pyte

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8
panel = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/spotmicro/pantalla/panel.py")
COLS, ROWS = 128, 36
pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    os.environ.update(TERM="xterm-256color", LANG="C.UTF-8")
    os.execv("/usr/bin/python3", ["python3", panel])
screen = pyte.Screen(COLS, ROWS)
stream = pyte.ByteStream(screen)
end, sent = time.time() + secs, False
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.2)
    if r:
        try:
            stream.feed(os.read(fd, 65536))
        except OSError:
            break
    if not sent and time.time() > end - secs / 2:
        os.write(fd, b"\x1b[B")  # flecha abajo: cambia la salida elegida
        sent = True
os.kill(pid, 15)
print("\n".join(line.rstrip() for line in screen.display))
