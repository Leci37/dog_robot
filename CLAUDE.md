# SpotMicro: contexto para cualquier IA o persona que trabaje en esta Pi

Robot cuadrúpedo impreso en 3D (clon de SpotMicro), heredado a medio montar. Esta carpeta (`~/spotmicro`) es el
proyecto. **Lee esto entero antes de mover nada** y añade al final del registro lo que hagas.

## Hardware
- **Raspberry Pi 2 Model B v1.1** (armv7l, 1 GB de RAM), sin WiFi ni Bluetooth. Alimentación por micro USB a 5 V.
  `vcgencmd get_throttled` debe dar `0x0`; `0x50000` = hubo caída de tensión desde el arranque (suele pasar
  2 s al arrancar con el cable actual).
- **PCA9685** (driver PWM de 16 canales) en I2C bus 1, **dirección 0x40** (0x70 = all-call del mismo chip).
  Cableado: VCC→pin 1 (3,3 V), SDA→pin 3, SCL→pin 5, GND→pin 6. OE y V+ del header lateral, libres.
  Si `i2cdetect -y 1` muestra **todas** las direcciones a la vez → mal contacto (casi siempre GND), no un chip roto.
- **12 servos** (probablemente MG996R), 3 por pata. Mapa (ver `servo_map.json`):

  | Pata | 1 hombro | 2 rodilla | 3 tobillo |
  |---|---|---|---|
  | A delantera izq | 4 | 2 | 0 (**no responde**: la PCA saca pulso, falla servo/cable) |
  | B delantera der | 5 | 3 | 1 |
  | C trasera der | 10 | 12 | 14 |
  | D trasera izq | 11 | 13 | 15 |

  Canales 6-9 libres. Patrón: delante bajando de 2 en 2 (B 5-3-1, A 4-2-0), detrás subiendo (C 10-12-14, D 11-13-15).
- En bolsa, sin montar: LCD 1602A (sin I2C), HC-SR04, HC-05, una PCA9685 de repuesto.

## Seguridad (no negociable)
- La alimentación de los servos va a la clema **V+/GND** de la PCA9685, con una fuente de **5–6 V y 10 A o más**
  (o LiPo 2S + reductor), con GND común con la Pi. **Nunca** desde la Pi.
- Había un cable con enchufe **Schuko** (230 V) conectado a la clema V+. **No enchufarlo jamás.**
- Probar servos **de uno en uno, con el robot en el aire**, movimientos pequeños (±10°). El primer pulso a un
  servo es absoluto: salta desde donde esté hasta el ángulo pedido.
- No desenchufar cables con la Pi encendida (ya ha habido cortes bruscos con escritura en la SD).
  Apagar con `sudo poweroff`.

## Software
- Raspberry Pi OS Lite 32-bit (Debian 13 Trixie), usuario `luislecinana`, sudo sin contraseña.
- I2C activado; HDMI forzado a 1280x720 (copias en `/boot/firmware/*.bak`).
- Entorno Python: `~/robot` (venv con `--system-site-packages`) con `adafruit-circuitpython-servokit`.
  Ejecutar con `~/robot/bin/python`.
- Journal en RAM (`Storage=volatile`): los logs se pierden al reiniciar.

## Ficheros de esta carpeta
- `CLAUDE.md` / `AGENTS.md`: este documento.
- `servo_test.py`: mueve **un** canal ±delta grados alrededor de un centro y lo suelta.
  `./servo_test.py 0` · `./servo_test.py 0 --delta 5` · `./servo_test.py --off` (suelta todos).
- `servo_map.json`: mapa canal → pata y articulación, en `"canales": {"<canal>": {"pata": "B", "articulacion":
  "rodilla"} | {"libre": true}}`. Lo escribe la web.
  **Nombres** (vista desde arriba, cabeza delante): **A** delantera izq, **B** delantera der, **C** trasera der,
  **D** trasera izq. Articulación (nombres del usuario, del cuerpo al suelo): **1** hombro (abre/cierra al lado),
  **2** rodilla (pata adelante/atrás), **3** tobillo (dobla la parte de abajo). Ej.: B3 = tobillo delantera derecha.
- `web/`: **web de mapeo** en `http://192.168.8.104:8080`, servicio systemd `spotmicro-web` (arranca solo).
  `server.py` (stdlib + servokit, límites 30–150°, ±20° máx.) e `index.html`. Por canal: Probar (90° → ±delta
  → 90°, sujeto o suelto), Soltar, ajuste ±1/±5°. Tras probar, pregunta pata A-D / "no se ha movido" y
  articulación. Botón "Soltar todo" = parada. Log de acciones en `web/acciones.log` (para seguir al usuario).
  Mientras la web corre, no uses `servo_test.py` a la vez (no comparten el estado de los ángulos).
  Sección **Patas**: deslizador por articulación; posturas "recogida"/"estirada" se **enseñan** (el usuario coloca
  la pata y guarda) en `poses.json`; Recoger/Estirar mueven las 3 articulaciones interpoladas a la vez.
  "Todas las patas a la vez": `move_many` mueve todos los servos juntos 1° por paso alternando patas
  (A1 B1 C1 D1 A2…); grupos (todos los hombros/rodillas/tobillos) con espejo 180−a en las derechas (B, C);
  "Bloquear todo a mitad" = todos a 90° sujetos; se pueden guardar posturas de las 4 patas de una vez.
  Secuencias: recorre hombros/rodillas/tobillos/todo con cartel y voz (una tras otra o a la vez). Se enseña porque la dirección de cada servo es desconocida (izq/der en espejo, horns distintos).

## Acceso
- Red: cable al router 4G Huawei (192.168.8.1). La Pi suele estar en **192.168.8.104** (DHCP).
- Desde el PC de sobremesa de Windows: `ssh spotmicro` (clave `~/.ssh/id_spotmicro`, alias en `~/.ssh/config`).

## Registro (añadir al final, con fecha)
- 2026-10-06: SD grabada (Imager). Reinicios en bucle por cargador insuficiente.
- 2026-10-11: SD recolocada, cargador nuevo; SSH por clave; sudo sin contraseña; I2C y HDMI activados;
  sistema actualizado. La PCA9685 daba ACK falsos en todas las direcciones por mal contacto; tras recolocar
  los cables responde en 0x40 (MODE1=0x11, PRESCALE=0x1E, valores de fábrica).
- 2026-10-11: creado `~/robot` con servokit; creada esta carpeta con `servo_test.py` y `servo_map.json`.
  Pendiente: fuente de servos y mapeo de canales.
- 2026-10-11: sin fuente en V+ todavía (el usuario no sabe qué hay en la clema). Robot dentro de su caja.
- 2026-10-11: fuente de servos conectada, robot fuera de la caja. Barrido de los 16 canales: se movieron servos.
  Creada la web de mapeo (`web/`, servicio `spotmicro-web`). Siguiente: mapear los 12 servos con la web,
  luego igualar las 4 patas con ajustes por servo (offsets) como postura de referencia.
- 2026-10-11: el usuario usa hombro/rodilla/tobillo; renombrado por número (antes hombro/muslo/rodilla).
  Canal 4: al probarlo con el canal 3 (B1) sujeto, el usuario vio moverse los hombros de A y B → probablemente
  A1 + respingo del servo sujeto; pendiente repetir con todo suelto.
- 2026-10-11: mapa completo por patrón de cableado (canal 1 = B3, canal 0 = A3 sin respuesta). Canales 6-9 libres.
  Web: amplitud hasta 45°, centro, velocidad, ±15, deslizador, postura 90, sección de patas con posturas.
- 2026-10-11: "Recoger todas" no funcionaba porque no había posturas guardadas (no existía poses.json) y además
  iba pata a pata. Rehecho: todo a la vez alternando, grupos con espejo, bloquear a mitad, guardar para todas.
