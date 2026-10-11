# Guía para Iván: trabajar en el robot con GPT (u otra IA)

Hola, Iván. Esto es lo mínimo para que tú y tu IA podáis seguir sin romper nada. El robot y la web ya funcionan;
cualquier cambio se hace en pequeño, se prueba sin mover servos y luego en el robot (primero en el aire).

## 1. Darle el contexto a la IA

- **Si usas un agente con acceso a los ficheros** (Codex, Cursor, Claude Code, GPT con herramientas): ábrelo en la
  carpeta del proyecto. Lee solo `AGENTS.md` (es el estándar que buscan) y de ahí va a `docs/`.
- **Si usas ChatGPT en el navegador**: al empezar la conversación sube o pega **`AGENTS.md`** y **`docs/ESTADO.md`**.
  Si vais a tocar rutinas, añade `docs/RUTINAS.md`; si vais a tocar la web o la tele, `docs/API.md`. Después pega solo
  el trozo de código que haya que cambiar (busca la función por su nombre en `web/server.py`).

Mensaje de arranque que funciona bien (cópialo):

```
Vas a ayudarme con un robot SpotMicro (Raspberry Pi 2 + PCA9685 + 12 servos MG996R). Te paso AGENTS.md y
docs/ESTADO.md: léelos y respeta sus reglas de oro. Quiero <lo que quieras>. Propón cambios pequeños como diff,
explica qué hacen y dime cómo probarlo con el simulador antes de tocar el robot. No reescribas ficheros enteros.
```

## 2. Llegar al robot

- Cable de red del portátil a la Pi → web `http://169.254.42.1`, SSH `ssh luislecinana@169.254.42.1`.
- Con el router 4G → normalmente `http://192.168.8.104`.
- En la tele (HDMI): **P** de pie, **0** o **Intro** para elegir rutina por número, **X** parar, **S** soltar todo,
  **R** repetir sin parar (en el selector), **+ / −** velocidad, **F** Toreto, **N** Nitro, **, .** Hz. **H** = ayuda.
- Tu clave SSH: pídele a Luis que la añada a `~/.ssh/authorized_keys` de la Pi (la contraseña no está en los documentos).

## 3. Cómo se hace un cambio sin romper nada

1. En la Pi: `cd ~/spotmicro && git status && git log --oneline -3` (que no haya cambios a medias de otro).
2. Haz el cambio en una copia (sección 7 de `AGENTS.md`), pasa `pruebas/prueba_servidor.py` contra el simulador y
   mira que acabe en **TODO BIEN**.
3. Copia los ficheros a `~/spotmicro`, reinicia el servicio que toque
   (`sudo systemctl restart spotmicro-web` o `spotmicro-pantalla`; `index.html` y `lite.html` no lo necesitan).
4. `git commit` en la Pi con un mensaje claro, y una línea al final de `CLAUDE.md` (Registro) contando qué y por qué.
5. Prueba en el robot **en el aire**; luego en el suelo, sujetándolo. Valora la rutina con estrellas en la web.

## 4. Cosas que la IA debe saber sí o sí

- **Falta de corriente**: si la Pi se apaga sola, no es un fallo del código: es el cargador/cable de la Pi o el GND de la
  clema. Mira la franja roja de "apagados de golpe" en la web o `energia.log`.
- **No tocar** la protección de calor de los Hz ni subir los límites de ángulo (30-150 lógico, 5-175 físico).
- **Calibración**: si una pata no queda como su pareja, se corrige en la web → "Afinar patas" (±1/±5 por servo),
  no cambiando las rutinas.
- **B3** está al límite (horn mal puesto) y **D3** es débil: leer `docs/ESTADO.md` antes de culpar a una rutina.
- Las **estrellas** de Luis (`config.json` → `valoraciones`) son el criterio para hacer rutinas nuevas: cruzar las mejores
  y cambiar una cosa cada vez para saber qué ayuda.

## 5. Ideas de por dónde seguir

Las tienes en `docs/ESTADO.md`, sección 5. Las primeras: valorar las 6 de oro (101-106), arreglar la alimentación
de la Pi, recolocar el horn de B3 y hacer una marcha recta con mando de giro desde la tele.
