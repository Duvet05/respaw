# Carga y verificación del Mega por USB

El 14 de septiembre de 2026 se cargó `respaw-v2` en el Arduino Mega recuperado,
con autorización del usuario. La aplicación local quedó ejecutándose con ese
dispositivo por USB. El Pico permaneció sin modificaciones.

Este informe corresponde a la primera carga del Mega. La actualización
posterior que completa el receptor del Pico y el emisor del Mega está en
[verificación del Pico](verificacion-pico-2026-09-14.md).

## Función de cada placa

La pantalla TFT de 3,5 pulgadas corresponde al Mega. El sketch original
`tesis-mega2560.ino`, en `enviarDatosPico()`, envía BPM, SDNN, RMSSD y una
etiqueta de estado por `Serial1`: esto indica que el Pico debía recibir esos
resultados. En la recuperación del Pico no apareció el receptor ni un programa
de arranque; el script borrado encontrado solo prueba el MAX30102. El
[contraste con la tesis](comparacion-tesis-firmware.md) documenta esa diferencia.

En la versión actual, la Mac ejecuta conversación, memoria y voz; el Mega recibe
órdenes USB para la pantalla y sus periféricos. El Pico no participa en este
recorrido. Usarlo después como puente inalámbrico sería trabajo nuevo.

## Identificación y respaldo

- Mega: `/dev/cu.usbmodem14301`, USB `2341:0042`, serie
  `55139313435351E0E1C1`, firma ATmega2560 `1e9801`.
- Pico: `/dev/cu.usbmodem14201`, USB `2e8a:0005`; identificado sin abrir su
  puerto serie ni escribir su memoria.
- Los 44 archivos del manifiesto original `SHA256SUMS` pasaron la comprobación
  local. Se descargaron el manifiesto, la flash completa, la aplicación HEX y
  la EEPROM del Mega desde el commit remoto
  `0c99c28919fc8241d2fad3d26922c3f54006a9f8` de GitHub y se compararon con las
  copias locales. Así se verificó una copia fuera de esta Mac antes de cargar.
- Dos lecturas nuevas de la flash física, de 262.144 bytes cada una,
  coincidieron entre sí y con ese respaldo original. También se conservaron
  EEPROM, fusibles, lock y firma anteriores a la carga.

El respaldo adicional, los registros de herramientas y los informes JSON están
fuera de Git, en
`~/Library/Application Support/ResPaw/hardware-backups/20260914/`.
Los archivos de recuperación del repositorio se conservaron sin cambios.

## Compilación y lectura posterior

Se usó el perfil fijado `mega` mediante `tools/arduino.sh`: 31.868 bytes de
programa y 1.004 bytes de RAM estática. Se cargó únicamente
`respaw-v2.ino.hex`, con verificación del cargador de Arduino; no se seleccionó
la imagen que incluye bootloader.

SHA-256 del HEX cargado:

```text
abbf7f76746a54c373525b7fb0d81a376398e884d23f1c9ae3372c616df2ad38
```

Una lectura completa independiente con `avrdude 8.2` confirmó los 31.868 bytes
especificados por el HEX. La región del bootloader desde `0x3E000`, la EEPROM,
los tres fusibles, lock y firma coincidieron con sus valores anteriores.
No se exige que el espacio de aplicación sin usar quede borrado: el cargador
puede conservar páginas de la aplicación anterior fuera de la nueva imagen.

## Pruebas con la placa y la web

El Mega emitió este evento de arranque:

```json
{"v":1,"type":"ready","board":"mega2560","sensor":false,"audio":false}
```

Se recibieron confirmaciones para PING, STOP y las cinco expresiones admitidas
por FACE. Una expresión desconocida devolvió `bad_face`. Al interrumpir los
PING se observó `host_timeout`; al reanudarlos el Mega volvió a confirmar las
órdenes. No se iniciaron reproducciones de audio ni mediciones fisiológicas.

Después se inició el servidor local con `pyserial 3.5` y el puerto del Mega.
Una prueba de Chrome con una sesión de invitado y un mensaje ficticio comprobó:

- Modelo de conversación y memoria semántica disponibles localmente.
- Estado del robot `simulated:false`, `ready:true`, sin error.
- Respuesta real de Qwen3 en 8.032 ms, con expresión `warm` enviada al robot.
- Veintitrés muestras de estado durante siete segundos sin errores USB.
- Botón **Detener** y estado posterior `neutral`.
- Ningún error JavaScript ni solicitud de red a destinos externos.

La captura de esa prueba está en
`~/Scratch/respaw-review-20260914/mega-usb-web.png`.
La suite de software previa había pasado 59 pruebas; esta carga no cambió el
código probado. Las comprobaciones adicionales fueron sobre flash, protocolo
serie y navegador con el dispositivo real.

## Alcance de la comprobación física

El usuario confirmó que la pantalla no estaba conectada durante la prueba.
Las confirmaciones USB prueban que el Mega ejecutó las órdenes de dibujo;
la comprobación visual queda pendiente de conectar la TFT. El evento
de arranque indica que no pudo inicializar el MAX30102 ni el DFPlayer; esto
no permite distinguir entre periféricos ausentes, alimentación o cableado.
Quedan pendientes la inspección del montaje y las pruebas de señal y audio.
La voz de conversación disponible corresponde a la Mac.

Para volver a iniciar la web con este Mega, con Ollama ya activo, ejecutar
desde la raíz de este checkout:

```sh
PYTHONPATH=companion uv run --offline --with pyserial==3.5 python -m respaw --device /dev/cu.usbmodem14301
```

Abrir <http://127.0.0.1:8765> y recargar si la página estaba abierta antes del
reinicio. `make run` sigue iniciando el simulador por defecto. El puerto USB
puede cambiar al reconectar; se identifica con `sh tools/arduino.sh board list`.
