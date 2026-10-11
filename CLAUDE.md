# LUIS LECI control (SpotMicro): contexto para cualquier IA o persona que trabaje en este proyecto

Robot cuadrúpedo impreso en 3D (clon de SpotMicro) con una Raspberry Pi 2, heredado a medio montar. La carpeta
`~/spotmicro` de la Pi es el proyecto (repo git; copia para GitHub en el PC). **Lee esto entero antes de mover
nada** y añade al final del registro lo que hagas. Guía corta para el usuario: `AYUDA.md` (también en la web,
en el HDMI con la tecla H y en `/ayuda`). Credenciales y datos personales: `NOTAS-LOCALES.md` (solo en la Pi,
en `.gitignore`, **nunca** subirlo).

## Nombres (los del usuario)
- Patas, vistas desde arriba con la cabeza delante: **A** delantera izq · **B** delantera der · **C** trasera der ·
  **D** trasera izq.
- Articulaciones, del cuerpo al suelo: **1 pectoral** (abre/cierra la pata al lado) · **2 hombro** (adelante/atrás) ·
  **3 codo** (dobla la parte de abajo). Ej.: B3 = codo de B. (Antes se llamaron hombro/rodilla/tobillo y, antes,
  hombro/muslo/rodilla: `sistema/migrar_nombres.py` convierte por número; `servo_map.json` lleva `"nomenclatura": 2`.)

## Hardware
- **Raspberry Pi 2 Model B v1.1** (armv7l, 1 GB), sin WiFi ni Bluetooth, micro USB 5 V. `vcgencmd get_throttled`
  debe dar `0x0`. LEDs: `ACT` (verde, lo controla `spotmicro-led`), `PWR` (rojo).
- **PCA9685** en I2C bus 1, **0x40**. Cableado: VCC→pin 1, SDA→pin 3, SCL→pin 5, GND→pin 6. Si `i2cdetect -y 1`
  muestra todas las direcciones → mal contacto (casi siempre GND).
- **12 servos** (MG996R probablemente). Mapa (`servo_map.json`):

  | Pata | 1 pectoral | 2 hombro | 3 codo |
  |---|---|---|---|
  | A delantera izq | 4 | 2 | 0 (**no responde**: la PCA saca pulso; falla el servo o su cable) |
  | B delantera der | 5 | 3 | **6** (conector movido desde el 1; a 90° quedaba "súper estirado": horn girado) |
  | C trasera der | 10 | 12 | 14 |
  | D trasera izq | 11 | 13 | 15 |

  Canales libres: 1, 7, 8, 9. Las traseras a 90° quedan perfectas y coordinadas.
- En bolsa: LCD 1602A (sin I2C), HC-SR04, HC-05, una PCA9685 de repuesto.

## Seguridad (no negociable)
- Servos alimentados por la clema **V+/GND** de la PCA9685 con fuente de **5–6 V y ≥10 A**, GND común. Nunca desde
  la Pi. Había un cable **Schuko** (230 V) en la clema V+: **no enchufarlo jamás**.
- Probar **con el robot en el aire**. Primer pulso a un servo suelto = salto absoluto. Límites lógicos 30–150°.
- No cambiar conectores con todo encendido (el 2026-10-11 la Pi se quedó apagada horas tras hacerlo).
- Apagar con el botón "Apagar la Pi" (web/tecla A) o `sudo poweroff`; nunca desenchufar en caliente.

## Servicios (systemd)
- **`spotmicro-web`**: `web/server.py` (stdlib + servokit), puertos **80 y 8080** (`CAP_NET_BIND_SERVICE`).
- **`spotmicro-pantalla`**: `pantalla/panel.py` en **tty1** (sustituye a `getty@tty1`, deshabilitado;
  `Conflicts=getty@tty1`), con sesión logind (PAM `/etc/pam.d/spotmicro-pantalla`) para lanzar el navegador.
- **`spotmicro-led`** (root): `sistema/led_vivo.py`. LED verde **corto-corto-largo** = web viva; parpadeo rápido =
  web caída; al pararse devuelve el LED al disparador `mmc0`.
- Journal **persistente** (32 MB, `/etc/systemd/journald.conf.d/90-spotmicro.conf`). Watchdog hardware de serie (1 min).
- Navegador kiosco: `cage` + `chromium` con `sistema/instala-navegador.sh` (reanudable; unidad
  `spotmicro-instala-navegador`; botón en la web). El 2026-10-11 la descarga iba muy lenta (4G a ~20-40 KB/s).

## Red (router o cable directo al PC)
- Perfil NM **`spotmicro-eth0`** (prioridad 100): DHCP sin límite + **169.254.42.1/16** siempre + IPv6 link-local
  **EUI-64** `fe80::ba27:ebff:fe16:ec15`. Probado simulando que no hay router: sigue activo con 169.254.42.1.
  Con router suele tener 192.168.8.104. Sin servidor DHCP en la Pi a propósito.
- Cable directo con Windows: el PC se pone 169.254.x.x solo → `http://169.254.42.1` / `ssh luislecinana@169.254.42.1`.
- Último recurso: `ssh -L 8080:localhost:8080 luislecinana@fe80::ba27:ebff:fe16:ec15%<ifIndex>` → `http://localhost:8080`.
- SSH: claves del PC de sobremesa y del portátil en `~/.ssh/authorized_keys`; contraseña en `NOTAS-LOCALES.md`.

## La web (`http://<ip>`) — "LUIS LECI control"
- Cabecera: alimentación, red, rutina en marcha (con Parar), Soltar todo y la **pestaña "estado" desplegable**
  (arriba a la derecha): desde dónde entras (router / cable directo / la propia Pi), qué dirección usas, IPs,
  credenciales (sin mostrar la contraseña), rutas (URLs, SSH, API, carpeta) y estado de la Pi.
- **Sistema**: conexión, alimentación + `energia.log`, pantalla HDMI (panel/web/consola, opción de arranque,
  instalar navegador), Apagar/Reiniciar, **Informe para la mudanza** (`/api/informe`) y Ayuda.
- **Límite de consumo** (barra arriba): `config.json` → `potencia {limitar, amperios}` (por defecto 5 A).
  `move_many` reparte en tandas: k = (A − sujetos·0,3) / (1,2 − 0,3), mínimo 1 (I_MOVE 1,2 A, I_HOLD 0,3 A).
  Con 5 A → 1 servo moviéndose a la vez; con 20 A → los 12. POST `/api/power`. Lo respetan grupos, rutinas y bloques.
- Grupos (todos los pectorales/hombros/codos, espejo 180−a en B y C), Bloquear todo a mitad, Recoger/Estirar.
- **Rutinas para probar** (`candidate_routines` en server.py): etapa 1 = 8 variantes de "De pie" (signo de hombro,
  de codo y delanteras igual/invertidas). Al marcar ✓ una, se guarda la convención en `config.json` y las posturas
  "de pie"/"estirada"/"recogida" en `poses.json`; etapa 2 = 15 rutinas (de pie alto/bajo, tumbarse, levantarse,
  sentarse, reverencia, flexiones, balanceo, saludar con B, marcar el paso, andar corto/largo/atrás, girar izq/der).
  Modelo: patas de longitud parecida → agachar `beta`: codo beta, hombro beta/2. Andar = "creep" (orden D A C B).
- **Mis rutinas** (`rutinas.json`): ejecutar, ver/editar en bloques, borrar. Teclas 1-9 en el HDMI.
- **Programar con bloques** (Blockly 13.3.0 servido desde `web/static/blockly`, funciona sin internet; renderer
  "zelos" tipo Scratch). Bloques: mover pata/articulación (ángulo de gesto = el de la pata D; el resto en espejo),
  mover relativo, pata entera, postura, todo a mitad, soltar, esperar, velocidad, repetir, ejecutar rutina,
  y avanzado "a la vez" + "servo (ángulo real)" (así se ven las rutinas de prueba). Cualquier rutina → bloques.
- Patas, Canales (probar, mapear, calibrar: Fijar aquí = 90°, Invertir, Quitar ajuste), Secuencias con voz.

## Lenguaje de rutinas (JSON, `run_steps` en server.py; se ejecuta en un hilo, se para con /api/routine_stop)
`articulacion` {pata|todas, art, angulo} · `articulacion_rel` {pata, art, delta} · `pata` {pata, pectoral, hombro,
codo} · `set` {objetivos:[{pata, art, angulo}]} (ángulos lógicos reales) · `postura` {nombre} · `mitad` · `soltar`
{pata?} · `esperar` {s} · `velocidad` {v: lento|normal|rapido} · `repetir` {n, pasos} · `rutina` {nombre}.
Con una rutina en marcha la web rechaza los movimientos manuales (salvo Soltar todo, que la para).

## Pantalla HDMI
- Menú 15 s al arrancar: [1] panel · [2] página web (kiosco, `?pantalla=1`) · [3] consola. Panel: URLs, alimentación,
  servos, rutina en marcha, rutinas 1-9, teclas S/M/R/E, X parar, W web, C consola en tty1, **T consola aparte
  (tty2, Alt+F1 vuelve)**, H ayuda, A/B apagar/reiniciar. Ctrl+Alt+F2 = login aparte en cualquier momento.

## Ficheros
- `web/` (server.py, index.html, static/blockly, unidad), `pantalla/` (panel.py, navegador.sh, unidad, PAM),
  `sistema/` (led_vivo.py + unidad, instala-navegador.sh, migrar_nombres.py, red_prueba.sh), `pruebas/`
  (simulador de servos `fake/adafruit_servokit.py` + `prueba_servidor.py`: ~45 comprobaciones sin tocar la placa).
- Datos: `servo_map.json`, `poses.json`, `config.json` (convención, pruebas y `potencia` {limitar, amperios}),
  `rutinas.json`. Registros (no van a git): `energia.log`, `vigilante.log`, `web/acciones.log`,
  `pantalla/{modo,estado,menu_visto}`.
- **Esquema de conexiones**: `web/static/esquema.svg` (al final de la web y en `/static/esquema.svg`), generado con
  `sistema/gen_esquema.py` (mapa de canales y avisos dentro del script: actualízalo si cambia el cableado).
- **Límite de consumo** (`move_many` en server.py): servo moviéndose ~1,2 A, sujetando ~0,3 A; con el límite activado
  mueve en tandas de `max_movers()` servos para no pasar de los amperios de la fuente (5 A → de uno en uno).
- `sistema/vigilante.sh` + `spotmicro-vigilante.timer` (cada minuto): repone 169.254.42.1/perfil de red, reinicia la
  web si no responde, pone la consola en tty1 si el panel falla, arranca el LED; registro en `vigilante.log`.
- Probar cambios del servidor sin tocar servos (en la Pi, desde una copia):
  `FAKE_LOG=/tmp/fake.log SPOTMICRO_PORTS=8099 SPOTMICRO_FAST=1 PYTHONPATH=pruebas/fake python3 web/server.py &`
  y `python3 pruebas/prueba_servidor.py`.

## Registro (añadir al final, con fecha)
- 2026-10-06: SD grabada (Imager). Reinicios en bucle por cargador insuficiente.
- 2026-10-11: SD recolocada, cargador nuevo; SSH por clave; sudo sin contraseña; I2C y HDMI; sistema actualizado.
  PCA9685 con ACK falsos por mal contacto; recolocada responde en 0x40.
- 2026-10-11: fuente de servos; web de mapeo; mapa completo; secuencias; patas; todo a la vez; grupos; calibración.
- 2026-10-11: a 90° traseras perfectas; codo A (canal 0) no responde; codo B "súper estirado" → movido al canal 6.
- 2026-10-11 ~04:34: la Pi se apagó al tocar conectores; volvió al desenchufar/enchufar (12:57).
- 2026-10-11 13:00: preparación para casa sin router y con cortes: red 169.254.42.1 + IPv6 fija, web en :80,
  energia.log, journal persistente, Apagar/Reiniciar, pantalla HDMI con menú, panel y kiosco, AYUDA.md.
- 2026-10-11 ~13:26: segundo apagado de golpe (durante una descarga de apt); energia.log lo registró. Sospecha:
  alimentación justa (o SD de 2015). Descarga de chromium muy lenta por el 4G: queda reanudable.
- 2026-10-11 14:00: nombres pectoral/hombro/codo; app "LUIS LECI control"; motor de rutinas + rutinas para probar;
  bloques Blockly; informe para la mudanza; pestaña de estado; LED corto-corto-largo; consola aparte en el HDMI;
  pruebas con simulador (todo bien); credenciales fuera de los documentos; copia para GitHub en el PC.
- 2026-10-11 14:11: tercer apagado de golpe, en el mismo segundo en que se movía el canal 4. Desde las 14:05 los servos no se movían aunque la PCA sacaba bien el PWM → sospecha: GND de la fuente de servos con mal contacto en la clema; la corriente de los servos volvía por la Pi (pin 6) y el cable micro USB, que se quemó (el usuario lo cambió y la Pi revivió). Tras cambiar el cable seguía con falta de corriente (throttled=0x50005 continuo): revisar cargador/cable de la Pi y el GND de la clema ANTES de mover servos. Fuente de servos actual: 5 A (insuficiente para los 12 de pie). Añadido el vigilante (spotmicro-vigilante.timer).
- 2026-10-11 15:30: límite de consumo de los servos (barra arriba, 5 A por defecto: movimientos en tandas); instalación del navegador relanzada (4G a ~470 KB/s).
- 2026-10-11 15:40: esquema de conexiones (web/static/esquema.svg, al final de la web, en la documentación y como
  artefacto: https://claude.ai/artifact/T9jZtMM2dSXeoykRx2TbbN). Avisos: codo A (canal 0) no responde, codo B
  (canal 6) girado, delanteras con otro orden de cables (posible cable cruzado), traseras C y D bien.
