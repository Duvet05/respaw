# Fuente candidata del Arduino Mega 2560

`FINAL_FINAL_FINAL_TESIS_AMIR_FLORES.ino` fue extraído sin modificaciones del
ZIP `FINAL_FINAL_FINAL_TESIS_AMIR_FLORES.zip` entregado el 5 de septiembre de
2026.

## Integridad y procedencia

- SHA-256 del ZIP recibido:
  `392d1e0a0f8dbb795f3ca68a676087bde4e9e36c2df94b605d8b409c216d2575`
- Ruta dentro del ZIP: `FINAL_FINAL_FINAL_TESIS_AMIR_FLORES.ino`
- Tamaño del sketch: 22,497 bytes, 587 líneas.
- SHA-256 del sketch:
  `73999c29e14e43f8dec78f26db58ddee244fc85c097a51a3293a7436a1a1f401`
- Fecha almacenada por el ZIP: 17 de octubre de 2025, 15:16.

## Relación con el firmware recuperado

El script `tools/compare_arduino_source.py` extrajo las cadenas literales de
código C/C++ del sketch y las buscó como secuencias UTF-8 exactas dentro de la
flash recuperada. Coincidieron las 17 de 17 cadenas comparadas, incluyendo:

- los mensajes de inicialización de TFT, DFPlayer y MAX30102;
- los tres estados `ESTRESADO / ANSIOSO`, `NEUTRO` y `RELAJADO`;
- la ventana de medición de 30 segundos;
- el protocolo `<BPM=...;SDNN=...;RMSSD=...;ESTADO=...>`;
- el mensaje de envío al Pico W y el regreso a hibernación.

Los offsets y bytes exactos están en `firmware-string-comparison.json`. Esta
coincidencia demuestra una asociación muy fuerte con el firmware, pero no
prueba que sea la entrada exacta de esa compilación: faltan las versiones del
compilador, core y librerías para intentar una reproducción binaria.

## Destino y dependencias aparentes

- Placa: Arduino Mega or Mega 2560 / ATmega2560.
- Core: `Wire`, `SoftwareSerial` y `math`.
- Librería SparkFun MAX3010x, que proporciona `MAX30105.h`.
- Adafruit GFX Library.
- MCUFRIEND_kbv.
- DFRobotDFPlayerMini.

No se recibieron números de versión de estas dependencias.

## Interfaces definidas por el sketch

- FSR402: `A8`, activado cuando el ADC supera 300.
- BUSY del DFPlayer: `A13`.
- SoftwareSerial del DFPlayer: RX `A15`, TX `A14`, 9600 baud.
- Consola USB: 115200 baud.
- Enlace al Pico W: `Serial1`, 115200 baud (TX1/RX1 del Mega).
- MAX30102: bus `Wire`, 100 muestras/s y rango ADC 16,384.
- TFT MCUFRIEND de 3.5 pulgadas: interfaz determinada por el shield/librería.

El sketch recibido se conserva como evidencia. Las observaciones y posibles
defectos están documentados en `docs/thesis-cross-check.md`; no se corrigieron
silenciosamente en esta copia.
