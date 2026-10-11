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
- **12 servos** (probablemente MG996R), 3 por pata. Asignación de canales: ver `servo_map.json`.
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
- `servo_map.json`: canal por pata y articulación. Patas: **A** delantera izquierda, **B** delantera derecha,
  asignan al final (el usuario aún no lo ha decidido). Articulaciones: hombro, muslo, rodilla.

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
