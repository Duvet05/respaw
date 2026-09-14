# Receptor ResPaw para Pico W

El Pico recibe los resultados del Mega por UART, valida los mensajes y muestra
su estado por la consola USB. Arranca automáticamente con `main.py`. Funciona
con el MicroPython 1.26.1 que ya tenía la placa; no activa Wi-Fi, guarda lecturas
en flash ni interviene en la conversación de la Mac.

La instalación del 14 de septiembre de 2026 se verificó en el Pico físico.
También se actualizó el Mega con el emisor correspondiente. Las placas solo
estaban conectadas por USB a la Mac: el enlace entre sus pines sigue pendiente.
Consultar el [informe de pruebas](../../../docs/verificacion-pico-2026-09-14.md).

## Cableado entre placas

El enlace es de una sola dirección, a 115200 baudios, 8 bits, sin paridad y un
bit de parada. Preparar el montaje con las placas sin alimentación:

| Origen | Conexión | Destino |
| --- | --- | --- |
| Mega D18 / TX1 | Adaptador de nivel lógico de 5 V a 3,3 V apto para UART | Pico GP1 / RX0, pin físico 2 |
| Mega GND | Tierra común, incluida la del adaptador | Pico GND, por ejemplo pin físico 3 |

El Pico solo recibe: GP0 y Mega D19/RX1 quedan sin conectar. Si el adaptador
necesita referencias HV/LV, usar 5 V del Mega para HV y 3V3(OUT) del Pico para
LV, siguiendo las indicaciones de ese módulo. Cada placa puede alimentarse por
su propio USB; no unir las salidas de alimentación de las dos placas entre sí.

**No conectar TX1 del Mega directamente a GP1:** el Pico usa lógica de 3,3 V y
sus GPIO no toleran 5 V. Los límites eléctricos están en la sección 5.5.3 del
[datasheet de RP2040](https://pip.raspberrypi.com/documents/RP-008371-DS).
Los pines se contrastaron con el
[pinout del Mega](https://docs.arduino.cc/resources/pinouts/A000067-full-pinout.pdf)
y la [referencia UART de MicroPython para RP2](https://docs.micropython.org/en/v1.26.0/rp2/quickref.html#uart-serial-bus).

```mermaid
flowchart LR
    Mac[Mac: conversación y memoria] <-->|USB| Mega[Mega: pantalla y sensores]
    Mega -->|D18 TX1 / 5 V| Nivel[Adaptación a 3,3 V]
    Nivel -->|GP1 RX0| Pico[Pico W: receptor]
    Pico -->|USB: diagnóstico| Consola[Consola en la Mac]
```

## Uso

- Un pulso breve del LED cada dos segundos indica que espera datos del Mega.
- LED encendido indica un enlace reciente, aunque el Mega no tenga sensor.
  No significa que una lectura fisiológica sea válida.
- La consola USB anuncia `pico_ready`, los mensajes aceptados como `pico_rx`
  y un resumen `pico_status` cada dos segundos.

Para observar la consola desde la Mac, con `pyserial` preparado:

```sh
uv run --offline --with pyserial==3.5 python -m serial.tools.miniterm /dev/cu.usbmodem14201 115200
```

Salir de miniterm con Ctrl+]. Ctrl+C interrumpe el programa MicroPython para
mantenimiento; Ctrl+D en su REPL lo reinicia. El puerto puede cambiar al
reconectar. La web sigue usando el puerto distinto del Mega.

La consola no acepta órdenes de control. Conectar ambos USB a la Mac no
reenviará automáticamente datos entre ellos. El programa no controla sensores
conectados directamente al Pico; recibe los resultados calculados por el Mega.

## Protocolo y caducidad

El Mega v2 transmite JSON por línea en `Serial1`, separado del control USB:

- `ready`: identifica el Mega y la disponibilidad de sensor y audio.
- `heartbeat`: estado y tiempo de funcionamiento, cada dos segundos en reposo.
- `measurement`: resultado de la captura, idéntico al que recibe la Mac.

Antes de capturar, el Mega anuncia `measuring:true` y termina de transmitir
antes de reiniciar el FIFO del sensor. Omite latidos serie durante la ventana
de 30 segundos para evitar bloqueos de transmisión dentro del muestreo.
El Pico permite hasta 36 segundos de silencio en esa ventana; fuera de ella
considera perdido el enlace después de seis segundos.

La lectura se conserva solo en RAM y se deja de ofrecer tras perder el enlace,
comenzar otra captura, reiniciar el Mega o cumplir 60 segundos. Una lectura
inválida reemplaza a la anterior y omite BPM/SDNN/RMSSD. Recibir mensajes
malformados no renueva la conexión. Una línea de más de 512 bytes se descarta
completa hasta el salto de línea, sin interpretar su sufijo como otra orden.

También reconoce el formato antiguo `<BPM=...;SDNN=...;RMSSD=...;ESTADO=...>`.
Sus números quedan en `legacy_values`, con `valid:false` y
`reason:legacy_unvalidated`, porque no trae pruebas de calidad. Se descarta la
etiqueta emocional. Ninguno de estos datos activa intervenciones del robot.

## Instalar o actualizar

Seguir la instrucción de respaldo del README principal antes de modificar una
placa. El instalador exige un puerto explícito, respalda dos veces los archivos
existentes antes de escribir, verifica cada archivo y activa `main.py` al final:

```sh
python3 tools/pico_install.py \
  --port /dev/cu.usbmodem14201 \
  --expected-id e66368254f3e912e \
  --backup-dir "$HOME/Library/Application Support/ResPaw/hardware-backups/pico-update-NUEVO"
```

La carpeta de respaldo debe ser nueva y estar fuera del repositorio. El
instalador conserva la librería original `max30102/` y no reemplaza MicroPython.
El código recuperado del proyecto sigue intacto en `pico/source/current/` y
`pico/source/recovered-deleted/`.

Pruebas: `PYTHONPATH=companion python3 -m unittest discover -s tests -p test_pico.py -v`.
Incluyen la lectura en Python de los mensajes emitidos por el serializador C++
de producción del Mega, compilado con sanitizadores.
