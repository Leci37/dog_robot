LUIS LECI control · AYUDA RÁPIDA (robot SpotMicro)
==================================================
(También en la web, sección "Sistema", en la pantalla HDMI con la tecla H, y por SSH: cat ~/spotmicro/AYUDA.md)

Nombres: patas vistas desde arriba con la cabeza delante: A delantera izq · B delantera der ·
C trasera der · D trasera izq. Articulaciones, del cuerpo al suelo: 1 pectoral (abre/cierra la pata
hacia el lado) · 2 hombro (pata adelante/atrás) · 3 codo (dobla la parte de abajo). Ej.: B3 = codo de B.

1. CÓMO CONECTARSE
------------------
A) Con router (Pi y PC enchufados por cable al router, hay internet)
   Navegador:  http://192.168.8.104   (o la IP que muestre la pantalla HDMI)
   SSH:        ssh luislecinana@192.168.8.104

B) Cable de red directo entre la Pi y el PC (sin router, sin internet)
   1. Enciende la Pi y espera 2-3 minutos.
   2. El adaptador de red del PC debe estar en "Obtener IP automáticamente" (lo normal).
      Sin router, Windows se pone él solo una dirección 169.254.x.x al cabo de un minuto o así.
   3. Navegador:  http://169.254.42.1
      SSH:        ssh luislecinana@169.254.42.1
   Si no carga:
   - Desenchufa y vuelve a enchufar el cable en el PC, espera 1 minuto y recarga.
   - Desconecta la VPN del PC si la tienes (puede bloquear la red local).
   - Último recurso, por IPv6 (funciona siempre que haya cable):
       En PowerShell:  Get-NetAdapter       (apunta el número ifIndex del adaptador Ethernet)
       ssh -L 8080:localhost:8080 luislecinana@fe80::ba27:ebff:fe16:ec15%NUMERO
       y, con esa ventana abierta, en el navegador del PC:  http://localhost:8080

   Usuario: luislecinana. El PC de sobremesa y el portátil entran por SSH con su clave, sin contraseña.
   Si la pide, es la contraseña del usuario de la Pi (apuntada en ~/spotmicro/NOTAS-LOCALES.md, que no
   se sube a GitHub). La pestaña "estado" (arriba a la derecha de la web) muestra cómo estás conectado.
   Por SSH también se puede usar la web: el truco "ssh -L 8080:localhost:8080 ..." sirve con
   cualquier dirección (192.168.8.104, 169.254.42.1 o la IPv6).

2. PANTALLA HDMI
----------------
- Puedes enchufar la tele antes o después de encender: el HDMI está forzado a 1280x720.
- Al arrancar sale un menú:  [1] panel del robot   [2] página web en la pantalla   [3] consola normal.
  Sin teclado, a los 15 s entra solo en la opción por defecto (se cambia en la web: Sistema > Pantalla HDMI).
- El panel muestra cómo conectarse, la alimentación, las rutinas guardadas y estas teclas (teclado USB):
    S soltar todo   M bloquear a mitad (90°)   R recoger   E estirar   1-9 ejecutar rutina   X parar rutina
    W web en la pantalla   C consola normal (en vez del panel)   T consola aparte (Alt+F1 vuelve al panel)
    H ayuda   A apagar   B reiniciar  (A y B piden confirmación)
- Consola en cualquier momento: Ctrl+Alt+F2 (login aparte) y Ctrl+Alt+F1 para volver al panel.
- La página web en la tele necesita ratón, va lenta en una Pi 2 y gasta más corriente.
  Para volver al panel: Alt+F4, o el botón "Panel" en la web del PC.
- Desde la web del PC (Sistema > Pantalla HDMI) eliges qué se ve en la tele: panel, web o consola.
- La página web en la tele necesita el navegador (cage + chromium). Si no está instalado, en
  Sistema > Pantalla HDMI sale el botón "Instalar / continuar el navegador" (hace falta router con
  internet; con 4G lento tarda bastante, pero sigue solo y se reanuda si se corta).

3. ALIMENTACIÓN (casas con cortes o cargador flojo)
---------------------------------------------------
- Pi: 5 V y 2,5 A por micro USB, con cable corto y grueso.
- Servos: fuente aparte de 5-6 V y 10 A o más, en la clema V+/GND de la PCA9685. NUNCA el cable Schuko.
- No cambies conectores de servos con todo encendido: un cortocircuito puede apagar el cargador
  de la Pi y dejarla apagada hasta que lo desenchufes.
- LÍMITE DE CONSUMO (arriba del todo en la web): casilla "Limitar el consumo" + amperios de la fuente de
  los servos (ahora 5 A). El robot reparte los movimientos en tandas para no pasar de esa corriente
  (~1,2 A por servo moviéndose, ~0,3 A sujetando): con 5 A se mueven de uno en uno; con 15-20 A, todos a la
  vez. Cuando tengas una fuente grande, sube el deslizador. Quitar el límite solo con fuente grande.
- Antes de desenchufar: botón "Apagar la Pi" (web o tecla A en la pantalla) y espera unos 20 s,
  hasta que el LED verde deje de parpadear.
- Indicador de alimentación (web y pantalla):
    OK                     todo bien
    HUBO CAÍDAS            desde que arrancó hubo momentos de falta de corriente
    FALTA CORRIENTE AHORA  el cargador o el cable no dan suficiente: cámbialos
- Registro permanente de arranques y cortes: ~/spotmicro/energia.log (también en la web).
  "terminó DE GOLPE" = se cortó la corriente, se colgó o se desenchufó sin apagar.
- Si la Pi se cuelga, el watchdog la reinicia sola en un minuto.
- Vigilante (cada minuto, solo): si falta la dirección 169.254.42.1 la vuelve a poner (o rehace el perfil
  de red), si la web no responde la reinicia, si el panel HDMI falla pone la consola normal para que la tele
  nunca quede en negro, y si el LED de vida se para lo arranca. Lo que arregla queda en ~/spotmicro/vigilante.log.
- LED verde de la Pi:
    corto, corto, largo (· · —) en bucle  = todo bien, el programa del robot está vivo
    parpadeo rápido continuo              = el programa no responde (arrancando o caído)
    parpadeo irregular                    = la Pi está arrancando o apagándose
    quieto (encendido o apagado)          = colgada: el watchdog la reinicia en ~1 minuto
    LED rojo apagado                      = no le llega corriente
- LED de vida en la placa de servos: el LED rojo de la PCA9685 va directo a la corriente y no se puede
  controlar. Pincha un LED con resistencia de 220-470 Ω en un canal libre (7, 8 o 9): pata larga (+) con la
  resistencia al pin de señal, pata corta (-) a GND; actívalo en Sistema > "LED de vida". Hace el mismo
  corto-corto-largo a la vez que el LED verde de la Pi. Nunca con un servo en ese canal.
- El punto de la cabecera de la web también hace corto-corto-largo mientras estás conectado.

4. RUTINAS Y BLOQUES (para que ande)
------------------------------------
- Web > "Rutinas para probar". Paso 1: la buena es la "De pie 6" (encontrada el 11-10). Prueba la 6 y
  sus variantes (6.1 baja, 6.2 alta, 6.3 y 6.4 inclinadas, 6.5 y 6.6 pectorales) y marca ✓ la mejor.
  Cada una recoge las 4 patas y se levanta con las 4 a la vez, muy despacio. Entre prueba y prueba,
  "Bajar despacio".
- "Afinar patas" (en "Rutinas para probar"): el ajuste de cada uno de los 12 servos con ±1/±5. Con el robot
  de pie, compara cada pata con su pareja (A con B, D con C) y ajusta hasta que queden iguales: vale para
  todas las posturas y rutinas. Ajustes puestos: A3 -15, B3 +45, C1 -5, D3 +10.
  Al marcar ✓ se guardan las posturas "de pie", "estirada" y "recogida", y aparecen las rutinas. Primero
  las 5 de andar: paso a paso, con balanceo, trote lento, hacia atrás y paseo (adelante, gira, adelante,
  gira). Luego girar a izquierda/derecha, marcar el paso, de pie alto/bajo, tumbarse, levantarse, sentarse,
  reverencia, flexiones, balanceo y saludar. Las de pie siguen debajo: marcar ✓ otra cambia la que usan todas.
  Marca ✓/✗ cada una y guarda las buenas en "Mis rutinas".
- Botón verde grande "▲ De pie" arriba, junto a "Soltar todo": vuelve a la de pie elegida (✓, ahora la 6.7
  con los ajustes); si hay una rutina en marcha, la para antes.
- Barra de botones arriba del todo: las 5 de pie mejores (6.7, 6, 6.1, 6.2, 6.5; la elegida en azul con ✓),
  Bajar despacio, todas las de andar, la velocidad y Parar.
- Andar: paso a paso, con balanceo, rápido, alto, alto y rápido, pasitos rápidos, paso largo, trote lento,
  trote rápido, trote alto, de lado, hacia atrás y paseo. Las "altas" van con las patas más rectas (el codo
  hace menos fuerza) y las "rápidas" suben y avanzan el pie de una vez.
- Rutinas verdes (fila "Nuevas" arriba): variantes del galope y del camello (las que avanzaron), caballo de
  tiro, de lado, balanceo potente, dar la pata estable, pis, desperezarse, menear y baile. "Creativas": aires
  del caballo (paso, tölt, ambladura, trote elíptico, piaffe, passage, galopes, brinco, bote).
- De pie suaves (fila amarilla arriba): escalones, ayudando a la D, en dos tiempos, y "De pie de Iván" (rosa:
  delanteras y traseras a ratos, nunca las 4 a la vez). Todas acaban con un toquecito a la pata D.
- Velocidad de las rutinas: marchas 1ª muy lenta, 2ª lenta, 3ª normal, 4ª rápida, 5ª muy rápida. Botones en
  "Rutinas para probar" y, con una rutina en marcha, al lado de "Parar rutina": se nota en el paso siguiente.
- Web > "Programar con bloques": como Scratch. Bloques de mover (pata/articulación/grados, relativo,
  pata entera), posturas, todo a mitad, soltar, esperar, velocidad, repetir y ejecutar rutina.
  Los ángulos son los de la pata D; las demás hacen el mismo gesto. Guarda el programa con un nombre.
- Cualquier rutina se para con "Parar rutina" (arriba), "Soltar todo" o la tecla X/S de la pantalla.
- En el suelo: primero sujetando el robot con la mano. El codo de A está ahora en el canal 1.

5. INFORME PARA LA MUDANZA
--------------------------
- Web > Sistema > "Informe para la mudanza" > Comprobar ahora: revisa red, alimentación, servicios,
  SSH, SD, placa de servos, mapa, rutinas y copias. Verde = bien, amarillo = aviso, rojo = fallo.

5a. PROTECCIONES (para que nadie apague la Pi pulsando botones)
---------------------------------------------------------------
- Falta de corriente: desde el 11-10-2026 16:45 solo AVISA (aviso rojo) y deja mover (lo pidió el usuario). Si se desmarca la casilla "Permitir mover aunque falte corriente", vuelve a frenar y NO mueve servos (pasó 5
  veces el 11-10-2026 que se apagaba al moverlos). Se puede permitir con la casilla de la barra de arriba.
- Un movimiento cada vez: si llega otra orden mientras el robot se mueve, se descarta (no se encola).
- Avisos abajo a la derecha: falta corriente, consumo alto, muchos botones a la vez, rutina en marcha.
- Cabecera: mini esquema de consumo en tiempo real (filas A B C D · pectoral hombro codo; gris suelto,
  ámbar sujetando ~0,3 A, rojo moviéndose ~1,2 A) y el total estimado frente al límite.
- BLOQUEAR TODO DONDE ESTÁ (botón junto a "Bloquear todo a mitad", y al guardar una postura con servos sueltos):
  sujeta cada servo en el último ángulo que le mandó la Pi y lo apunta en la memoria. Si la fuente da, todos a la vez;
  si no, uno a uno cada 0,3 / 0,4 / 0,5 s (se elige en "Mis posturas") con una barra arriba: "Bloqueando A2 hombro… 3/12"
  y un botón Parar. Si la fuente no da ni para tenerlos todos sujetos (o marcas "soltar cada uno después"), bloquea,
  apunta y suelta cada uno. Al terminar, "Guardar postura" guarda las 12. Ojo: si moviste a mano una pata suelta,
  salta hasta la posición apuntada (robot en el aire o bien apoyado).
- Memoria de posición: la Pi guarda en la SD el último ángulo de cada servo (sobrevive a cortes).
  "Mis posturas" puede guardar con esa memoria, sujetar los sueltos donde estaban, o copiar del robot
  moviendo solo el dibujo en "El robot en vivo" (botón "Copiar del robot", no mueve los servos).
- Tabla del mapa: filas en rojo/ámbar/verde y, al pasar el ratón, qué falla y cómo comprobarlo. Si cambias un servo
  de conector, pon su pata/articulación en el canal nuevo: el viejo queda libre ("antes A3") y su ajuste se va con él.
- Franja de apagados de golpe arriba: cuántas veces se apagó la Pi sin avisar y si fue por falta de corriente.
- Botones ±5 / ±15 / ±30 en canales y grupos. En la pantalla HDMI: ←→ 5°, RePág/AvPág 15°, Inicio/Fin 30°.
- La página web en la tele abre la versión ligera (http://<ip>/lite): mismas cosas básicas, sin bloques, más rápida.

5b. LÍMITE DE CONSUMO Y ESQUEMA DE CONEXIONES
---------------------------------------------
- Arriba del todo en la web: casilla "Limitar el consumo de los servos a la fuente" y deslizador con los
  amperios de la fuente (ahora 5 A). Con el límite, los movimientos se hacen en tandas para no pasar de
  esos amperios (con 5 A, de uno en uno). Con una fuente de 15-20 A, sube el deslizador.
- Esquema de conexiones (Pi, placa, fuentes y los 12 servos con su canal y avisos): al final de la web,
  en http://<ip>/static/esquema.svg y en web/static/esquema.svg del proyecto.
- "El robot en vivo" (al final de la web): dibujo desde arriba (qué pata es cada una, sensores, Pi, placa y
  canales) y las 4 patas de lado tal como cree la Pi que están, en tiempo real. En cada pata: "¿Está así de
  verdad? ✓/✗". Si no, "pectoral/hombro/codo al revés" (la Pi invierte ese servo sin moverlo). Se puede
  arrastrar la rodilla (mueve el hombro) o la pezuña (mueve el codo), y usar ±5, probar y fijar 90°.
- Partes del robot (como en el dibujo del SpotMicro): delante, 2 sensores de ultrasonidos HC-SR04 (los 4
  "ojos"); en cada esquina el pectoral; la pieza grande redondeada es el muslo (hombro); la fina, la parte
  de abajo (codo o rodilla). Dentro: Raspberry Pi y PCA9685.

6. SI ALGO VA MAL
-----------------
- Los servos hacen cosas raras: "Soltar todo" (botón rojo de la web, o tecla S en la pantalla).
- La Pi se apaga o reinicia sola (ya pasó 2 veces el 11-10-2026): casi siempre es la alimentación.
  Cargador oficial de 5,1 V 2,5 A y cable corto; mira energia.log ("terminó DE GOLPE").
- La Pi no aparece en la red: mira las luces.
    roja fija = le llega corriente · verde parpadeando = arrancando o trabajando
    verde fija desde el principio = no lee la SD · roja apagada o parpadeando = falta corriente
  Desenchufa 10 s (con la fuente de los servos desconectada) y vuelve a enchufar.
- La web no carga pero SSH sí:   sudo systemctl restart spotmicro-web
- Qué ha pasado:   cat ~/spotmicro/energia.log      journalctl -b -1   (arranque anterior)
- Robot: siempre en el aire para probar, movimientos pequeños, y "Soltar todo" al terminar.
