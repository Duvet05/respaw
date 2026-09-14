# ResPaw Mega v2

Firmware nuevo para Arduino Mega 2560. Mantiene la TFT y los periféricos del
sketch recuperado, mientras la conversación y la memoria corren en la Mac.
El código original y las imágenes de recuperación se conservan aparte.

## Compilar

Desde la raíz del repositorio:

```sh
python3 tools/setup_local.py --arduino  # Instalación opcional en Mac Apple Silicon
make firmware
```

`sketch.yaml` fija la plataforma AVR 1.8.6 y las cinco librerías. La primera
compilación las descarga; las siguientes reutilizan la caché de ResPaw fuera
del repositorio. `tools/arduino.sh` admite una instalación propia mediante
`ARDUINO_CLI` y otra ubicación de datos mediante `RESPAW_DATA_DIR`.

Este comando no carga el sketch. Antes de cargar una placa, seguir la
instrucción de respaldo del README de recuperación y verificar el cableado
real. El 14 de septiembre de 2026 se cargó esta versión en el Mega recuperado:
la lectura posterior coincidió con el programa compilado y se verificaron las
órdenes USB. El sensor de pulso y el DFPlayer no pudieron inicializarse en ese
montaje. El [informe de hardware](../../../docs/verificacion-hardware-2026-09-14.md)
detalla las comprobaciones y la validación física pendiente.

## Hardware conservado

| Componente | Conexión del sketch recuperado |
| --- | --- |
| TFT de 3,5 pulgadas | Shield paralelo, MCUFRIEND_kbv / Adafruit GFX |
| FSR | A8, contacto por encima de 300 |
| MAX30102 | I²C del Mega; 100 Hz explícitos, promedio FIFO de 1 muestra |
| DFPlayer RX/TX del Mega | SoftwareSerial A15 / A14, 9600 baudios |
| DFPlayer BUSY | A13 con pull-up; ocupado en LOW |
| Mac | Serial USB, 115200 baudios |
| Pico W opcional | Serial1 TX1/D18 a GP1 del Pico, mediante adaptación de 5 V a 3,3 V |

El umbral del FSR y el detector PPG son parámetros iniciales del prototipo,
pendientes de calibración con el montaje. No se controla un servo o una IMU.

El [receptor del Pico](../../../pico/source/respaw-v2/README.md) recibe mensajes
de arranque, latidos de conexión y resultados de medición. La telemetría sale
por `Serial1`; las órdenes de la Mac siguen llegando solo por USB. El Mega no
espera respuestas del Pico ni deja de funcionar cuando está ausente. Anuncia
el comienzo de una captura y omite latidos durante sus 30 segundos para no
bloquear el muestreo al escribir en serie.

## Protocolo

Órdenes ASCII, una por línea, máximo 79 caracteres antes del terminador:

```text
V1 1 PING
V1 2 FACE warm
V1 3 MEASURE
V1 4 STOP
V1 5 PLAY 1
```

El identificador va de 1 a 65535. `FACE` admite `neutral`, `warm`, `listening`,
`thinking` y `sleeping`. `PLAY` admite pistas de 1 a 9 existentes en la SD.
La aplicación de conversación expone STOP, FACE, MEASURE y PING; el modelo
solo selecciona una expresión validada. No escribe líneas serie libres.

Las respuestas son objetos JSON con `v:1` y un `type`:

```json
{"v":1,"type":"ready","board":"mega2560","sensor":true,"audio":true}
{"v":1,"type":"ack","id":1}
{"v":1,"type":"error","id":3,"reason":"no_contact"}
{"v":1,"type":"measurement","valid":false,"rr_count":0,"rejected":0,"rmssd_pairs":0,"motion_checked":false,"window_ms":30000,"reason":"insufficient_signal"}
```

Una medición válida añade `bpm`, `sdnn` y `rmssd`. Una inválida devuelve una
razón y omite esos valores. `ack` confirma la aceptación; el evento
`measurement` llega al terminar la ventana. `animation_done` indica el fin
observado de reproducción por BUSY. Un identificador 0 corresponde a un evento
local o a una orden que no se pudo identificar.

El host envía PING cada dos segundos. Si faltan durante seis segundos después
del primer PING, el Mega detiene medición y audio. El host marca la conexión
como no preparada si no recibe confirmaciones durante cuatro segundos. STOP
sigue admitido aunque la medición esté activa. FACE y PLAY se rechazan durante
la captura para no perder muestras al dibujar o manejar audio.

## Cambios de comportamiento

- La medición de 30 segundos y la espera de audio usan `millis()` y límites de
  tiempo; ya no ejecutan una larga secuencia fija que impida procesar STOP.
- Los tiempos usan `uint32_t`: evita desbordamientos del `int` de 16 bits del
  AVR presentes en duraciones de 34 y 88 segundos del sketch recuperado.
- Los intervalos RR se calculan con estadística incremental acotada a 128,
  sin sobrescribir un arreglo de 50 valores. No hay divisiones por cero.
- Se reinicia el detector en cada ventana. La pérdida de contacto, saturación,
  interrupción del FIFO y ausencia de muestras invalidan la medición.
- Se requieren al menos diez intervalos aceptados y nueve pares consecutivos
  para RMSSD. Rechazar un intervalo rompe la continuidad del par.
- El audio tiene un límite de cuatro minutos y debe afirmar BUSY en dos
  segundos. La inicialización de librerías tiene esperas acotadas; no se
  promete tiempo real estricto.
- Un nuevo contacto del FSR puede iniciar una medición. Mantenerlo presionado
  no dispara ventanas sucesivas indefinidamente.

La señal es PPG y no hay control de movimiento por IMU. `valid:true` significa
que pasó estos filtros del prototipo; no certifica calidad clínica, ECG ni
validez de una inferencia emocional. La conversación no recibe una etiqueta
de «ansioso», «estresado» o «relajado» a partir de la medición.
