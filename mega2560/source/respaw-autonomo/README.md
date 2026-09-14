# ResPaw autónomo con el hardware existente

Firmware para **Mega 2560 + TFT paralela de 3,5 pulgadas + FSR en A8**. Arranca
al recibir alimentación y ofrece acompañamiento guiado sin Mac, red, Pico ni
modelo de lenguaje. La placa de desarrollo, pantalla y pin del FSR son los del
sketch original; su montaje físico sigue pendiente de comprobar.

La interacción combina una biblioteca de indicaciones breves con preferencias
aprendidas de respuestas explícitas. No reconoce voz, no comprende texto libre
ni genera conversaciones sobre acontecimientos personales. El DFPlayer,
parlante, micrófono y botones adicionales no están confirmados físicamente;
esta versión usa texto en la TFT. No mide el pulso ni sustituye esa función del
[firmware USB v2](../respaw-v2/README.md).

## Uso sin computadora

1. Con el equipo apagado, conectar la TFT y el FSR como en el montaje original.
   El FSR necesita su circuito divisor; no basta con un sensor suelto entre A8
   y alimentación. El esquema existente debe comprobarse antes de conectarlo.
2. Dar alimentación al Mega. El USB puede proporcionar energía; el programa
   no necesita un host ni mensajes serie para arrancar o seguir funcionando.
3. Un toque abre la selección de persona. **Invitado** es la opción inicial;
   **Persona 1–3** son perfiles que la persona debe elegir siempre de la misma
   manera. El robot no identifica a nadie automáticamente.
4. En los menús, un toque cambia de opción. Mantener alrededor de un segundo
   **y soltar** elige. Mantener tres segundos vuelve al inicio sin guardar.
5. Elegir **Una pausa breve**, **Un paso pequeño** o **Solo compañía**. Un toque
   termina la actividad; también finaliza a los 30, 30 o 45 segundos respectivos.
6. Decidir si ayudó. Para un perfil, **Me ayudó – guardar** y
   **No me ayudó – guardar** actualizan sus preferencias. La opción inicial es
   **Terminar sin guardar**. Invitado nunca escribe preferencias.

En un perfil con respuestas guardadas, la siguiente visita muestra la actividad
y valoración de la última respuesta **guardada**. No se inventan fechas ni se
afirma que esa valoración describa cómo está la persona ahora. El menú ofrece
**Olvidar mis preferencias**, con confirmación que empieza en «No, volver».

No se usa el panel táctil de la TFT: la entrada es el sensor de presión. Los
textos de la pantalla omiten tildes porque usan la fuente básica de Adafruit GFX.

## Qué aprende

Cada perfil guarda cuántas veces valoró cada una de las tres actividades y
cuántas le ayudaron. Las recomendaciones se sortean con pesos proporcionales a
`1 + 100 × (valoraciones útiles + 1) / (valoraciones totales + 2)`. Una valoración
positiva aumenta la probabilidad de esa actividad; la última actividad marcada
como no útil queda fuera de la propuesta inmediata. Todas siguen disponibles
para elegir manualmente. Los contadores se reducen al alcanzar 60 para que
nuevas preferencias puedan cambiar las anteriores.

Esta selección ponderada permite explorar y aprender preferencias; no es un
modelo conversacional, aprendizaje profundo ni una evaluación de efectividad
del acompañamiento. Presión, BPM, abandono, silencio y tiempo transcurrido no se
convierten en valoraciones ni se usan para inferir emociones.

## Memoria y límites físicos

Los tres perfiles ocupan 24 bytes de datos; cada registro de EEPROM ocupa 32
bytes, con versión, secuencia y CRC. Se rotan hasta 128 registros y se marca
cada uno como válido después de escribirlo. Al reiniciar se recupera el último
registro íntegro; guardar verifica la lectura antes de confirmar en pantalla.
Olvidar guarda primero el estado nuevo y después limpia los registros antiguos.
Si se interrumpe esa limpieza por falta de alimentación, pueden quedar copias
antiguas, aunque el estado nuevo siga siendo el más reciente.

El Mega dispone de 8 KB de SRAM y 4 KB de EEPROM según
[Arduino](https://docs.arduino.cc/hardware/mega-2560). Eso permite esta lógica y
memoria compacta. El Pico W disponible tiene 264 KB de SRAM según
[Raspberry Pi](https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html);
ninguna de esas placas puede ejecutar el modelo Qwen de 4B utilizado por la web.

El Pico conserva su papel de receptor en el firmware v2. No se necesita para
esta primera versión autónoma: así no se depende del enlace entre dos placas
que actualmente solo están conectadas por USB a la Mac. Añadirlo después exige
definir el cableado y sus niveles eléctricos, no solamente cambiar software.

## Compilar y verificar

Desde la raíz del repositorio:

```sh
make firmware-autonomous
PYTHONPATH=companion python3 -m unittest discover -s tests -p test_firmware.py -v
make check
```

Las pruebas C++ con AddressSanitizer y UndefinedBehaviorSanitizer cubren cambios
de preferencia, separación de perfiles, invitado, consentimiento, cancelación,
inactividad, rebote e histéresis del FSR, EEPROM corrupta, rotación, reinicio,
desbordamiento de secuencia y cortes en cada una de las 33 escrituras de un
guardado. No sustituyen la prueba de la TFT y el FSR físicos.

Este sketch se carga **en lugar de** `respaw-v2`; no usa su protocolo `V1` ni es
el dispositivo que controla la web de Ollama. Antes de cargarlo, conservar una
copia de la flash y EEPROM actuales, siguiendo las instrucciones de recuperación
del repositorio. Compilar no modifica ninguna placa.

Verificación de esta versión en la Mac, 14/09/2026:

- Compilación AVR con las dependencias fijadas: **26.986 bytes de programa** y
  **520 bytes de RAM estática**; quedan 7.672 bytes de SRAM para pila y variables
  locales según el compilador.
- `make check`: **72 pruebas** pasaron, incluidos ambos núcleos C++ con
  sanitizadores, además de JavaScript y comprobación del diff.
- SHA-256 del HEX generado fuera del repositorio:
  `767f9a66be6d8810e6c21035b1b83e07b752d38f589cc5d3617350bd8d445a90`.
- **Cargado en el Mega el 14/09/2026**, con respaldo previo y verificación del
  programa mediante lectura independiente. El bootloader, la EEPROM y la
  configuración se conservaron. La [verificación con la placa](../../../docs/verificacion-autonomo-2026-09-14.md)
  documenta las comprobaciones y sus límites.
- La TFT está desconectada y falta verificar físicamente el circuito del FSR.
  La legibilidad y los gestos en ese montaje siguen pendientes.

## Diagnóstico por USB (opcional)

La consola serie a 115200 baudios permite verificar el mismo controlador sin
un FSR conectado. No abrirla mientras otro proceso tenga el puerto del Mega.
Enviar líneas completas:

```text
STATE
DIAG ON
NEXT
SELECT
SELECT
NEXT
SELECT
STOP
DIAG OFF
```

Este recorrido elige invitado, inicia una actividad, la termina y sale sin
guardar. `DIAG ON` desactiva temporalmente el FSR; `NEXT` y `SELECT` representan
los mismos gestos físicos. `STOP` vuelve al inicio. `DIAG OFF` y reiniciar
restauran el FSR. El modo diagnóstico no modifica las reglas de persistencia:
elegir un perfil y una valoración con «guardar» sí escribe EEPROM.

Los eventos JSON `autonomous_state` exponen pantalla, perfil y selección. Los
estados son: `0` inicio, `1` perfil, `2` elección, `3` actividad, `4` valoración,
`5` confirmar olvido y `6` despedida. Son pruebas de lógica y comunicación;
recibirlas no demuestra que la pantalla esté conectada ni que se vea bien.
