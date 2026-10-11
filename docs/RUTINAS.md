# Rutinas: motor, familias, códigos y cómo añadir una

Todo está en `web/server.py`. Busca las funciones por nombre (los números de línea cambian).

## 1. El lenguaje de pasos (`run_steps`)

Una rutina es una lista de pasos JSON que corre en un hilo (`start_routine`). Se para con `/api/routine_stop` o
`/api/off` (Soltar todo). Con una rutina en marcha la web rechaza los movimientos manuales.

| Paso | Qué hace |
|---|---|
| `{"op": "set", "objetivos": [{"pata": "A", "art": "codo", "angulo": 60}, …], "juntos": true}` | Ángulos **lógicos** ya calculados (lo que generan las rutinas). `juntos`: todos a la vez. |
| `{"op": "articulacion", "pata": "A".."D"\|"todas", "art": …, "angulo": 30..150}` | Ángulo de **gesto** (el de la pata D; las demás en espejo según la convención). |
| `{"op": "articulacion_rel", "pata", "art", "delta"}` · `{"op": "pata", "pata", "pectoral", "hombro", "codo"}` | Relativo / pata entera (gesto). |
| `{"op": "postura", "nombre": "de pie"}` · `{"op": "mitad"}` · `{"op": "soltar", "pata"?}` | Posturas guardadas, todo a 90°, soltar. |
| `{"op": "esperar", "s": 0..30}` · `{"op": "velocidad", "v": "lento\|normal\|rapido\|suave"}` | Pausas (s/grado: 0,05 · 0,03 · 0,012 · 0,1). |
| `{"op": "repetir", "n": 1..50, "pasos": […]}` · `{"op": "rutina", "nombre": "…"}` | Bucle y llamar a otra rutina guardada. |

Encima de eso:
- **Marcha** (config `marcha`, 1ª-6ª: ×2 · ×1,4 · ×1 · ×0,6 · ×0,35 · ×0,18) multiplica pausas y esperas; se cambia en marcha.
- **Repetir sin parar**: `start_routine(..., loop=True)` (desde `/api/routine_run {…, repetir: true}`); `routine["bucle"]`, `routine["vuelta"]`.
- **Límite de consumo** (`move_many`/`max_movers`): sin `juntos`, reparte en tandas según los amperios de la fuente
  (config `potencia.amperios`, ahora 20 A: los 12 a la vez). `juntos` lo ignora.
- **Toreto** (`toreto["on"]`): cada movimiento va de un salto (sin interpolar), todo a la vez, con una "patada" de
  hasta `toreto["patada"]` grados (0-10, por defecto 3) más allá del objetivo solo si el movimiento es de 4° o más y
  nunca mayor que su mitad (con 6° fijos en cada pasito de las marchas "le daba epilepsia").
- **Hz de los servos** (`set_hz`, 50-200): más Hz = más fuerza y más calor. `nitro_watch` estima el **calor** (0-100 %)
  cada segundo: sube a más de 60 Hz mientras sujetan (100 % en 600 s a 70-100 Hz, 240 s a 110-150, 120 s a 160-200,
  con los 12 sujetos; menos sujetos, más lento) y baja a 50-60 Hz (de 100 a 0 en 300 s). Al 100 % vuelve sola a 50 Hz
  y no deja subir hasta bajar del 30 %. No sube con la Pi en falta de corriente. Al arrancar, siempre 50 Hz.

## 2. Geometría (cómo se calculan los ángulos)

- `dev_angle(pata, art, desviación, conv)`: desviación respecto a 90° → ángulo lógico de esa pata (signos de la
  convención, espejo en las derechas, delanteras invertidas).
- Modelo: patas de longitud parecida; **agacharse `beta`** = codo `beta` y hombro `beta/2` (el pie queda bajo la cadera).
  `stance(conv, beta, legs, pectoral)`; `base_stance(conv)` = la de pie elegida (agachado de delanteras y traseras + pectoral).
- `S(objetivos, juntos=False)` crea un paso `set`. `no_pec(objs)` quita los pectorales.
- `stand_program(conv)`: levantarse sin que una pata cargue sola: pectorales → recoger las 4 a la vez → estirarlas a la
  vez despacio.
- `phase_gait(conv, fases, duty, step, lift, ticks, cycles, v, press, swing, stance, hip)`: **el motor de las marchas
  que mejor van**. Cada pata tiene su fase (0-1) en el ciclo; mientras apoya (`duty`) el pie va de delante a atrás
  (`step`, puede ser distinto por pata para girar), en el aire vuelve en arco (`lift`). `press`: pisada firme.
  `swing="limpio"`: sube antes de avanzar. `stance="empuje"`: aprieta al final del apoyo. `hip`: cuánto de la subida
  hace el hombro por pata. `GALOPE = {D: 0, C: 0.1, A: 0.5, B: 0.6}`, `CAMELLO = {A: 0, D: 0, B: 0.5, C: 0.5}`.
- Otras: `walk_program` (paso a paso), `trot_program`, `march_program`, `crab_program` (de lado).

## 3. Familias y códigos (los que se teclean en la tele)

`candidate_routines(conv)` devuelve todas con su `codigo` (tabla fija `CODIGOS`; las que no estén cogen 100+ libres).
Las que están en `RETIRADAS` no salen (el código sigue, por si se quieren recuperar).

| Códigos | Familia (función) | Color en la web |
|---|---|---|
| 10 o P | La de pie elegida (✓), `/api/home` | botón verde grande |
| 11-15 | Ganadoras de plata (`plata_routines`, grupo `ganadora`) | plata |
| 16, 47-49 / 96-99 | Dar la pata **B** / **A** sentado, 4 versiones (`pata_sentado`) | plata |
| 74-79 | Galope fuerte (6 agresivas del galope plata a la derecha) | plata (y rojo en la caja Toreto) |
| 101-106 | **Oro**: mejoras de Galope fuerte · ritmo, una cosa cada una (`oro_routines`) | oro |
| 120, 121 | **Pedir el autobús** B / A (`autobus_routines`, de la otra sesión) | fucsia |
| 17, 18-25 | Bajar despacio; de pie 6.7, 6, 6.1, 6.2, 6.5, 6.3, 6.4, 6.6 (`VARIANTES_6`) | — |
| 26, 28, 29 | De pie suaves (escalones, dos tiempos, **de Iván**) con "toquecito" a la pata D (`amarillas_routines`) | amarillo / rosa |
| 30-46 | Nuevas: galopes, camellos, canguro, caballo de tiro, de lado (`nuevas_routines`) | verde |
| 37, 38, 63-69 | Creativas: aires del caballo (`CREATIVAS`) | — |
| 50-61, 71-73 | Andar clásico, girar, marcar el paso | — |
| 80-84 | Curiosas (balanceo potente, baile, menear, pis, desperezarse), sin estrellas | azul |
| 88-95 | Balanceo, reverencia, de pie alto/bajo, tumbarse, levantarse, sentarse, flexiones | — |

Populares en la tele (`popular_ids`): primero `FIJAS_TELE` (16, 47, 76), luego por estrellas (≥3).

## 4. Cómo añadir una rutina (receta)

1. Escribe una función o un elemento de lista que devuelva `(id, nombre, descripción, programa)` usando
   `phase_gait`, `stance`, `base_stance`, `S(..., juntos=True)`, etc. Mira `oro_routines` o `autobus_routines` como
   ejemplo corto. **Parte siempre de `base_stance(c)`** (la de pie elegida) y vuelve a ella al final.
2. Regístrala en `_candidate_routines` (al final, antes del filtro `RETIRADAS`) con sus marcas: `"etapa": 2`, y si
   quieres `"color"`, un grupo (`"plata": True, "grupo": …`) o una familia (`"nueva"`, `"curiosa"`…).
3. Dale un **código fijo** en `CODIGOS` (mira los huecos libres en la tabla de arriba; no reutilices los de las retiradas).
4. Si tiene color nuevo: CSS `button.<color>` y `.card.<color>` en `web/index.html` (y en `web/lite.html` para la tele).
5. Añade una comprobación en `pruebas/prueba_servidor.py` (que exista, su código y que termine) y pasa el simulador.
6. Apunta en `CLAUDE.md` y en `docs/ESTADO.md`; despliega; Luis la prueba y la valora con estrellas.

Criterios aprendidos: empezar en el aire; `juntos` para todo lo que carga peso; nada de patadas grandes en pasos
pequeños; respetar los topes (30-150 lógico, 5-175 físico; `tope_warnings` avisa); con el peso delante, sentarse
antes de levantar una delantera.
