# LUIS LECI control

Control web para un robot cuadrúpedo **SpotMicro** (impreso en 3D) con **Raspberry Pi 2** y una placa de servos
**PCA9685**. Pensado para funcionar sin internet, con o sin router, con pantalla HDMI y con cortes de corriente.

- **Web** (`http://<ip-de-la-pi>`): probar y mapear los 12 servos, calibrarlos, mover grupos y patas, posturas,
  rutinas para probar (de pie, andar, girar, sentarse…), **programación por bloques tipo Scratch** (Blockly,
  incluido en el repo para que funcione sin internet), informe de estado y una pestaña con cómo estás conectado.
- **Pantalla HDMI**: menú al arrancar (panel de texto, la web en kiosco o la consola normal) y panel con teclas.
- **Red**: con router por DHCP y, siempre, `169.254.42.1` para cable directo a un PC (sin configurar nada) e IPv6
  link-local fija.
- **Cortes de corriente**: registro permanente de arranques y caídas de tensión (`energia.log`), escrituras atómicas,
  journal persistente, watchdog, botón de apagado limpio, y el **LED verde** haciendo corto-corto-largo mientras
  el programa está vivo.

## Estructura
| Carpeta / fichero | Qué es |
|---|---|
| `web/server.py`, `web/index.html` | Servidor (Python estándar + `adafruit-circuitpython-servokit`) y página |
| `web/static/blockly/` | Blockly 13.3.0 (Apache 2.0, ver su `LICENSE`) |
| `pantalla/` | Panel de la pantalla HDMI (curses), lanzador del navegador kiosco, unidad systemd y PAM |
| `sistema/` | LED de vida, instalador del navegador, migración de nombres, prueba de red |
| `pruebas/` | Simulador de la placa de servos y pruebas del servidor (no mueven nada) |
| `servo_map.json`, `poses.json`, `config.json`, `rutinas.json` | Datos de este robot (mapa, posturas, sentido, rutinas) |
| `AYUDA.md` | Guía de uso (conexión, pantalla, alimentación, rutinas, fallos) |
| `CLAUDE.md` / `AGENTS.md` | Contexto técnico completo para quien siga el proyecto (personas o IA) |

## Instalar en una Raspberry Pi (Raspberry Pi OS Lite 32-bit, Trixie)
1. Copia el repo en `~/spotmicro`, activa I2C (`sudo raspi-config nonint do_i2c 0`) y crea el entorno:
   `python3 -m venv --system-site-packages ~/robot && ~/robot/bin/pip install adafruit-circuitpython-servokit`.
2. Servicios: copia `web/spotmicro-web.service`, `pantalla/spotmicro-pantalla.service` y
   `sistema/spotmicro-led.service` a `/etc/systemd/system/`, `pantalla/pam-spotmicro-pantalla` a
   `/etc/pam.d/spotmicro-pantalla`, y `sudo systemctl disable getty@tty1 && sudo systemctl enable --now
   spotmicro-web spotmicro-pantalla spotmicro-led`. (Las rutas asumen el usuario `luislecinana`: cámbialas si no.)
3. Red: ver `sistema/red_prueba.sh` (crea el perfil `spotmicro-eth0` de NetworkManager).
4. Opcional, pantalla HDMI con la web: `sudo sistema/instala-navegador.sh`.

## Probar sin robot
```
FAKE_LOG=/tmp/fake.log SPOTMICRO_PORTS=8099 SPOTMICRO_FAST=1 PYTHONPATH=pruebas/fake python3 web/server.py &
python3 pruebas/prueba_servidor.py
```

## Seguridad
La web no tiene contraseña: úsala solo en tu red local o por cable directo. Los servos van con fuente propia
(5–6 V, ≥10 A) en la clema V+/GND de la PCA9685, nunca desde la Pi. Prueba siempre con el robot en el aire.
