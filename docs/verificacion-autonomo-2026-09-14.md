# Carga y verificación del Mega autónomo

El 14 de septiembre de 2026 se cargó `respaw-autonomo` en el Mega conectado por
USB, con autorización del usuario. Sustituye a `respaw-v2` y ofrece actividades
guiadas y preferencias en EEPROM. La conversación libre de la web sigue
ejecutándose en la Mac. El Pico W conserva su firmware receptor anterior.

La fuente cargada corresponde al commit
`a65cd1bbdda3b62fb8fe2ae9011a35471f6a9587`, integrado en `main` mediante el
[PR #2](https://github.com/Duvet05/respaw/pull/2). La carga no modificó el código
que había pasado las 72 pruebas de `make check` y la compilación AVR con las
dependencias fijadas: 26.986 bytes de programa y 520 bytes de RAM estática.

## Identificación y respaldo

- Mega en `/dev/cu.usbmodem14301`: USB `2341:0042`, serie
  `55139313435351E0E1C1`, firma del ATmega2560 `1e9801`.
- Pico W detectado en `/dev/cu.usbmodem14201`: USB `2e8a:0005`, serie
  `e66368254f3e912e`. Su puerto no se abrió durante esta carga.
- Se detuvo el servidor local que estaba configurado para controlar el Mega
  antes de utilizar el puerto serie.
- Dos lecturas completas previas de flash, EEPROM, fusibles, lock y firma
  coincidieron. La flash de 262.144 bytes también coincidió con la última
  [carga verificada de `respaw-v2`](verificacion-pico-2026-09-14.md):
  `1ccd4c17c120f392794c0cf5a563160fcea5bdee460b2c3854e54a1dd01daf27`.

Las capturas y los registros de esta operación están fuera de Git, en
`~/Library/Application Support/ResPaw/hardware-backups/20260914/mega-autonomo-update/`.
Los respaldos originales del repositorio y la copia remota verificada antes
de la primera carga se conservaron.

## Carga y lectura independiente

Se utilizó `tools/arduino.sh upload --profile mega --verify`, con el puerto
explícito y `--input-file` apuntando únicamente a `respaw-autonomo.ino.hex`.
El cargador escribió y verificó los 26.986 bytes de aplicación. No se usó
la imagen que incluye el bootloader.

SHA-256 del HEX cargado:

```text
767f9a66be6d8810e6c21035b1b83e07b752d38f589cc5d3617350bd8d445a90
```

Una lectura completa posterior con `avrdude 8.2` confirmó todos los bytes
especificados por el HEX. También confirmó que el bootloader desde `0x3E000`,
la EEPROM de 4.096 bytes, los tres fusibles, lock y firma se conservaron.
La flash completa resultante tiene SHA-256
`ef0fffb565ac100bed747580f80b77a879878189c8848cf7bfecf210410aa7dd`.
El espacio de aplicación que no ocupa el HEX puede conservar páginas de la
versión anterior; la comprobación compara las direcciones de la nueva imagen.

## Pruebas del controlador por USB

La consola a 115200 baudios emitió `autonomous_ready` con
`host_required: false`. Con `DIAG ON`, se recorrieron las opciones mediante
gestos simulados, sin depender de una señal de presión en el pin A8:

- Las tres actividades completaron una sesión de invitado sin guardar.
  Se probaron terminar sin valoración, una valoración positiva y una negativa;
  en invitado ninguna de ellas persiste preferencias.
- La actividad configurada para 30 segundos terminó sin recibir comandos
  durante su ejecución. El estado de valoración se recibió por USB a los
  31,848 segundos desde la confirmación de inicio; es el tiempo observado
  por la consola, no una medición de precisión del temporizador interno.
- La despedida volvió automáticamente al inicio.
- `STOP` canceló una actividad del perfil 1 y volvió al inicio como invitado.
- La confirmación de olvido empezó en «No, volver» y permitió cancelar.
- `DIAG OFF` desactivó el diagnóstico. Un reinicio posterior confirmó el
  arranque en el estado inicial y con el diagnóstico desactivado.

Una lectura final confirmó que los 4.096 bytes de EEPROM seguían idénticos al
respaldo previo. No se guardaron ni borraron preferencias de los perfiles en
la placa durante las pruebas. Los casos de persistencia, corrupción y cortes
de escritura corresponden a las pruebas C++ ya documentadas en la guía.

El resumen está en `protocol-check.json`, la secuencia recibida en
`protocol-events.json` y las comparaciones de memoria en `before-check.json`
y `after-check.json`, dentro de la carpeta de respaldo. También se conservaron
los scripts usados para estas comprobaciones. El Mega recibió alimentación
por USB durante la prueba; no se comprobó físicamente una fuente independiente.

## Web y límites del montaje

La web se reinició en <http://127.0.0.1:8765> sin `--device`. Su API confirmó
el modelo local disponible, la memoria semántica disponible y
`robot.simulated: true`. Esta versión del Mega no utiliza el protocolo `V1`
de la web; para recuperar ese control habría que cargar otra vez `respaw-v2`.

La TFT sigue desconectada y el circuito del FSR en A8 no está comprobado.
Recibir estados por USB no prueba que los textos se vean ni que los gestos
funcionen con el sensor físico. Falta conectar y verificar ambos según el
montaje original. Esta versión autónoma no mide pulso ni usa audio.

El Pico W tiene Wi-Fi, pero su receptor instalado no implementa conexión a
un modelo cloud. Ese modo requiere ampliar su firmware y el enlace con el
Mega; no se añadió ni se probó en esta carga.
