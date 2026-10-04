# Control de dos servos SG90

Copia inicial del sketch `SG90.codigo.ino`, incorporada el 3 de octubre de
2026 desde `Descargas/SG90.codigo/SG90.codigo.ino`, sin modificar su contenido.
La carpeta conserva el nombre del archivo principal para abrirla en Arduino IDE.

## Uso y procedencia

- Placa asociada a la comprobación USB: Arduino Uno, COM5.
- Dependencia: biblioteca `Servo` (`Servo.h`).
- Pines de señal: servo 1 en D9 y servo 2 en D10.
- Consola serie: 9600 baudios; comandos `numeroServo angulo`, por ejemplo `1 120`.
- El controlador acepta ángulos entre 0 y 180 grados.
- Tamaño original: 2615 bytes.
- SHA-256 del archivo original:
  `6fa093cd661711aae1d5fa4692971bb6a62d59d531e09fd3965b92b29c162c8f`.

El banner `CONTROL DE 2 SERVOS` y los ejemplos del sketch coinciden con la
salida observada en el Uno conectado. No se ha leído ni comparado su flash,
por lo que esa coincidencia no demuestra identidad binaria del firmware.
Este sketch es independiente del firmware ResPaw para Mega 2560.

La copia conserva el valor inicial `anguloServo1 = 220` del original, aunque
la validación de comandos admite únicamente 0–180. No se corrigió durante
esta incorporación para conservar una base fiel para el historial de cambios.

Las futuras modificaciones deben registrarse con commits sobre este archivo.
Esta incorporación no cargó código a la placa ni probó movimientos de servos.
