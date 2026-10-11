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
- Antes de desenchufar: botón "Apagar la Pi" (web o tecla A en la pantalla) y espera unos 20 s,
  hasta que el LED verde deje de parpadear.
- Indicador de alimentación (web y pantalla):
    OK                     todo bien
    HUBO CAÍDAS            desde que arrancó hubo momentos de falta de corriente
    FALTA CORRIENTE AHORA  el cargador o el cable no dan suficiente: cámbialos
- Registro permanente de arranques y cortes: ~/spotmicro/energia.log (también en la web).
  "terminó DE GOLPE" = se cortó la corriente, se colgó o se desenchufó sin apagar.
- Si la Pi se cuelga, el watchdog la reinicia sola en un minuto.
- LED verde de la Pi:
    corto, corto, largo (· · —) en bucle  = todo bien, el programa del robot está vivo
    parpadeo rápido continuo              = el programa no responde (arrancando o caído)
    parpadeo irregular                    = la Pi está arrancando o apagándose
    quieto (encendido o apagado)          = colgada: el watchdog la reinicia en ~1 minuto
    LED rojo apagado                      = no le llega corriente

4. RUTINAS Y BLOQUES (para que ande)
------------------------------------
- Web > "Rutinas para probar". Paso 1: prueba las 8 variantes de "De pie" (robot en el aire) y marca
  ✓ la que deja las 4 patas iguales. Eso guarda el sentido de las articulaciones y las posturas
  "de pie", "estirada" y "recogida", y aparecen ~15 rutinas: de pie alto/bajo, tumbarse, levantarse,
  sentarse, reverencia, flexiones, balanceo, saludar, marcar el paso, andar (corto/largo/atrás), girar.
  Marca ✓/✗ cada una y guarda las buenas en "Mis rutinas".
- Web > "Programar con bloques": como Scratch. Bloques de mover (pata/articulación/grados, relativo,
  pata entera), posturas, todo a mitad, soltar, esperar, velocidad, repetir y ejecutar rutina.
  Los ángulos son los de la pata D; las demás hacen el mismo gesto. Guarda el programa con un nombre.
- Cualquier rutina se para con "Parar rutina" (arriba), "Soltar todo" o la tecla X/S de la pantalla.
- En el suelo: primero sujetando el robot con la mano. El codo de A (canal 0) no responde.

5. INFORME PARA LA MUDANZA
--------------------------
- Web > Sistema > "Informe para la mudanza" > Comprobar ahora: revisa red, alimentación, servicios,
  SSH, SD, placa de servos, mapa, rutinas y copias. Verde = bien, amarillo = aviso, rojo = fallo.

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
