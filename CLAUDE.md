# SpotMicro: contexto para cualquier IA o persona que trabaje en esta Pi

Robot cuadrúpedo impreso en 3D (clon de SpotMicro), heredado a medio montar. Esta carpeta (`~/spotmicro`, repo git)
es el proyecto. **Lee esto entero antes de mover nada** y añade al final del registro lo que hagas.
Guía corta para el usuario (conexión, pantalla, cortes, fallos): `AYUDA.md` (también en la web y en el HDMI).

## Hardware
- **Raspberry Pi 2 Model B v1.1** (armv7l, 1 GB de RAM), sin WiFi ni Bluetooth. Alimentación por micro USB a 5 V.
  `vcgencmd get_throttled` debe dar `0x0`; `0x50000` = hubo caída de tensión desde el arranque.
- **PCA9685** (driver PWM de 16 canales) en I2C bus 1, **dirección 0x40** (0x70 = all-call del mismo chip).
  Cableado: VCC→pin 1 (3,3 V), SDA→pin 3, SCL→pin 5, GND→pin 6. OE y V+ del header lateral, libres.
  Si `i2cdetect -y 1` muestra **todas** las direcciones a la vez → mal contacto (casi siempre GND), no un chip roto.
- **12 servos** (probablemente MG996R), 3 por pata. Mapa (ver `servo_map.json`):

  | Pata | 1 hombro | 2 rodilla | 3 tobillo |
  |---|---|---|---|
  | A delantera izq | 4 | 2 | 0 (**no responde**: la PCA saca pulso, falla servo/cable) |
  | B delantera der | 5 | 3 | **6** (el usuario movió el conector desde el 1; a 90° quedaba "súper estirado") |
  | C trasera der | 10 | 12 | 14 |
  | D trasera izq | 11 | 13 | 15 |

  Canales libres: 1, 7, 8, 9. Patas traseras: perfectas a 90° y bien coordinadas (dicho por el usuario).
- En bolsa, sin montar: LCD 1602A (sin I2C), HC-SR04, HC-05, una PCA9685 de repuesto.

## Seguridad (no negociable)
- La alimentación de los servos va a la clema **V+/GND** de la PCA9685, con una fuente de **5–6 V y 10 A o más**
  (o LiPo 2S + reductor), con GND común con la Pi. **Nunca** desde la Pi.
- Había un cable con enchufe **Schuko** (230 V) conectado a la clema V+. **No enchufarlo jamás.**
- Probar servos **con el robot en el aire**, movimientos pequeños. El primer pulso a un servo suelto es absoluto.
- No cambiar conectores con todo encendido: el 2026-10-11 la Pi se quedó apagada horas tras tocar conectores de
  servos (probablemente el cargador entró en protección). Apagar con el botón "Apagar" de la web o `sudo poweroff`.

## Software y servicios
- Raspberry Pi OS Lite 32-bit (Debian 13 Trixie), usuario `luislecinana`, sudo sin contraseña.
- I2C activado; HDMI forzado a 1280x720 (`hdmi_force_hotplug` + `video=HDMI-A-1:1280x720@60D`; copias `*.bak`).
- Python de los servos: venv `~/robot` (`--system-site-packages`) con `adafruit-circuitpython-servokit`.
- **Journal persistente** (`/etc/systemd/journald.conf.d/90-spotmicro.conf`, 32 MB, sync 30 s):
  `journalctl -b -1` = arranque anterior. **Watchdog hardware** activo (systemd, 1 min, viene de serie).
- Servicio **`spotmicro-web`** (`web/server.py`, puertos **80 y 8080**, `AmbientCapabilities=CAP_NET_BIND_SERVICE`).
- Servicio **`spotmicro-pantalla`** (`pantalla/panel.py` en **tty1**, sustituye a `getty@tty1`, que está deshabilitado;
  `Conflicts=getty@tty1`). Tiene sesión logind (PAM `/etc/pam.d/spotmicro-pantalla`) para poder lanzar el navegador.
- Navegador kiosco: `cage` + `chromium` (instalados con `--no-install-recommends`), lanzado por
  `pantalla/navegador.sh` con perfil en `/tmp` (RAM). Log: `/tmp/spotmicro-kiosko.log`.

## Red (para funcionar con router y con cable directo al PC)
- Perfil NetworkManager **`spotmicro-eth0`** (prioridad 100): DHCP sin límite de espera
  (`ipv4.dhcp-timeout=2147483647`, `may-fail yes`) **más** la dirección fija **169.254.42.1/16** siempre puesta, e
  IPv6 solo link-local con **EUI-64** → `fe80::ba27:ebff:fe16:ec15` (fija, derivada de la MAC B8:27:EB:16:EC:15).
  - Con router: IP por DHCP (suele ser 192.168.8.104) + 169.254.42.1.
  - Cable directo a un PC Windows sin router: Windows se autoasigna 169.254.x.x (APIPA) → `http://169.254.42.1`.
  - No hay servidor DHCP en la Pi a propósito (no puede estropear la red de casa ni robar la ruta de internet del PC).
- El perfil automático en memoria "Wired connection 1" de NM queda sin uso.
- Último recurso: `ssh -L 8080:localhost:8080 luislecinana@fe80::ba27:ebff:fe16:ec15%<ifIndex>` → `http://localhost:8080`.
- Claves SSH autorizadas: PC de sobremesa (`leci@sobremesa-spotmicro`, alias `ssh spotmicro` en ese PC) y portátil
  corporativo (`llecinana@ecna.es`). Contraseña = usuario (equipo de pruebas). mDNS: `luislecinana.local` (avahi).

## Ficheros de esta carpeta
- `CLAUDE.md` / `AGENTS.md`: este documento. `AYUDA.md`: guía para el usuario (servida en `/ayuda`).
- `servo_test.py`: mueve **un** canal ±delta y lo suelta (no usarlo con la web activa: no comparten estado).
- `servo_map.json`: `"canales": {"<canal>": {"pata", "articulacion", "offset"?, "invertir"?, "nota"?} | {"libre": true}}`.
  **Nombres** (vista desde arriba, cabeza delante): **A** delantera izq, **B** delantera der, **C** trasera der,
  **D** trasera izq. Articulación (del cuerpo al suelo): **1** hombro, **2** rodilla, **3** tobillo. Ej.: B3.
  `offset`/`invertir` = calibración por servo (ángulo lógico → físico: `(180−a si invertir) + offset`).
- `poses.json`: posturas enseñadas por pata (`recogida`, `estirada`) y `_candidatas` (marcas bien/mal).
- `energia.log`: **registro permanente** (fsync) de arranques ("terminó DE GOLPE" = corte/cuelgue), caídas de tensión
  (bits de `vcgencmd get_throttled` vigilados cada 2 s) y apagados limpios.
- `pantalla/`: `panel.py`, `navegador.sh`, unidad y PAM; ficheros de estado `modo` (pedido: panel|web|consola),
  `arranque` (opción por defecto del menú), `estado` (lo que se ve), `menu_visto` (boot_id).
- `web/`: `server.py`, `index.html`, `spotmicro-web.service`, `acciones.log` (cada acción del usuario: sirve para
  seguir lo que hace "mano a mano").

## La web (`http://<ip>` o `:8080`)
- **Sistema**: conexión (modo router/directo/sin cable, URLs, SSH), alimentación (estado, avisos, energia.log),
  pantalla HDMI (panel/web/consola y opción de arranque), Apagar/Reiniciar (sueltan servos antes), Ayuda.
- Amplitud/centro/velocidad; "Todas las patas a la vez" (`move_many`: 1° por servo y paso alternando patas);
  grupos hombros/rodillas/tobillos con espejo 180−a en B y C; Bloquear todo a mitad (90°);
  **Posturas candidatas** (5 bases × delanteras igual/invertidas, marcar bien/mal) para descubrir sentidos;
  Secuencias con cartel y voz; Patas (deslizadores, enseñar recogida/estirada); Canales (probar, mapear,
  calibrar: Fijar aquí = 90°, Invertir, Quitar ajuste).
- API: `/api/state`, `/api/system`, `/ayuda`, POST `/api/test|test_many|angle|pose90|group|pose_save|pose_go|
  leg_release|calibrate|candidate_go|candidate_mark|release|off|map|screen|screen_boot|system_action`.
- Al arrancar el servidor recupera de los registros de la PCA9685 los ángulos de los servos que siguen sujetos.

## Pantalla HDMI (`pantalla/panel.py`)
- Al arrancar la Pi: menú 15 s → [1] panel (por defecto) · [2] página web (cage+chromium, `?pantalla=1`) · [3] consola.
- Panel: URLs y SSH, alimentación, servos, teclas S/M/R/E (soltar, mitad, recoger, estirar), W web, C consola,
  H ayuda, A/B apagar/reiniciar (doble pulsación). Órdenes vía la API local.
- Consola: el panel arranca `getty@tty1` (que para el panel por Conflicts). Volver: botón "Panel" en la web.

## Registro (añadir al final, con fecha)
- 2026-10-06: SD grabada (Imager). Reinicios en bucle por cargador insuficiente.
- 2026-10-11: SD recolocada, cargador nuevo; SSH por clave; sudo sin contraseña; I2C y HDMI activados;
  sistema actualizado. La PCA9685 daba ACK falsos en todas las direcciones por mal contacto; tras recolocar
  los cables responde en 0x40 (MODE1=0x11, PRESCALE=0x1E, valores de fábrica).
- 2026-10-11: creado `~/robot` con servokit; creada esta carpeta con `servo_test.py` y `servo_map.json`.
- 2026-10-11: fuente de servos conectada. Web de mapeo; mapa completo por patrón de cableado; nombres
  hombro/rodilla/tobillo; secuencias; patas; todo a la vez; grupos; calibración por servo; posturas candidatas.
- 2026-10-11: a 90° las traseras quedan perfectas; delante: tobillo A (canal 0) no responde, tobillo B quedaba
  "súper estirado" (horn girado o al revés). El usuario movió el conector del tobillo B al canal 6.
- 2026-10-11 ~04:34: la Pi desapareció al tocar conectores con todo encendido; volvió al desenchufar y enchufar
  (12:57). El watchdog no la reinició → probablemente se quedó sin corriente, no colgada.
- 2026-10-11 13:00: preparación para una casa sin router y con cortes: red con 169.254.42.1 + IPv6 EUI-64,
  web en puerto 80, energia.log, journal persistente, botones Apagar/Reiniciar, pantalla HDMI con menú,
  panel y navegador kiosco, ayuda (`AYUDA.md`).
