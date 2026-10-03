# Verificación del servidor y gateway del Pico

Se retomó la arquitectura de la conversación compartida y se implementó el
primer tramo Pico ↔ servidor. Este informe diferencia las pruebas con el Pico
físico por USB de la conexión Wi-Fi, que requiere completar el setup.

## Servidor desplegado

Raspberry Pi 4 Model B Rev 1.2, 4 GB de RAM, Debian aarch64, Python 3.13.5.
Se desplegó un servicio WebSocket separado del companion, con identidad del
Pico `e66368254f3e912e`, Bearer y operador privado en otro puerto de loopback.
El servicio está activo y habilitado; `Linger=yes` permite mantenerlo tras cerrar
SSH. Los dos listeners se comprobaron en `127.0.0.1`, sin escuchar en toda la LAN.

Funnel publica solo el puerto del robot en 8443. Se conservaron sus rutas previas
de 443 y 2222. Se verificó el acceso usando la IP **pública** `209.177.145.97`,
obtenida mediante DNS público, evitando confundir MagicDNS con acceso por
Internet. Sin token respondió HTTP 401; con token completó TLS, `hello`,
`welcome` y un latido WebSocket. Ese cliente de prueba corrió en la Mac.

Las once pruebas del servidor pasaron también en la Raspberry. Su CLI informa
el estado actual y puede enviar FACE/PING/STOP/MEASURE; el gateway inicial
rechaza ejecutar esas órdenes sobre el Mega porque esa ruta todavía no existe.

## Pico físico y conservación de datos

Se confirmó por USB el Pico W `e66368254f3e912e`, MicroPython 1.26.1, puerto
`/dev/cu.usbmodem11101`. Antes de escribir, se respaldaron dos veces todos sus
archivos y coincidieron; `main.py`, `receiver.py` y `telemetry.py` eran idénticos
a la versión v2 del repositorio.

Se copiaron el repositorio previo y el respaldo lógico a la Raspberry, otro
medio, y se verificaron sus hashes. Las 44 entradas del `SHA256SUMS` original
pasaron en la copia remota. SHA-256 de los archivos de respaldo:

```text
repository-before.tar 141d61996d1770f7a44ef3aeda3d7879170a0c07db1102d4f9eba56ce1883986
pico-before.tar       801cd6c0f4c6b70d660a4f5f023dec7a75df63df069f03bf35e35e50b01592b4
```

Dos sesiones independientes recorrieron:

```text
Servidor Raspberry → WSS → Mac → USB → MicroPython del Pico
Pico valida FACE warm → codifica V1 <id> FACE warm\n → servidor confirma validated
Receiver del Pico procesa heartbeat sintético → servidor recibe source:synthetic
```

La prueba usó el código del protocolo en RAM y el receptor instalado. No escribió
flash, no transmitió por UART y no pretendió comprobar el cableado del Mega.
Los tres archivos del dispositivo siguieron idénticos después de las dos sesiones.
El informe se conservó fuera del repositorio en
`/tmp/respaw-pico-link-physical-20261003.json`.

Se corrigió además el transporte de mantenimiento: la REPL cruda del RP2040
perdía el final de programas largos enviados en un bloque. El emisor ahora
espacia bloques de 128 bytes y completa escrituras parciales. Las pruebas cubren
esa condición y un puerto que no admite escritura. En el Pico, tras recoger
basura se observaron 195.296 bytes libres y se cargó la CA X2 con
`SSLContext`/`CERT_REQUIRED`.

## Instalación del gateway

El instalador creó un segundo respaldo externo y leyó de vuelta cada archivo
escrito. Conservó byte por byte `receiver.py`, `telemetry.py` y `max30102/`.
Instaló los módulos del gateway y la CA, comprobó sus importaciones, el contexto
TLS y el formulario en el Pico real, y activó `main.py` al final. MicroPython
no se reemplazó.

El informe de instalación tiene `complete:true`, `tls_preflight:true` y
`receiver_preserved:true`. Está en
`/tmp/respaw-gateway-install-20261003/install-report.json`; la configuración y
la clave de setup están en un archivo privado separado y no se añaden a Git.

El siguiente paso físico es conectar el celular a `ResPaw-Setup-912E`, abrir
`http://192.168.4.1`, guardar la red de 2,4 GHz y observar `gateway_connected`
en el Pico junto con `connected:true, transport:wifi` en la Raspberry.

## Verificación automática y límites

La revisión incluye pruebas del cliente MicroPython contra sockets reales del
servidor: autenticación, dos conexiones, latidos y rechazo de acciones físicas
con `mega_unavailable`. Cubre además límites de tramas antes de reservar su
payload, Ping/Pong, handshake, formulario móvil y ausencia de secretos en la
página. El cliente de esas pruebas corre en CPython; no equivale a TLS/Wi-Fi
de la placa física.

Quedan pendientes: configuración Wi-Fi por el usuario, conexión WSS física,
reinicio frío/NTP, caída de router y reconexión real. DNS y NTP usan mecanismos
síncronos de MicroPython que pueden retrasar el procesamiento UART durante un
fallo; no se promete lectura ininterrumpida en ese caso.

El Mega no está conectado en estas pruebas. Sigue pendiente habilitar su parser
de comandos en `Serial1`, verificar cableado y TFT, publicar eventos de contacto
y unir este servicio al companion. No se instaló un proveedor de IA en nube ni
se probó conversación o voz a través del Pico.
