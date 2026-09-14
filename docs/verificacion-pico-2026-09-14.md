# Receptor Pico instalado y Mega actualizado

El usuario pidió completar el receptor del Pico y confirmó que las placas
estaban conectadas solamente por USB a la Mac, sin cables entre sus pines.
Se instalaron `main.py`, `receiver.py` y `telemetry.py` en el Pico W, conservando
MicroPython 1.26.1 y la librería original `max30102/`. Se actualizó además el
Mega para enviar telemetría por `Serial1` manteniendo el control USB de la Mac.

## Respaldo y verificación de la instalación

Pico identificado por `machine.unique_id`: `e66368254f3e912e`, puerto
`/dev/cu.usbmodem14201`. Antes de escribir se leyeron dos veces sus 2.097.152
bytes de flash. Ambas lecturas coincidieron con la imagen original del
repositorio y con una copia descargada del commit remoto
`0c99c28919fc8241d2fad3d26922c3f54006a9f8` de GitHub.

SHA-256 de la flash previa:

```text
7513edbca76064fd74171e2c441feb1b1404457f4a752c0d901dfe71bae2da4c
```

El instalador volvió a respaldar los archivos visibles antes de escribir.
Cada módulo nuevo se leyó de la placa y se comparó byte por byte con el código
local, antes y después de activar su nombre definitivo. `main.py` se instaló
al final. Las dos fuentes originales de `max30102/` siguieron coincidiendo
con su copia recuperada.

Los respaldos e informes se guardaron fuera del repositorio, bajo
`~/Library/Application Support/ResPaw/hardware-backups/20260914/`:

- `pico-before/`: dos lecturas de flash, copia remota y respaldo lógico previo.
- `pico-install-v2/`: respaldo adicional, manifiesto de instalación y pruebas
  del UART y del arranque.
- `mega-pico-update/`: carga del Mega, lectura posterior y prueba del protocolo USB.

## Prueba en el Pico físico

Se habilitó temporalmente el bucle interno de diagnóstico del UART0, a 115200
baudios. Los datos atravesaron el transmisor, receptor y FIFO reales del UART,
sin conectar cables externos. La función está documentada en la sección
4.2.3.2.6 del [datasheet de RP2040](https://pip.raspberrypi.com/documents/RP-008371-DS).

Se compilaron las funciones C++ de producción que serializan los mensajes del
Mega y se enviaron sus cinco mensajes sintéticos a ese UART: arranque, anuncio
de captura, una medición válida y dos inválidas. El programa del Pico aceptó
los cinco, preservó los valores esperados y omitió valores fisiológicos de
los resultados inválidos. Rechazó tres entradas defectuosas: línea demasiado
larga, versión booleana y paquete antiguo con NaN.

La prueba terminó con el bucle interno desactivado. La placa tenía 151.264
bytes de heap libre en esa comprobación; los modos Wi-Fi estación y punto de
acceso estaban inactivos. Se reinició el programa y se observó:

```json
{"v":1,"type":"pico_ready","board":"pico_w","uart":0,"rx_gpio":1,"baud":115200}
```

Tres resúmenes sucesivos confirmaron que quedó esperando UART, con
`connected:false`, cero mensajes recibidos y sin lecturas ficticias retenidas.

## Mega actualizado

El perfil fijado `mega` produjo un programa de 32.934 bytes y 1.165 bytes de RAM
estática, dejando 7.027 bytes de RAM disponibles. Se cargó solo la aplicación
por el bootloader y se verificaron sus bytes. Una lectura completa independiente
confirmó que el programa coincide con el HEX y que bootloader, EEPROM,
fusibles, lock y firma quedaron iguales a los valores anteriores.

SHA-256 del HEX de esta actualización:

```text
002c6afe2cf560ce8ef9b1711c211a04841b4d8718bb11c92b3cc711b6a3e05f
```

Después de la carga se volvieron a verificar PING, STOP, las cinco expresiones,
el rechazo de una expresión desconocida y la recuperación tras el timeout del
host. El Mega siguió informando `sensor:false` y `audio:false` en este montaje.
La pantalla tampoco estaba conectada, según confirmó el usuario previamente.

## Software y límites

`make check` pasó las 71 pruebas: incluye 12 nuevas para el Pico, integración
entre serializador C++ y receptor Python, sanitizadores, HTTP local y sintaxis
JavaScript. Las pruebas cubren fragmentación, límites de línea, valores no
finitos, calidad insuficiente, compatibilidad antigua sin diagnóstico emocional,
caducidad, reconexión, reinicios y vuelta del contador de tiempo.

La prueba interna del Pico no verifica la señal que sale del pin D18 del Mega
ni el cableado entre placas. Esa comprobación queda pendiente del montaje con
adaptación de 5 V a 3,3 V. Tampoco se validó una señal de pulso real ni audio
del DFPlayer. Las instrucciones y los pines están en la
[guía del receptor](../pico/source/respaw-v2/README.md).

La web sigue usando el USB del Mega; los mensajes de diagnóstico del Pico se
consultan en una consola aparte. La Mac ejecuta la conversación y conserva los
recuerdos. El Pico guarda las lecturas recibidas solamente en RAM.
