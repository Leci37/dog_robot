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
  | A delantera izq | 4 | 2 | **1** (antes en el 0, que no respondía; ajuste −15 = 15° hacia delante) |
  | B delantera der | 5 | 3 | **6** (conector movido desde el 1; horn girado: ajuste +45 → en la de pie ya está en 175°, su tope) |
  | C trasera der | 10 | 12 | 14 |
  | D trasera izq | 11 | 13 | 15 |

  Canales libres: 0, 7, 8, 9. Las traseras a 90° quedan perfectas y coordinadas.
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
- **Bloquear todo donde está** (`/api/lock_all` {segundos 0,2-2, soltar}; `plan_lock_all` en server.py): sujeta cada
  servo en el ángulo apuntado (el que sujeta, si no la memoria `last_known`, si no 90°) y lo apunta. Todos a la vez si
  `max_movers` lo permite; si no, uno a uno pata a pata con pausa; si la fuente no da para sujetar los 12
  (12·0,3 + 0,9 > A) o `soltar`, bloquea-apunta-suelta (los que ya estaban sujetos no se sueltan). Es una rutina
  (paso interno `bloquear`, `lock_step`): se para con Parar/Soltar todo y bloquea los movimientos a mano mientras dura.
  Progreso en `GET /api/consumo` → `progreso` {running, nombre, paso, total, detalle, lista, fin, error}: barra en la
  cabecera (`#lockProg`, chips A1…D3). "Guardar postura" con sueltos pregunta y hace esto antes de guardar (`usar_ultima`).
- Pasos ±5/±15/±30 en canales y grupos. Franja de **apagados de golpe** arriba (`system_info()["apagones"]`:
  `outages()` lee `journalctl --list-boots` y cuenta "Undervoltage detected" por arranque, caché 60 s).
- **Versión ligera** `/lite` (`web/lite.html`, sin Blockly): la que abre el navegador de la tele (`pantalla/navegador.sh`).
- Mapa: al poner una pata/articulación en otro canal, el canal viejo queda `{"libre", "nota": "antes B3 (pasado al
  canal N)"}`, la calibración viaja si el canal nuevo no tenía, y se recarga `CAL` (`load_cal()` tras `save_map`).
- **Rutinas para probar** (`candidate_routines` en server.py): etapa 1 = la **variante 6** de "De pie" (`CONV6`: hombro −,
  codo +, delanteras invertidas; la ganadora de las 8 combinaciones) y sus variantes `VARIANTES_6` (6.1 baja, 6.2 alta,
  6.3/6.4 inclinadas, 6.5/6.6 pectorales ±10) + "Bajar despacio" (`ayuda`, sin marcas). Todas usan `stand_program`:
  pectorales → recoger las 4 patas a la vez (`TUCK` 52) → estirarlas a la vez a velocidad `suave` (0,1 s/°).
  Al marcar ✓ una, se guarda en `config.json` la convención con `variante`, `delanteras`, `traseras`, `pectoral`
  (solo una ✓ a la vez) y las posturas "de pie" (`base_stance`)/"estirada"/"recogida" en `poses.json`; etapa 2 =
  17 rutinas sobre esa de pie: primero las 5 de andar (`andar` paso a paso, `andar-balanceo`, `trote`, `andar-atras`,
  `paseo` = adelante/girar izq/adelante/girar der), girar izq/der, marcar el paso, y luego de pie alto/bajo, tumbarse,
  levantarse, sentarse, reverencia, flexiones, balanceo y saludar con B. Las de pie (etapa 1) siguen visibles debajo:
  marcar ✓ otra cambia la convención. `juntos_multi` mueve juntos los pasos de varias patas; las marchas van todas
  `juntos`. Modelo: patas de longitud parecida → agachar `beta`: codo beta, hombro beta/2. `walk_program` (creep,
  orden D A C B, al revés hacia atrás): el pie sube con hombro +lift/2 y codo +lift (sale vertical), avanza en el aire,
  baja, y el cuerpo avanza con las 4; `sway` aparta el cuerpo con los pectorales (supone pectoral + = abrir, sin
  confirmar). `trot_program`: diagonales A+C / B+D. `march_program`: marcar el paso.
- **Marchas de velocidad** (`MARCHAS`, config `marcha` 1-5: ×2 · ×1,4 · ×1 · ×0,6 · ×0,35 sobre la pausa por grado y
  las esperas de las rutinas). `POST /api/routine_speed {marcha}`, vale con la rutina en marcha (`gear_factor()` se lee
  en cada paso). En la web: botones en "Rutinas para probar" y un desplegable junto a "Parar rutina".
- **Paso `set` con `juntos`**: `move_many(..., juntos=True)` mueve todos los servos a la vez (1° cada uno por paso)
  aunque el límite de consumo diga tandas: en tandas, cada pata levantaba sola su esquina y su servo no podía.
- **Ajuste fino** `/api/calibrate` `{action: "nudge", delta: ±1-30}`: corre el `offset` del canal (máx. ±60) sin
  cambiar el ángulo lógico; si está sujeto se ve moverse. En la web, panel "Afinar patas" en "Rutinas para probar"
  (los 12 servos en vista desde arriba A B / D C, ±1/±5; `tuneOpen` recuerda si está abierto; `tryInfo` solo se repinta
  si cambia el HTML).
  Las variantes avisan si su postura final lleva un servo más allá de 5-175° físicos (`tope_warnings`).
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
- **P = de pie** (`/api/home`: para la rutina en marcha y lanza la de pie ✓) y **0 / Intro = rutinas por código**
  (`Panel.selector`: número + Intro o flechas; populares primero). "Probar salida" pasó de la P a la **U**.
  Vista del panel sin tele: `pruebas/ver_panel.py` y `pruebas/ver_selector.py` (abre el selector; nunca pulsa Intro).
- **Códigos** fijos `CODIGOS` en server.py (10/P de pie, 11-16 plata, 17 bajar, 18-25 de pie, 26-29 suaves, 30-38
  galopes, 40-46, 50-59 andar, 60-73, 80-89 curiosas, 90-95); las que no estén cogen 100+. `/api/routine_run
  {codigo}`; `/api/routines` da `codigo` en cada candidata, `home` y `populares` (plata + ≥3 estrellas).
  /lite: botón DE PIE (P), caja "Rutinas por código" (input + desplegable por familias + populares) y teclado.
- **Saltos de línea**: `.gitattributes` con `eol=lf`. Desde Windows (core.autocrlf=true) no copiar la copia de trabajo
  a la Pi tal cual: en 2026-10-12 se colaron CRLF en server.py, index.html, prueba_servidor.py, CLAUDE.md y AYUDA.md.

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
  y `python3 pruebas/prueba_servidor.py`. Si hay dos pruebas a la vez (dos sesiones), cada una en su puerto y su log:
  `SPOTMICRO_PORTS=8097 … FAKE_LOG=/tmp/fake8.log` y `BASE=http://127.0.0.1:8097 FAKE_LOG=/tmp/fake8.log python3 pruebas/prueba_servidor.py`.

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
- 2026-10-11 15:50: navegador kiosco instalado y probado (cage + chromium arrancan; Pi 2 sin GLES3 → `--disable-gpu`).
  Sección "El robot en vivo" al final de la web: vista desde arriba con componentes y canales + 4 patas de lado según
  lo que cree la Pi (ángulos de gesto, convención o la habitual si falta), arrastrables; "¿Está así de verdad?" →
  `/api/leg_check` (config.json `verificacion`); "X al revés" → calibrate invert del canal. Referencias del usuario:
  render del SpotMicro (4 círculos delante = 2 HC-SR04) y fotos de un DOGZILLA Lite (parecido, no igual: servos de bus,
  246×145×170-195 mm de pie, ~99-125 mm plegado). El usuario a veces llama "rodilla" al codo (articulación 3).
- 2026-10-11 16:00: 4.º y 5.º apagados de golpe moviendo servos (postura con codos a 30/150 = topes; grupo de codos).
  Añadido: freno `power_guard()` (no mueve con throttled bit 0 salvo config `potencia.permitir_sin_corriente`);
  órdenes de movimiento con `lock.acquire(timeout=1)` → 409 si ya se mueve (no se encolan); `/api/consumo` (estimado
  por servo: suelto 0, sujeto 0,3 A, moviendo 1,2 A) para la cabecera y el panel de avisos; memoria de posición
  `ultima_posicion.json` (`save_last` tras cada acción; `pose_save` con `usar_ultima` o `angulos` del dibujo;
  `/api/lock_loose` a la última posición); modo "Copiar del robot" en El robot en vivo; tabla del mapa con diagnóstico.
  Usuario compartirá la web con un compañero (Iván): todo pensado para que pulsar mucho no apague la Pi.
  Leer la posición real de los servos NO es posible con MG996R: haría falta sacar el cable del potenciómetro a un ADC
  (ADS1115) o cambiar a servos de bus con realimentación (como los del DOGZILLA).
- 2026-10-11 16:15: LED de vida en la placa: el LED rojo de la PCA9685 va a VCC (no controlable); opción de LED en un canal libre (7-9) que mueve server.py (board_led_loop, config led_placa) con el mismo LED_PATTERN que sistema/led_vivo.py, sincronizados por la hora; el punto de la cabecera de la web también. OJO: otra sesión de Claude trabaja a la vez en este proyecto (lite.html, navegador.sh -> /lite, panel.py): no pisar sus ficheros sin coordinar.
- 2026-10-11 16:15: 6.º apagado de golpe (arranque 16:15). Después, falta de corriente casi continua (0x50005) incluso sin mover servos.
- 2026-10-11 16:35: "Bloquear todo donde está" con barra de progreso (pedido del usuario: "uno a uno, bloquea, suelta…,
  una barra de carga… 0,3-0,5 s por cada uno, y entonces guardarlo y mapearlo"); Guardar postura lo usa. Integrado lo de
  la otra sesión (lite, apagones, ±5/15/30, mapa que libera el canal viejo; su primer /api/lock_all rehecho). Pruebas:
  simulador TODO BIEN (con bloquear uno a uno / bloquea-y-suelta / a la vez y el cambio de canal del mapa). Dos sesiones
  probando a la vez chocaron en el puerto 8099 → `BASE` en prueba_servidor.py. Cable suelto enchufado por el usuario
  "en el pin 1" (¿codo de A?): pendiente de probar con "Probar" en el canal 0 y en el 1 y corregir el mapa.
- 2026-10-11 16:45: el freno bloqueaba todo (0x50005 continuo). Por petición del usuario, potencia.permitir_sin_corriente por defecto True (aviso, no bloqueo). Revisión final antes de llevar el robot a la otra casa (cable directo + HDMI): servicios, 169.254.42.1, IPv6, web, /lite, panel, LED, PCA 0x40: todo bien; solo falla la alimentación de la Pi. Sin posturas guardadas (el usuario borró las suyas).
- 2026-10-11 20:35: intento de WiFi (PENDIENTE para el compañero). ESP-12 suelto descartado (sin resistencias de
  arranque no responde). Plan: NodeMCU (ESP8266) por el puerto serie de la Pi con firmware esp_slip_router (red SLIP:
  lento, vale para /lite). Cableado propuesto, todo desde la Pi y SIN el V+ de la clema: NodeMCU VIN -> pin 4 (5 V),
  RX -> pin 8 (TXD), TX -> pin 10 (RXD), GND -> pin 14. A las 20:35 los pines 8/10 no tenían nada conectado (prueba con
  pull-down: leen 0) y por el serie no llegó ningún byte. Para usarlo: quitar `console=serial0,115200` de cmdline.txt
  y deshabilitar serial-getty@ttyAMA0; instalar esptool; grabar con FLASH+RST. Alternativa más sencilla: USB WiFi en
  modo punto de acceso (NetworkManager). Hacia las 20:28, al alimentar el NodeMCU desde el V+ de la clema, hubo un
  cortocircuito con algo de humo en la parte de potencia de los servos: revisar condensador de la PCA, cables y fuente
  antes de volver a enchufarla. La Pi y la PCA (0x40, lado lógico) siguen bien. 9 apagados de golpe en total (dos más
  al enchufar/desenchufar cables en caliente).

- 2026-10-11 20:45: la PCA9685 quedó quemada en la parte de potencia (los servos no reciben V+; la lógica 0x40 sí respondía). El usuario la cambia por la de repuesto: mismos canales y dirección 0x40. Pi apagada con poweroff.
- 2026-10-11 21:40: WiFi con NodeMCU por el puerto serie (pines 4/14 alimentación, 8/10 datos): instalado esptool 5.5 en
  ~/robot y descargado esp_slip_router (martin-ger) en ~/esp-firmware (0x00000.bin, 0x10000.bin). Los pines 8 y 10 leen
  alto con pull-down (hay algo conectado), pero el ESP no manda ni un byte (ni el mensaje de arranque a 74880 baudios) y
  esptool no conecta con FLASH+RST: probablemente TX/RX al revés (rótulos de la placa cruzados) o el pulsado fuera de
  tiempo. Comando para grabar (con la consola serie parada: sudo systemctl stop serial-getty@ttyAMA0):
  sudo ~/robot/bin/python -m esptool --port /dev/ttyAMA0 --baud 115200 --before no-reset --after no-reset
  --connect-attempts 100 write-flash --erase-all -fm dio -fs detect 0x00000 0x00000.bin 0x10000 0x10000.bin
  Lo más fácil: un cable micro USB de DATOS al NodeMCU -> /dev/ttyUSB0 y esptool lo pone solo en modo grabación.
  Después: slattach -L -p slip -s 115200 <puerto>; sl0 192.168.240.2 pointopoint 192.168.240.1; consola del ESP por
  telnet 192.168.240.1 7777 (set ap_ssid / ap_password, save, reset); el portátil en la WiFi del ESP abre http://192.168.240.2/lite.
- 2026-10-12 (sesión desde el portátil, cable directo 169.254.42.1): el usuario eligió la **variante 6** de "De pie" y
  pidió B3 45° y A3 15° más hacia delante "en el inicio" → calibración de canal: B3 (6) offset +45 (en el registro
  se ve que lo subió a mano de 130 a 150, el tope lógico), A3 (1) offset −15 (espejo de B3: **sentido pendiente de
  confirmar** por el usuario). B3 queda en 175° físicos en la de pie: al recoger no se dobla más. Arreglo de verdad:
  girar el horn de B3 3 dientes (MG996R, 25 dientes = 14,4° cada uno → 43°) y dejar el ajuste en ~+2; el de A3 es
  1 diente. "Le cuesta levantarse": con la fuente de 5 A `move_many` iba servo a servo (tandas de 1) y la rutina
  empezaba con "mitad" (todo a 90 = patas rectas, lo más alto) → cada pata subía sola su esquina. Ahora: `stand_program`
  (recoger y estirar las 4 a la vez, despacio) y `juntos`. Borradas las 8 variantes viejas y las posturas de_pie_v1/v2
  (eran iguales; quedan en git, commit 04abf55). Pruebas con simulador: TODO BIEN (se arreglaron dos que dependían del
  canal 0 de A3 y del tiempo del LED). Pendiente: probar en el robot y que el usuario marque ✓; después, las marchas.
  La fuente de servos de 5 A sigue siendo poca: 6 V y ≥10 A.
- 2026-10-12 (2): el usuario: "la 2 con un poco de la 5" (y la 6.1 también iba bien) → **6.7** = agachado 30 y pectorales
  +5, marcada ✓ (convención guardada; "de pie" = la 6.7). Pidió velocidades (marchas 1ª-5ª) y 5 rutinas de andar:
  paso a paso, con balanceo, trote lento, hacia atrás y paseo. Nuevo motor de marcha (pie vertical, todo `juntos`).
  Con B3 a +45, al levantar la pata B andando el codo llega a 175° (le faltan ~6°): la pata B levanta menos hasta
  girar su horn. Pruebas con simulador: TODO BIEN. OJO al probar: `pkill -f` con la ruta absoluta no encuentra un
  servidor de prueba lanzado con ruta relativa (se quedó uno vivo en el 8097 con el simulador): parar por el puerto.
- 2026-10-12 (3): probó paso a paso, con balanceo, girar a la derecha y trote lento (todas terminadas; la Pi sin caídas
  de tensión). El "7,2 A" del trote es la estimación de la web (4 servos moviéndose ×1,2 + 8 sujetos ×0,3), no una medida.
  Pidió de calibración por defecto C1 −5 y D3 +10, luego D3 +18 "la dirección más" (puestos en servo_map.json; ojo: a mano había dejado C1 en 75 = −10 y
  D3 en 115 = −5 respecto a la 6.7, y D1 en 100 = +5, D2 en 70 = −5; se le dijo) y "10° a un hombro hacia la posición
  del otro" (no dijo cuál ni hacia dónde: se le dio el panel "Afinar patas" con los 12 servos para que lo ajuste él).
  Comentó que las patas tienen "cierta (a)simetría": de ahí el panel.
- 2026-10-12 (4): D3 a +18 ("la dirección más"). Barra de botones arriba (`#quick`, en `renderRt`): las 5 de pie
  favoritas (`FAVORITAS_6`: 6.7, 6, 6.1, 6.2, 6.5; candidatas con `favorita`), Bajar despacio, las de andar
  (candidatas con `andar`), marchas y Parar. Más rutinas de andar (el usuario: "con más fuerza", "desde arriba va mejor",
  "más rápidas"): rápido (`fast`: el pie sube y avanza en un movimiento, velocidad rápida), alto (agachado `ALTA` 22:
  el codo hace menos fuerza y B3 tiene margen), alto y rápido, pasitos rápidos, paso largo, trote rápido, trote alto y
  de lado (`crab_program`, con los pectorales; sentido sin confirmar). Pruebas con simulador: TODO BIEN.
- 2026-10-12 (5): el usuario afinó los servos en "Afinar patas" con el robot en la 6.7 y la dejó **por defecto**:
  ajustes A1 +10, A3 −15, B1 −15, B3 +50, C1 −5, C3 −10, D3 −22 (D3 al final al revés que el +18 que pidió de palabra).
  Físicos en la de pie: A 105/105/45, B 70/75/170, C 80/105/50, D 95/75/98 (pectoral/hombro/codo).
- 2026-10-12 (6): resultados del usuario: solo avanzan el **galope** (3 cm adelante) y el **camello/ambladura** (1 cm
  atrás); el resto no avanza. "Saludar con B" se caía hacia la esquina B. Marchas creativas por fases (`phase_gait`,
  `CREATIVAS`: paso de caballo, tölt, ambladura, trote elíptico, piaffe, passage, medio galope, galope, brinco, bote).
  20 rutinas **verdes** (`nuevas_routines`, candidatas con `nueva`, primeras en la lista y fila "Nuevas" en la barra):
  variantes del galope (largo, rápido, alto, otra mano, girando izq/der), camello con paso invertido (normal y rápido),
  canguro, caballo de tiro adelante/atrás (idea del usuario: las de pie proyectan el cuerpo), balanceo potente, de lado
  derecha/izquierda (trote lateral con pectorales; el balanceo lo movía un poco a la derecha), dar la pata B/A estable
  (`shift`: cuerpo atrás y al lado contrario, la otra delantera adelante y la diagonal estirada atrás), hacer pis,
  desperezarse, menear el culete y baile.
- 2026-10-12 (7): botón grande "▲ De pie" en la cabecera (`#homeBtn`): lanza la de pie marcada ✓ (si no, la 6.7) y,
  si hay una rutina en marcha, la para antes.
- 2026-10-12 00:20: el usuario: "Galope alto" es la que más le gusta. Después el robot "no tiene potencia, no menea".
  Diagnóstico desde la Pi: Pi viva, PCA9685 en 0x40, MODE1=0x20 (despierta), PRESCALE 0x79 (50 Hz) y los pulsos de
  cada canal correctos (p. ej. B3 2387 us = 170°, A3 996 us = 45°): la lógica y el PWM van bien → falla la potencia de
  los servos (fuente de 5 A en protección o apagada, cable/clema V+-GND suelta, o el camino de V+ de la placa quemado como
  la primera PCA). Antes, 23:57, la Pi se reinició de golpe con caída de tensión al arrancar; el límite de consumo
  estaba DESACTIVADO (todas las rutinas moviendo 8-12 servos a la vez con 5 A).
- 2026-10-12 (8): "muy poca potencia, la pata D siempre se queda abajo y hay que darle un toquecito" → `amarillas_routines`
  (candidatas `suave`, `color`): De pie suave (escalones de 3° con descanso), De pie ayudando a la D (cuerpo apartado,
  D 6° por delante), De pie en dos tiempos (traseras y luego delanteras) — amarillas — y **De pie de Iván** (rosa, idea
  del usuario: delanteras un poco, traseras un poco, alternando, nunca las 4 a la vez). Todas acaban con un toquecito
  a D (la estira 8° de golpe y vuelve). Fila "De pie suave" en la barra de arriba.
- 2026-10-12 (9): **valoraciones** 0-6 estrellas (el usuario valora, Claude lee y propone otras estructuras):
  `POST /api/routine_rate {id, estrellas}` → config.json `valoraciones` {id: {estrellas, hora}}; en la web, estrellas
  bajo cada botón de la barra de arriba y en cada tarjeta (pulsar la misma estrella otra vez = 0). Para leerlas:
  `python3 -c "import json; print(json.load(open('config.json'))['valoraciones'])"` en ~/spotmicro.
  "Más rápido aún": marcha **6ª turbo** (×0,18) y **Galope turbo** (galope alto, apoyo 40 %, 8 ticks).
- 2026-10-12 (10): fallo de las estrellas: llevaban `data-n` y el manejador de los ±5 de los canales usa `d.n` como canal
  → al pulsar la estrella 5 mandaba `/api/angle {ch: 5, angle: null}` (500) y no valoraba. Ahora `data-estrellas`, y
  `/api/angle` rechaza un ángulo que no sea número (400, "no se mueve nada"). OJO con los nombres de `data-*`: varios
  manejadores de clic de la página miran el mismo `dataset`.
- 2026-10-12 (11): **ronda de estrellas** (con la marcha en 6ª turbo y el límite de consumo desactivado: la Pi se apagó
  de golpe en "De pie en dos tiempos" y "ayudando a la D"). Estrellas: galope a la derecha 5; galope alto 4; galope
  largo, galope rápido, camello al revés, camello rápido, canguro, caballo de tiro atrás y de pie suave 3; galope a la
  izquierda y ayudando a la D 2; galope turbo, galope a la otra mano y caballo de tiro 1. De palabra: caballo de tiro se
  cae hacia delante; canguro va bien pero muy girado a la derecha; dar la pata B y A (estables) se caen hacia delante
  (el peso del robot va delante); de pie suave: el codo de D se queda sin fuerza al final. Mapa: lo que avanza es la
  familia del galope (orden D C A B, apoyo 45 %, ritmo) y mejor alta; la zancada asimétrica decide el giro.
  → **6 de plata** (`plata_routines`, candidatas `plata`, caja plateada arriba con el ranking de estrellas): galope
  plata (alto, pisada firme `press`, derechas +2), galope plata a la derecha (cruce 5★ + 4★), galope plata suave,
  canguro plata (derechas más largas), camello plata y dar la pata sentado. `phase_gait(press=…)`: el pie apoyado
  empuja hacia el suelo (penetration depth de spot_mini_mini, OpenQuadruped; allí un RL/ARS ajusta altura del pie y
  penetración). **Curiosas** (azules, sin estrellas, `CURIOSAS`): balanceo potente, baile, menear, pis, desperezarse.
- 2026-10-12 (12, sesión paralela "fork"): **la tele**. "Rutinas populares accesibles desde el HDMI, P = de pie, las
  demás por código con un desplegable". Panel: P, 0/Intro selector, populares en el recuadro del robot, U probar salida.
  /lite: DE PIE, caja de código, desplegable, populares, marcha y teclado. Web del PC: código delante de cada rutina.
  Todo a LF + `.gitattributes`. OJO: dos sesiones de Claude editaron a la vez la carpeta compartida del PC
  (Gith/spotmicro) y una pisó a la otra en server.py; esta trabajó en un clon aparte (Gith/spotmicro-tele) y la otra
  integra con `git fetch` de la Pi antes de desplegar.
- 2026-10-12 (13): "Dar la pata sentado" fue un éxito y "Galope plata a la derecha" lo que mejor anduvo. Caja de plata
  por grupos (candidatas `grupo`): **Galope fuerte** (6 agresivas de la plata a la derecha: zancada, más aire, ritmo,
  más recto, pisotón, a tope; códigos 74-79) y **Dar la pata sentado** A y B × 4 versiones (`pata_sentado`: 1 la
  original; 2 pata más arriba y adelante; 3 culo más abajo; 4 culo abajo, cuerpo atrás y pata lo más arriba; A 96-99,
  B 16 y 47-49). **RETIRADAS** (filtro en `_candidate_routines`, el código sigue): las de 1-2 estrellas (galope turbo,
  otra mano, izquierda, caballo de tiro, ayudando a la D), las dar la pata de pie (estables y saludar con B) y andar
  alto y rápido, pasitos, paso largo, trote alto y bote de gacela. Fusionado a mano con b2596e6 (tele) con git merge-file.
- 2026-10-12 (14): **fuente de servos nueva de 20 A** (la vieja era de 5 A). Botones "Fuente vieja 5 A" / "Fuente nueva 20 A"
  junto al deslizador de la barra de consumo (`data-psrc`; cambiar de fuente ya no pregunta lo de quitar el límite,
  solo desmarcar la casilla). Puesto 20 A con el límite activado: con 20 A `max_movers` deja mover los 12 a la vez, así
  que no frena; solo avisa. El medidor de arriba escala con `limite_A`.
- 2026-10-12 (13, sesión "fork"): Luis: "dar la pata sentado 1 y 2 van muy bien, y el galope fuerte también: en el HDMI".
  `FIJAS_TELE` (plata-pata-sentado 16, pata-b-sentado-2 47, fuerte-ritmo 76) van siempre primero en `popular_ids`;
  luego por estrellas (≥3) y al final la plata sin valorar (antes salía la plata en su orden y las de 2★ primero).
- 2026-10-12 (15): **Repetir sin parar**: casilla en cada caja de rutina (y en Mis rutinas; se guarda en el navegador,
  localStorage "repetirSinParar"); `/api/routine_run {…, repetir: true}` → `start_routine(loop=True)` repite el programa
  hasta Parar/Soltar todo (`routine["bucle"]`, `routine["vuelta"]`; la cabecera muestra "🔁 vuelta N"). **Oro** (fila dorada
  en la caja de plata, códigos 101-106, `oro_routines`): mejoras de "Galope fuerte · ritmo" (la mejor según las estrellas y
  lo que dijo el usuario; "más aire" tenía 2★), cambiando UNA cosa cada una: despegue limpio (`phase_gait(swing="limpio")`:
  sube antes de avanzar), empuje final (`stance="empuje"`: aprieta al final del apoyo), B sube con el hombro
  (`hip={"B": 1.0}`, su codo está en el tope), ritmo + pisotón (press 7), aire con ritmo, y todo lo bueno junto.
  El contador de códigos automáticos ahora salta los fijos de CODIGOS.
- 2026-10-12 (14, sesión "fork"): **modo Toreto** (Luis: "máxima potencia, como en Fast and Furious, no me importa si
  rompo algo"). `toreto` en server.py: `_move_toreto` (salto directo con `TORETO_PATADA` 6° de más y vuelta, espera
  `TORETO_S_POR_GRADO`), todo a la vez (ignora las tandas), también en `move_to`; config `toreto` (se guarda y se aplica
  al arrancar). **Nitro** aparte: `set_nitro` pone la PCA9685 a `TORETO_HZ` 200 Hz (y `set_pulse_width_range` de los 16:
  la librería calcula el pulso con la frecuencia) y `nitro_watch` lo apaga a los `NITRO_S` 120 s; nunca al arrancar; al
  parar el servidor vuelve a 50 Hz (si no, al arrancar la librería pone 50 Hz con los registros de 200 Hz = pulsos ×4).
  La otra sesión avisó con razón: los MG996R son analógicos y a 200 Hz se calientan/queman. `POST /api/toreto
  {activo, nitro}`; `toreto`, `nitro`, `toreto_hz`, `nitro_quedan_s` en /api/state y /api/routines. Web: casilla
  TORETO en la cabecera (cabecera con raya roja si está activo) y caja roja `#toreto` (Toreto, Nitro con confirmación,
  "Repetir sin parar" con el `repetir` de la otra sesión, y los Galope fuerte en rojo con estrellas).
- 2026-10-12 (16): reparto de trabajo: la otra sesión hace los modos nuevos (Toreto…) en server.py/index.html y esta
  sesión lo lleva al **HDMI** (pantalla/panel.py y web/lite.html son de esta sesión). Llevado ya: **repetir sin parar**
  (panel: tecla R en el selector, se guarda en pantalla/repetir; /lite: casilla y tecla R, localStorage "liteRepetir"),
  **velocidad** con +/- (panel y /lite), "vuelta N" en la rutina en marcha, y las **de oro** (grupo propio y color).
  Probar el panel sin tele: copia en /tmp con la API apuntando a un servidor de prueba y pruebas/ver_selector.py.
