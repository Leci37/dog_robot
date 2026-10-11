SPOTMICRO · AYUDA RÁPIDA
========================
(También en la web, sección "Sistema", en la pantalla HDMI con la tecla H, y por SSH: cat ~/spotmicro/AYUDA.md)

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

   Usuario: luislecinana. El PC de sobremesa y el portátil (clave llecinana@ecna.es) entran por SSH
   con su clave, sin contraseña. Si la pide, es la misma que el usuario.
   Por SSH también se puede usar la web: el truco "ssh -L 8080:localhost:8080 ..." sirve con
   cualquier dirección (192.168.8.104, 169.254.42.1 o la IPv6).

2. PANTALLA HDMI
----------------
- Puedes enchufar la tele antes o después de encender: el HDMI está forzado a 1280x720.
- Al arrancar sale un menú:  [1] panel del robot   [2] página web en la pantalla   [3] consola normal.
  Sin teclado, a los 15 s entra solo en la opción por defecto (se cambia en la web: Sistema > Pantalla HDMI).
- El panel muestra cómo conectarse, la alimentación y estas teclas (teclado USB):
    S soltar todo   M bloquear a mitad (90°)   R recoger   E estirar
    W web en la pantalla   C consola normal   H ayuda   A apagar   B reiniciar  (A y B piden confirmación)
- La página web en la tele necesita ratón, va lenta en una Pi 2 y gasta más corriente.
  Para volver al panel: Alt+F4, o el botón "Panel" en la web del PC.
- Desde la web del PC (Sistema > Pantalla HDMI) eliges qué se ve en la tele: panel, web o consola.

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

4. SI ALGO VA MAL
-----------------
- Los servos hacen cosas raras: "Soltar todo" (botón rojo de la web, o tecla S en la pantalla).
- La Pi no aparece en la red: mira las luces.
    roja fija = le llega corriente · verde parpadeando = arrancando o trabajando
    verde fija desde el principio = no lee la SD · roja apagada o parpadeando = falta corriente
  Desenchufa 10 s (con la fuente de los servos desconectada) y vuelve a enchufar.
- La web no carga pero SSH sí:   sudo systemctl restart spotmicro-web
- Qué ha pasado:   cat ~/spotmicro/energia.log      journalctl -b -1   (arranque anterior)
- Robot: siempre en el aire para probar, movimientos pequeños, y "Soltar todo" al terminar.
