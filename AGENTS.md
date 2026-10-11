# AGENTS.md: léeme primero (para cualquier IA: GPT, Claude, Codex, Cursor… y para personas)

Este fichero se basta solo: si eres un chat sin acceso al robot (ChatGPT en el navegador, por ejemplo), con esto ya
entiendes el proyecto. Si tienes acceso a la Pi o al repo, lee después `docs/ESTADO.md` (dónde estamos y a dónde vamos),
`docs/RUTINAS.md`, `docs/API.md` y, para la historia completa con fechas, el registro al final de `CLAUDE.md`.

> Proyecto de **Luis** (dueño del robot) y su compañero **Iván**. Hablan español; Luis suele dictar por voz, así que
> sus mensajes traen errores de transcripción ("la pata de Dinamarca" = la pata D, "megahercios" = hercios,
> "gustando" = costando). Pregunta si algo no cuadra, pero no le hagas repetir lo evidente.

## 1. Qué es

**LUIS LECI control**: el control de un robot cuadrúpedo **SpotMicro** impreso en 3D (heredado a medio montar), con
una **Raspberry Pi 2** (sin WiFi) y una placa de servos **PCA9685** que mueve **12 servos MG996R** (analógicos).
Todo se maneja desde una **web** servida por la propia Pi y desde una **pantalla HDMI** (tele) con teclado.
Funciona sin internet: con router 4G o con un cable de red directo al portátil.

## 2. Reglas de oro (no negociables)

1. **Seguridad física.** Los servos van con su propia fuente (ahora **5 V y 20 A**) en la clema V+/GND de la PCA9685,
   con GND común; **nunca** desde la Pi. Hubo un cable Schuko (230 V) metido en la clema: **jamás enchufarlo**.
   No cambiar cables con todo encendido (ya quemó una PCA9685 y apagó la Pi). Probar primero **con el robot en el aire**.
2. **No quemar los servos.** Los MG996R son analógicos, pensados para 50 Hz. La web deja subir a 200 Hz pero con una
   protección de calor que vuelve sola a 50 Hz (ver `docs/RUTINAS.md`). No quites esa protección.
3. **La Pi se apaga de golpe por falta de corriente** con las rutinas fuertes (está en `energia.log`). Cada corte puede
   estropear la SD: no hagas cambios que escriban mucho en disco, y usa escrituras atómicas (`write_json`).
4. **No rompas lo que ya funciona.** Antes de desplegar, pasa las pruebas con el **simulador de servos** (sección 7).
   Nada de reescribir ficheros enteros de memoria: cambios pequeños y localizados.
5. **La fuente de la verdad es el repo git `~/spotmicro` EN LA PI.** Haz commit allí de cada cambio, con un mensaje
   claro, y apunta lo hecho al final de `CLAUDE.md` (sección "Registro", con fecha).
6. **Saltos de línea LF** (hay `.gitattributes`). Si editas desde Windows, no copies ficheros con CRLF a la Pi.
7. **Credenciales**: `NOTAS-LOCALES.md` existe solo en la Pi y está en `.gitignore`. Nunca lo subas ni copies su contenido.
8. **Puede haber dos IA trabajando a la vez** (pasó el 12-10-2026). Antes de desplegar: `git log` en la Pi para ver si
   alguien ha hecho commit; si es así, fusiona (merge de 3 vías) en vez de pisar. Reparte ficheros por dueño.

## 3. Cómo llegar al robot

| Cómo | Dirección |
|---|---|
| Cable de red directo del portátil a la Pi (Windows se pone 169.254.x.x solo) | `http://169.254.42.1` · `ssh luislecinana@169.254.42.1` |
| Con el router 4G Huawei | suele ser `http://192.168.8.104` |
| Último recurso (IPv6 de enlace local fija) | `ssh -L 8080:localhost:8080 luislecinana@fe80::ba27:ebff:fe16:ec15%<ifIndex>` |

- Web: puerto 80 y 8080. Versión ligera para la tele: `/lite`. Ayuda para el usuario: `/ayuda` (= `AYUDA.md`).
- SSH con clave (PC de sobremesa y portátil de Luis autorizados). `sudo` sin contraseña.
- La Pi **no tiene reloj**: sin internet su hora puede ir mal (no te fíes de las fechas de los ficheros).

## 4. Hardware y nombres (los de Luis)

Patas vistas desde arriba con la cabeza delante: **A** delantera izq · **B** delantera der · **C** trasera der ·
**D** trasera izq. Articulaciones del cuerpo al suelo: **1 pectoral** (abre/cierra la pata) · **2 hombro**
(adelante/atrás) · **3 codo** (dobla la parte de abajo; Luis a veces lo llama "rodilla"). Ej.: **B3** = codo de B.

| Pata | 1 pectoral | 2 hombro | 3 codo |
|---|---|---|---|
| A delantera izq | canal 4 (ajuste +10) | 2 | **1** (ajuste −15) |
| B delantera der | 5 (ajuste −15) | 3 | **6** (ajuste **+50**: horn mal puesto, llega a su tope) |
| C trasera der | 10 (ajuste −5) | 12 | 14 (ajuste −10) |
| D trasera izq | 11 | 13 | 15 (ajuste −22; es la pata más débil) |

Canales libres: 0, 7, 8, 9. PCA9685 en I2C 0x40. El mapa y los ajustes están en `servo_map.json`.

**Ángulo lógico y físico.** La web y las rutinas trabajan con el ángulo **lógico** (límites 30–150°). El físico que
recibe el servo es `lógico (o 180−lógico si "invertir") + ajuste`, recortado a 5–175°. Las patas derechas (B, C) van
en espejo (`180 − a`). Un **gesto** se describe con los ángulos de la pata D y se traduce a las demás con la
**convención** guardada en `config.json` (`sh`, `sk`, `fi`: signo de hombro, de codo y delanteras invertidas). La
buena es la "variante 6": hombro −, codo +, delanteras invertidas.

## 5. Arquitectura

| Pieza | Qué hace |
|---|---|
| `web/server.py` | Servidor HTTP (Python estándar + `adafruit-circuitpython-servokit`), servicio `spotmicro-web`. Todo el movimiento, rutinas, protecciones y API. |
| `web/index.html` | La web completa (un solo fichero, sin frameworks). Blockly en `web/static/blockly` (programar con bloques). |
| `web/lite.html` | Web ligera para la tele (la abre el navegador kiosco de la Pi). |
| `pantalla/panel.py` | Panel de texto (curses) en la HDMI, servicio `spotmicro-pantalla`; teclas para todo. |
| `sistema/` | LED de vida, vigilante (repara red/web/pantalla cada minuto), instalador del navegador, utilidades. |
| `pruebas/` | Simulador de la PCA9685 (`fake/adafruit_servokit.py`), pruebas del servidor (`prueba_servidor.py`), vistas del panel sin tele (`ver_panel.py`, `ver_selector.py`). |
| Datos | `servo_map.json` (canales y ajustes), `poses.json` (posturas), `config.json` (convención, potencia, marcha, Toreto, valoraciones…), `rutinas.json` (las de Luis), `ultima_posicion.json` (memoria de posición). |

Conceptos clave del motor (detalle en `docs/RUTINAS.md`):
- **Rutina** = lista de pasos JSON (`set`, `postura`, `esperar`, `repetir`, `velocidad`…) que corre en un hilo.
- **`juntos`**: mover todos los servos a la vez, 1° por servo y paso. Sin `juntos`, el límite de consumo los mueve por
  tandas; para levantar el cuerpo eso era fatal (cada pata cargaba sola con su esquina).
- **Marchas de velocidad** 1ª–6ª (multiplican pausas y esperas), cambiables con la rutina en marcha.
- **Repetir sin parar**: `/api/routine_run {…, repetir: true}` hasta Parar.
- **Toreto** (máxima potencia: salto directo con "patada", todo a la vez) y **barra de Hz** 50–200 con calor estimado.
- **Códigos** de rutina (números) para lanzarlas desde la tele: 10 o P = de pie, 11–16 plata, 101–106 oro, etc.
- **Estrellas** 0–6 que pone Luis a cada rutina: son el "aprendizaje" del proyecto (ver `docs/ESTADO.md`).

## 6. Cómo se trabaja (flujo que ya funciona)

1. Copia de trabajo en el portátil (`git clone luislecinana@169.254.42.1:spotmicro`, con `core.autocrlf=false`), o
   edita directamente en la Pi.
2. Cambio pequeño → prueba con el simulador (sección 7) → copia a la Pi **sin CRLF** y con escritura atómica
   (`cp x x.nuevo && sync && mv x.nuevo x`) → espera a que no haya rutina en marcha (`GET /api/consumo` →
   `progreso.running`) → `sudo systemctl restart spotmicro-web` (o `spotmicro-pantalla` si tocas el panel; `index.html`
   y `lite.html` no necesitan reinicio) → `git commit` en la Pi → apunta en `CLAUDE.md`.
3. Si otra IA ha hecho commit entretanto: `git merge-file` de 3 vías (base = el commit del que partiste) y vuelve a probar.

## 7. Probar sin tocar los servos (obligatorio antes de desplegar)

En la Pi, desde una copia (no desde `~/spotmicro`, porque las pruebas cambian datos):
```
mkdir -p /tmp/prueba && cd ~/spotmicro && git archive HEAD | tar xf - -C /tmp/prueba
cp servo_map.json poses.json /tmp/prueba/ && cd /tmp/prueba   # y copia encima tus ficheros cambiados
FAKE_LOG=/tmp/fake.log SPOTMICRO_PORTS=8095 SPOTMICRO_FAST=1 PYTHONPATH=pruebas/fake ~/robot/bin/python web/server.py &
BASE=http://127.0.0.1:8095 FAKE_LOG=/tmp/fake.log python3 pruebas/prueba_servidor.py   # debe acabar en "TODO BIEN"
```
Usa un puerto libre (8095–8099) si hay otra prueba en marcha, y para el servidor de prueba al acabar (por su PID:
`ss -ltnp | grep :8095`). Para ver el panel de la tele sin tele: apunta `API` de `pantalla/panel.py` (en la copia) al
puerto de prueba y ejecuta `~/robot/bin/python pruebas/ver_selector.py /tmp/prueba/pantalla/panel.py 7`.
Puedes abrir `http://169.254.42.1:8095/lite` en el navegador: son servos simulados, no se mueve nada.

## 8. Dónde estamos (resumen; el detalle en `docs/ESTADO.md`)

- **Funciona**: web completa, tele, de pie (6.7 con los ajustes de Luis), "Dar la pata sentado" (éxito), las marchas de
  la familia del **galope** avanzan algo, "Galope fuerte · ritmo" es la mejor (5★).
- **Falla o limita**: la Pi se apaga por falta de corriente con rutinas fuertes; B3 en su tope (girar su horn 3
  dientes); el codo de D se queda sin fuerza; los MG996R justos de par; las marchas clásicas (paso a paso, trote) no avanzan.
- **Siguiente**: valorar las 6 "oro" (101–106) y cruzar las mejores; arreglar la alimentación de la Pi; recolocar el
  horn de B3; luego marchas más largas y giros.

## 9. Si eres un chat sin acceso a la Pi (p. ej. GPT en el navegador)

Pide a Iván que te pegue el fichero que haya que cambiar (o la parte), propón el cambio como **diff pequeño** con
explicación, y recuérdale probarlo con el simulador (sección 7) antes de desplegar. Guía para él: `docs/USAR-CON-GPT.md`.
