# Recuperación de Raspberry Pi Pico W y Arduino Mega 2560

Captura realizada el 5 de septiembre de 2026. Todas las operaciones sobre las
placas fueron de lectura. Las memorias se leyeron dos veces y las copias fueron
comparadas antes de generar los artefactos canónicos.

## Organización del repositorio

Cada dispositivo sigue la misma separación para distinguir evidencia original
de archivos derivados:

```text
docs/                 Tesis y contraste técnico
mega2560/
  captures/           Lecturas originales de flash, EEPROM, fuses y serie
  firmware/           Imágenes canónicas restaurables
  analysis/           Desensamblados, cadenas y comparaciones
  reports/            Informes de recuperación
  source/             Fuente candidata y su procedencia
pico/
  captures/           Dos lecturas originales de la flash
  firmware/           Imagen completa y partición LittleFS
  reports/            Informes lógico y forense
  source/             Archivos visibles y código borrado recuperado
tools/                 Utilidades reproducibles de recuperación y análisis
```

## Resultado breve

### Raspberry Pi Pico W

- Placa: Pico W, RP2040 revisión B2, flash de 2 MiB.
- Firmware: MicroPython 1.26.1, compilado el 11 de septiembre de 2025.
- La REPL, USB y el modo BOOTSEL funcionan.
- La flash completa se leyó dos veces con `picotool --verify`; ambas imágenes
  son idénticas.
- El sistema de archivos actual sólo contiene:
  - `max30102/__init__.py`
  - `max30102/circular_buffer.py`
- Actualmente no hay `main.py` ni `boot.py`.
- La búsqueda forense halló un script Python borrado completo en el offset de
  flash `0x1A9000`. Se conservó byte por byte como
  `pico/source/recovered-deleted/recovered_source_0x1A9000.py`. El nombre original ya
  no estaba disponible; por su contenido parece un `main.py` de prueba del
  MAX30102. El archivo recuperado es UTF-8 y su sintaxis Python es válida.

Artefactos principales:

- `pico/firmware/firmware-full.bin`: imagen restaurable de los 2 MiB completos.
- `pico/firmware/filesystem-littlefs.bin`: partición LittleFS cruda de 848 KiB.
- `pico/source/current/max30102/`: archivos fuente que aún eran visibles.
- `pico/source/recovered-deleted/`: fuente tallada desde espacio borrado.
- `pico/reports/logical-recovery-report.json`: recuperación lógica, archivo por archivo.
- `pico/reports/forensic-report.json`: datos de flash y evidencia forense.

### Arduino Mega 2560

- MCU confirmada por firma: ATmega2560 (`1E 98 01`).
- USB oficial Arduino: VID:PID `2341:0042`.
- Bootloader Wiring/STK500v2 funcional, versión informada `2.10`.
- Flash completa de 262,144 bytes leída dos veces. La primera pasada sólo
  recortó 738 bytes finales `FF`; todo su contenido coincide con la segunda.
- EEPROM de 4,096 bytes: completamente borrada (`FF`) y coincidente en ambas
  lecturas.
- Configuración leída dos veces: lock `CF`, low fuse `FF`, high fuse `D8`,
  extended fuse `FD`.
- Región de aplicación con datos hasta `0x0877D`; región de bootloader desde
  `0x3E000`.
- No hubo salida serie durante 15 segundos a 115200 ni a 9600 baudios. Eso no
  prueba una avería: el programa puede mostrar los mensajes sólo en la TFT o
  esperar una entrada externa.

Las cadenas conservadas en el binario indican una aplicación con TFT,
DFPlayer Mini, MAX30102, medición de 30 segundos, cálculo de BPM/SDNN/RMSSD,
clasificación `ESTRESADO / ANSIOSO`, `NEUTRO` o `RELAJADO`, hibernación y envío
de resultados al Pico W.

Posteriormente se recibió el sketch
`mega2560/source/tesis-mega2560/tesis-mega2560.ino`. Sus 17
cadenas literales significativas aparecen exactamente en la flash recuperada,
lo que lo identifica con alta confianza como la misma versión o una versión
muy cercana. La comparación reproducible está en
`mega2560/analysis/firmware-string-comparison.json` y el cruce con la tesis en
`docs/comparacion-tesis-firmware.md`. El documento original se conserva como
`docs/tesis-amir-flores.pdf`.

Artefactos principales:

- `mega2560/firmware/firmware-full.bin`: flash exacta, incluida la zona del bootloader.
- `mega2560/firmware/firmware-full.hex`: representación Intel HEX completa.
- `mega2560/firmware/application-only.hex`: aplicación sin la región del bootloader,
  adecuada para una eventual restauración por el bootloader USB.
- `mega2560/firmware/eeprom.bin` y `mega2560/firmware/eeprom.hex`: copia de EEPROM.
- `mega2560/analysis/application-disassembly.txt`: desensamblado AVR de la aplicación.
- `mega2560/analysis/analyzed-functions.json`: 144 funciones detectadas por análisis.
- `mega2560/analysis/printable-strings.json`: cadenas con sus offsets.
- `mega2560/reports/recovery-report.json`: tamaños, hashes y verificaciones.
- `mega2560/source/`: sketch recibido y documentación de procedencia.
- `docs/comparacion-tesis-firmware.md`: correspondencias, diferencias y riesgos frente
  a la arquitectura descrita en la tesis.

## Límite de la recuperación de fuente

En MicroPython los `.py` se almacenan como texto, por lo que se recuperaron
exactamente los dos archivos visibles y un script borrado. En el ATmega2560 el
sketch se guarda compilado: el `.ino/.cpp` exacto, nombres locales, estructura
original y comentarios no existen en la flash. El BIN/HEX preserva el programa
ejecutable exacto. El sketch recibido después coincide con toda la evidencia
textual recuperada, pero sin las versiones originales del toolchain y las
librerías no es posible demostrar una identidad de compilación bit por bit.

## Precaución

No flashear ninguna placa hasta copiar esta carpeta a por lo menos otro medio.
Una restauración completa del Mega que incluya bootloader y fuses debe hacerse
con un programador ISP; `application-only.hex` mantiene aparte la opción de
restaurar únicamente la aplicación por USB. Verificar siempre `SHA256SUMS`
antes de usar una copia.
