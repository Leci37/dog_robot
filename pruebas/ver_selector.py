#!/usr/bin/env python3
"""Como ver_panel.py, pero abre el selector de rutinas por código (tecla 0) y escribe un código, para revisarlo
sin la tele. Solo pulsa teclas que NO mueven servos: 0 (abre la lista) y dígitos (filtra); nunca Intro.
Uso: ~/robot/bin/python pruebas/ver_selector.py [ruta del panel] [código a escribir]"""
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

import pyte

panel = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/spotmicro/pantalla/panel.py")
code = sys.argv[2] if len(sys.argv) > 2 else ""
COLS, ROWS = 128, 36
pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    os.environ.update(TERM="xterm-256color", LANG="C.UTF-8")
    os.execv("/usr/bin/python3", ["python3", panel])
screen = pyte.Screen(COLS, ROWS)
stream = pyte.ByteStream(screen)
shots, keys = [], [(4, b"0")] + [(5 + i * 0.3, d.encode()) for i, d in enumerate(code)]
start = time.time()
while time.time() - start < 8:
    r, _, _ = select.select([fd], [], [], 0.2)
    if r:
        try:
            stream.feed(os.read(fd, 65536))
        except OSError:
            break
    while keys and time.time() - start > keys[0][0]:
        os.write(fd, keys.pop(0)[1])
    if not shots and time.time() - start > 3.5:  # el panel antes de pulsar nada
        shots.append("\n".join(line.rstrip() for line in screen.display))
os.kill(pid, 15)
print(shots[0] if shots else "")
print("=" * COLS)
print("\n".join(line.rstrip() for line in screen.display))
