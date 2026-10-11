# API del servidor (`web/server.py`, puertos 80 y 8080)

Todo es JSON. Errores: `400 {"error": "texto para el usuario"}`; `409` si el robot ya se está moviendo (las órdenes
de movimiento no se encolan: pulsar muchos botones a la vez podía pasar de amperios). Con una rutina en marcha, los
movimientos manuales devuelven 400 salvo Soltar todo. No hay contraseña: solo en red local / cable directo.

## Lectura (GET)

| Ruta | Qué devuelve |
|---|---|
| `/api/state` | `angles` (lógicos por canal, `null` = suelto), `map`, `poses`, `rutina`, `potencia`, `convencion`, `ultima` (memoria de posición), `toreto`, `nitro`, `toreto_hz`, `pwm` {hz, zona, calor, enfriando, limite_s, quedan_s, patada, min, max}, `limits` |
| `/api/consumo` | Consumo estimado por servo y total (`total_A`, `limite_A`), `falta_corriente`, `progreso` de la rutina, `pwm`. La web lo pide cada segundo. |
| `/api/routines` | `candidatas` (todas las rutinas generadas, con `id`, `nombre`, `desc`, `codigo`, `pasos`, flags de familia/color), `guardadas` (las de Luis), `convencion`, `pruebas` (marcas ✓/✗), `ejecutando`, `marcha`/`marchas`, `valoraciones`, `home`, `populares`, `codigo_de_pie`, `toreto`, `nitro`, `pwm` |
| `/api/system` | Alimentación (`throttled`), temperatura, red, pantalla, `energia` (últimas líneas de energia.log), `apagones` |
| `/api/informe` | Comprobación general (servicios, red, alimentación, SD, PCA, mapa, git) |
| `/api/conexion` | Cómo te estás conectando (router / cable directo / la propia Pi), URLs y comandos SSH |
| `/`, `/lite`, `/ayuda`, `/static/…` | Web completa, web de la tele, `AYUDA.md`, ficheros estáticos (Blockly, esquema.svg) |

## Movimiento y servos (POST)

| Ruta | Cuerpo |
|---|---|
| `/api/angle` | `{ch, angle (número, 30-150), speed: lento\|normal\|rapido}` |
| `/api/test` · `/api/test_many` | `{ch \| chs[≤4], delta, center, speed, hold}`: ida y vuelta para identificar un servo |
| `/api/release` · `/api/leg_release` · `/api/off` | `{ch}` · `{pata}` · `{}` (Soltar todo; también para la rutina) |
| `/api/pose90` · `/api/group` | `{speed}` · `{articulacion, angle, mirror, speed}` (todos los pectorales/hombros/codos) |
| `/api/lock_loose` · `/api/lock_all` | sujetar los sueltos donde estaban `{a: "ultima"\|"mitad"}` · "Bloquear todo donde está" `{segundos, soltar}` |
| `/api/pose_save` · `/api/pose_go` · `/api/pose_delete` | `{nombre, pata: "todas"\|A-D, angulos?, usar_ultima?}` · `{nombre, pata, speed}` · `{nombre}` |
| `/api/calibrate` | `{ch, action: zero\|invert\|reset\|nudge, delta?}` (nudge = correr el ajuste ±N°, lo usa "Afinar patas") |
| `/api/map` | `{ch, pata, articulacion}` o `{ch, libre: true}` o `{ch, clear: true}` |
| `/api/leg_check` | `{pata, ok: true\|false\|null}` ("¿la pata está como el dibujo?") |
| `/api/home` | `{}`: para lo que haya y lanza la de pie elegida (tecla P) |

## Rutinas (POST)

| Ruta | Cuerpo |
|---|---|
| `/api/routine_run` | `{candidata: id}` o `{codigo: 76 \| "P"}` o `{nombre}` (guardada) o `{programa: [...], nombre}`; y `repetir: true` para que no pare |
| `/api/routine_stop` | `{}` |
| `/api/routine_speed` | `{marcha: 1..6}` (vale con la rutina en marcha) |
| `/api/routine_rate` | `{id, estrellas: 0..6}` (las valoraciones de Luis) |
| `/api/try_mark` | `{id, mark: "bien"\|"mal"\|""}`; ✓ en una "de pie" la hace la elegida (guarda la convención y las posturas) |
| `/api/convention_reset` | `{}` (volver a elegir la de pie) |
| `/api/routine_save` · `/api/routine_get` · `/api/routine_delete` | `{nombre, candidata \| programa, bloques}` · `{nombre \| candidata}` · `{nombre}` |

## Potencia y sistema (POST)

| Ruta | Cuerpo |
|---|---|
| `/api/power` | `{limitar, amperios (1-60; fuente vieja 5, nueva 20), permitir_sin_corriente}` |
| `/api/hz` | `{hz: 50..200}` (400 si los servos se están enfriando o la Pi tiene falta de corriente) |
| `/api/toreto` | `{activo?, patada? (0-10), hz?, nitro? (true = 200 Hz, false = 50 Hz)}` |
| `/api/board_led` | `{canal, activo}` (LED de vida en un canal libre de la PCA) |
| `/api/screen` · `/api/screen_boot` | `{modo: panel\|web\|consola}` (qué muestra la tele ahora / al arrancar) |
| `/api/system_action` | `{accion: apagar\|reiniciar\|instalar-navegador}` (apagar suelta los servos antes) |

## Ficheros de datos

`servo_map.json` (canales, `offset`, `invertir`), `poses.json`, `config.json` (`convencion`, `potencia`, `marcha`,
`pruebas`, `valoraciones`, `toreto`, `toreto_patada`, `verificacion`, `led_placa`), `rutinas.json`,
`ultima_posicion.json`. Registros (no van a git): `web/acciones.log` (todo lo que se hace), `energia.log` (arranques
y cortes), `vigilante.log`. Escribe siempre con `write_json` (temporal + fsync + .bak + rename): hay cortes de corriente.
