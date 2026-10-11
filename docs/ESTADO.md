# Estado del proyecto: dónde estamos y a dónde vamos

Actualizado el 12-10-2026 (madrugada). Si cambias algo importante, actualiza este fichero y apunta el detalle en el
registro de `CLAUDE.md`.

## 1. Historia en cuatro líneas

- **6-11 oct.**: SD grabada, red sin router (169.254.42.1), web para mapear y calibrar los 12 servos, pantalla HDMI,
  registro de cortes de corriente. Una PCA9685 se quemó (cortocircuito al alimentar un NodeMCU desde la clema) y se
  cambió por la de repuesto. Intento de WiFi con NodeMCU por el puerto serie: **pendiente** (detalles en `CLAUDE.md`).
- **11 oct. noche**: se probaron las 8 combinaciones de sentido de las articulaciones; **la variante 6** es la buena.
- **12 oct.**: de pie que funciona (6.7), motor de marchas, 80+ rutinas, estrellas para valorarlas, rutinas por código
  para la tele, modo Toreto, barra de Hz con protección de calor, fuente de servos nueva de 20 A. Trabajaron dos
  sesiones de IA a la vez (una la web y los modos, otra la tele/HDMI y la potencia).

## 2. Qué funciona

| Cosa | Estado |
|---|---|
| Web completa (mapa, calibración, "Afinar patas", posturas, bloques, informe) | ✅ |
| Tele: panel de texto (`pantalla/panel.py`) y `/lite`; tecla P = de pie, rutinas por código, populares | ✅ |
| **De pie 6.7** (agachado 30, pectorales +5) con los ajustes de servo de Luis; botón grande "▲ De pie" | ✅ (la pata D a veces no llega arriba) |
| **Dar la pata sentado** (16, 47; también A 96-99) | ✅ éxito: sentado con el culo abajo no se cae |
| **Pedir el autobús** (120 B, 121 A, fucsia): sentado, pata arriba del todo, ti-ti-ti con el codo, saludo | nuevo, sin valorar |
| Marchas de la familia del **galope** | avanzan algo (3 cm la primera vez); la mejor, **Galope fuerte · ritmo (76) 5★** |
| Repetir sin parar, marchas de velocidad 1ª-6ª, estrellas 0-6, ranking | ✅ |
| Toreto (salto con patada) y barra de Hz 50-200 con calor estimado y vuelta sola a 50 Hz | ✅ (Toreto rehecho para que no tiemble) |

## 3. Qué falla o limita (por orden de importancia)

1. **La Pi se apaga de golpe** con las rutinas fuertes (13+ apagados; `energia.log` y la franja roja de la web). La Pi
   va por micro USB: necesita un cargador de 5,1 V / 2,5 A con cable corto y grueso, y el **GND de la clema bien
   apretado** (si se afloja, la corriente de los servos vuelve por la Pi). Cada apagón arriesga la SD.
2. **B3 (codo delantero derecho) tiene ajuste +50**: el horn está montado 3 dientes girado. En la de pie ya va a 170° y
   al recoger o levantar el pie llega a su tope (175°): la pata B levanta menos y arrastra. Arreglo mecánico: quitar el
   tornillo del horn, girarlo 3 dientes (MG996R: 25 dientes = 14,4° cada uno) y bajar el ajuste a ~+5. A3 (−15) es 1 diente.
3. **El codo de D se queda sin fuerza** al final de levantarse (las rutinas amarillas le dan un "toquecito" al final).
   Puede ser ese servo flojo; probar a cambiarlo por uno de los libres o a intercambiarlo con otro para ver si el fallo
   va con el servo o con la pata.
4. **Par justo**: los MG996R a 5 V van justos para este robot. Una fuente de **6 V** (sin pasar de 6,6) les da ~15 % más.
5. **Las marchas clásicas no avanzan** (paso a paso, trote, balanceo…): los pies arrastran y resbalan. Lo que avanza es
   el galope (fases D C A B, apoyo 45 %, ritmo) con zancada asimétrica.
6. El consumo de la web es una **estimación** (1,2 A moviéndose, 0,3 A sujetando), no una medida.

## 4. Lo aprendido con las estrellas (el "aprendizaje por refuerzo" a mano)

Luis valora cada rutina de 0 a 6 estrellas desde la web; quedan en `config.json` → `valoraciones`. Ranking a 12-10:

| ★ | Rutinas |
|---|---|
| 5 | Galope girando a la derecha (31), Galope plata a la derecha (12), **Galope fuerte · ritmo (76)**, Dar la pata B sentado 1 (16), 2 (47) y 3 (48) |
| 4 | Galope alto (30), Galope plata (11), Canguro plata (14), Galope fuerte · pisotón (78) |
| 3 | Galope largo/rápido, camellos, canguro, caballo de tiro atrás, de pie suave, camello plata, galope fuerte recto y a tope |
| 2 | Galope a la izquierda, ayudando a la D, galope fuerte zancada y más aire |
| 1 | Galope turbo, galope a la otra mano, caballo de tiro, dar la pata B sentado 4 |

Conclusiones que guían las rutinas nuevas:
- **Familia del galope** (orden D C A B, apoyo ~45 %) y **de pie alto** (agachado 22-30): lo que mejor anda.
- **Zancada asimétrica 18/6** (izquierdas largas, derechas cortas) va mejor que más larga (22/10: 2★) o más recta (20/14: 3★).
- **Pisar fuerte ayuda** (pisada 5-7, la "penetration depth" de spot_mini_mini) y el **ritmo rápido** (9 puntos por ciclo).
- **Más tiempo en el aire no ayuda** (más aire 2★, turbo 1★). Las velocidades 6ª turbo provocan apagones.
- **El peso va delante**: todo lo que levanta una pata delantera de pie se cae hacia delante. **Sentado** (traseras
  agachadas, peso atrás) funciona. "Cuanto más arriba la pata, más equilibrado" (Luis).
- Las de dar la pata: la 4 (culo abajo + cuerpo atrás + pata a tope) se cayó (1★); 1-3 bien.

Importante: muchas valoraciones se hicieron en **6ª turbo y con el límite de consumo quitado**; repetir las dudosas en 3ª.

## 5. Siguientes pasos propuestos

1. **Valorar las 6 de oro (101-106)**: cada una cambia una sola cosa respecto a la mejor (ritmo, 76): despegue limpio,
   empuje final, B con el hombro, pisotón, aire, y todas juntas. Con sus estrellas se sabe qué ayuda y se cruzan las mejores.
2. **Alimentación de la Pi** (cargador y GND) antes de más pruebas fuertes; volver a activar el límite de consumo.
3. **Recolocar el horn de B3** (y de A3) y reajustar en "Afinar patas" con el robot de pie.
4. Marchas: hacerlas **más largas y que giren** a voluntad (el galope gira a la derecha por la asimétrica): un "trim" de
   giro para ir recto, y luego un mando (adelante / izquierda / derecha) desde la tele.
5. Cuando haya margen de corriente: probar 70-100 Hz (zona "fuerte") con el galope ritmo y Toreto con patada 0-3.
6. Hardware deseable: **INA219** para medir la corriente de verdad; **ADS1115** si se quiere leer la posición real de
   los servos (cable del potenciómetro); WiFi (NodeMCU con esp_slip_router o un USB WiFi en modo punto de acceso).

## 6. A dónde va (la idea)

Un perro robot que se pone de pie, anda hacia delante de forma fiable, gira, da la pata y hace gracias, manejable
desde la tele con un mando sencillo (P = de pie, números para rutinas) y desde el móvil/PC. El método es empírico:
proponer variantes, probarlas en el robot, valorarlas con estrellas y cruzar las mejores.
