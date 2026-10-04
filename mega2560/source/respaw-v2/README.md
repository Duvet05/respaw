# ResPaw Mega v2

Firmware para Arduino Mega 2560. Mantiene la TFT y los periféricos del
sketch recuperado, mientras la conversación y la memoria corren en el servidor.
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
real. El 14 de septiembre de 2026 se cargó una revisión anterior de v2 en el Mega recuperado:
la lectura posterior coincidió con el programa compilado y se verificaron las
órdenes USB. El sensor de pulso y el DFPlayer no pudieron inicializarse en ese
montaje. El [informe de hardware](../../../docs/verificacion-hardware-2026-09-14.md)
detalla las comprobaciones y la validación física pendiente.

El 3 de octubre se cargó esta revisión con órdenes por USB y Serial1. La
lectura independiente posterior confirmó el HEX, el bootloader intacto y
EEPROM/fuses sin cambios; el probe USB comprobó PING, FACE, STOP y vencimiento
del controlador. El usuario confirmó que casi ningún periférico está
conectado y no hay cables UART entre Mega y Pico. TFT, presión y gesto físico
siguen pendientes. La [verificación de integración](../../../docs/verificacion-integracion-2026-10-03.md)
registra el respaldo y los resultados reales.

## Hardware conservado

| Componente | Conexión del sketch recuperado |
| --- | --- |
| TFT de 3,5 pulgadas | Shield paralelo, MCUFRIEND_kbv / Adafruit GFX |
| FSR | A8, presión por encima de 300; liberación por debajo de 250, estable durante 40 ms |
| MAX30102 | I²C del Mega; 100 Hz explícitos, promedio FIFO de 1 muestra |
| DFPlayer RX/TX del Mega | SoftwareSerial A15 / A14, 9600 baudios |
| DFPlayer BUSY | A13 con pull-up; ocupado en LOW |
| Mac | Serial USB, 115200 baudios |
| Pico W | Serial1 TX1/D18 a GP1/RX del Pico con adaptación de 5 V a 3,3 V; GP0/TX del Pico a RX1/D19 del Mega con niveles compatibles y GND común |

El umbral del FSR y el detector PPG son parámetros iniciales del prototipo,
pendientes de calibración con el montaje. No se controla un servo o una IMU.

El [receptor v2 del Pico](../../../pico/source/respaw-v2/README.md) recibe
telemetría. El [gateway](../../../pico/source/respaw-gateway/README.md) añade
órdenes por UART cuando la configuración y la capacidad anunciada del Mega
permiten habilitar TX. El Mega ahora procesa órdenes tanto en USB (`Serial`)
como en `Serial1`, con buffers independientes y respuestas dirigidas al puerto
solicitante. Arranque, contacto y mediciones se anuncian por ambos puertos.
Anuncia el comienzo de una captura y omite latidos periódicos durante sus 30
segundos para reducir bloqueos del muestreo. Sigue procesando PING y STOP.

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
{"v":1,"type":"ready","board":"mega2560","sensor":true,"audio":true,"commands":true}
{"v":1,"type":"ack","id":1,"command":"PING"}
{"v":1,"type":"contact","sensor":"fsr_a8","pressed":true,"uptime_ms":1500}
{"v":1,"type":"error","id":3,"reason":"no_contact"}
{"v":1,"type":"measurement","valid":false,"rr_count":0,"rejected":0,"rmssd_pairs":0,"motion_checked":false,"window_ms":30000,"reason":"insufficient_signal"}
```

Una medición válida añade `bpm`, `sdnn` y `rmssd`. Una inválida devuelve una
razón y omite esos valores. `ack` confirma que se ejecutó el manejador de la
orden: para FACE, después de dibujar; para STOP, después de detener; para
MEASURE, después de iniciar la captura. No confirma el resultado de una
medición ni que un periférico ausente haya producido un efecto observable. El evento
`measurement` llega al terminar la ventana. `animation_done` indica el fin
observado de reproducción por BUSY. Un identificador 0 corresponde a un evento
local o a una orden que no se pudo identificar.

El primer PING adquiere el control para su puerto, USB o UART. Ese controlador
envía PING cada dos segundos; los demás reciben `controller_busy` al intentar
controlar el cuerpo. FACE, MEASURE y PLAY requieren un propietario adquirido
mediante PING; sin él devuelven `controller_required`. Si faltan PING durante
seis segundos, el Mega detiene medición/audio, vuelve a neutral, libera el
control y envía `host_timeout` al propietario anterior. Un nuevo controlador
puede adquirirlo después. STOP se admite desde cualquiera de los dos puertos,
incluso durante la captura, y no cambia el propietario. FACE y PLAY se rechazan
durante la captura para reducir pérdidas de muestras al dibujar o manejar audio.

El estado inicial del FSR y sus cambios estables se publican como `contact`.
`fsr_a8` identifica la entrada eléctrica: el código no determina si está en
la cabeza, mano u otra parte del montaje. Una presión con controlador activo
publica el evento sin forzar una medición de 30 segundos; el servidor puede
responder con un gesto. Cuando no hay controlador se conserva la medición
automática por una nueva presión. Estos umbrales necesitan calibración real.

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
- Un nuevo contacto del FSR puede iniciar una medición cuando no hay controlador.
  Mantenerlo presionado no dispara ventanas sucesivas indefinidamente.

`tests/mega_transport_fixture.cpp` ejecuta el parser, despacho, reglas de control
y eventos de contacto que utiliza este sketch con streams simulados. La prueba
compila con sanitizadores y verifica fragmentación en ambos puertos, respuestas,
rechazo de órdenes inválidas, STOP, caducidad, desbordamiento del reloj y presión
con/sin controlador. Esa prueba no sustituye la comprobación del cableado, FSR
y TFT físicos.

La señal es PPG y no hay control de movimiento por IMU. `valid:true` significa
que pasó estos filtros del prototipo; no certifica calidad clínica, ECG ni
validez de una inferencia emocional. La conversación no recibe una etiqueta
de «ansioso», «estresado» o «relajado» a partir de la medición.
