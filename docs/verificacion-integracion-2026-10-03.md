# Integración Raspberry, Pico y Mega: 3 de octubre de 2026

Esta entrega continúa la [verificación inicial del enlace](verificacion-enlace-2026-10-03.md).
El software está integrado; la prueba física completa sigue pendiente.
La prueba real de nube se añadió el 4 de octubre UTC, todavía 3 de octubre en Lima.

## Raspberry de Católica

- Release activo: `~/.local/share/respaw/releases/20261003-contact/`.
- `respaw-link.service` y `respaw-companion.service` habilitados y activos.
- Proveedor seleccionado: OpenAI `gpt-4o-mini`; STT OpenAI y TTS ElevenLabs.
- Ollama instalado durante la preparación y luego detenido/deshabilitado por
  instrucción del usuario. El ensayo Qwen3 1.7B no produjo respuesta en 180 s;
  no se verificó conversación ni recuerdo con ese modelo en la Raspberry.
- En la comprobación inicial desde la Mac por SSH en `127.0.0.1:8765`, antes
  de instalar las credenciales, la interfaz informó:
  `provider:openai`, `configured:false`, voz pendiente y robot desconectado.
- Las claves de OpenAI y ElevenLabs se instalaron después en archivos privados
  con permisos 0600: `/tmp/respaw-cloud.env` en la Mac y
  `~/.local/state/respaw/cloud.env` en la Raspberry. Sus valores no se guardaron
  en Git ni en logs.
- 83 pruebas de enlace, transporte, memoria y APIs pasaron en Python 3.13.5.
  Después se comprobaron también 20 pruebas del proveedor y voz con campos
  de configuración vacíos. Las respuestas de IA y audio de esas pruebas son fixtures.
  El release de contacto pasó luego 131 pruebas en la Raspberry, incluyendo
  transporte, respuesta corporal, memoria y API local, con claves vacías.

## Prueba real de GPT, memoria y voz

La Raspberry completó dos turnos reales con OpenAI `gpt-4o-mini`, con respuestas
estructuradas válidas y HTTP 200. El dato ficticio de un gato llamado
**Nube** se guardó como recuerdo autorizado y se recuperó en una sesión nueva
y una instancia nueva usando la misma SQLite temporal. Esto comprueba la
persistencia y recuperación con el proveedor real sin introducir recuerdos
de prueba en la base de uso habitual.

La ruta de producción `/api/speak` obtuvo una respuesta real de ElevenLabs:
HTTP 200 y 56050 bytes MP3, con voz George (`JBFqnCBsd6RMkjVDRZzb`). `ffprobe`
verificó MP3 mono, 44100 Hz y 3,436553 segundos. SHA256:
`eb2dba7019b4e5da3e23295748fbf4cf24363fce9271a1bd2d4112e1c628fab2`.
La evidencia privada está en `~/.cache/respaw/live-cloud-20261003/report.json` y
`~/.cache/respaw/live-cloud-20261003/voice-test.mp3` en la Raspberry. El destino
de su copia en la Mac es `/tmp/respaw-live-cloud-20261003/`.

La API de producción de la Raspberry en 8765, accesible mediante el túnel local,
también pasó `POST /api/transcribe` con ese MP3 sintético de ElevenLabs. OpenAI
`gpt-4o-mini-transcribe` devolvió HTTP 200 y el texto «Tu gato se llama Nube en
esta prueba. Suena adorable.», validado por presencia de gato y Nube. El
resultado está en `/tmp/respaw-live-cloud-20261003/transcription-report.json`
en la Mac. Es una prueba real del proveedor STT y la ruta de producción con
audio sintético, sin captura de voz humana.
La solicitud no creó un perfil persistente.

El Pico siguió offline durante la prueba. No se comprobó el cuerpo, el parlante
ni una expresión visible en TFT. El MP3 corresponde al flujo de audio del
navegador; obtener esos bytes no acredita reproducción por el robot. El
micrófono físico y el recorrido de una conversación hablada real quedan pendientes.

## Pico W físico

- Identidad: `e66368254f3e912e`, MicroPython 1.26.1.
- Gateway bidireccional instalado y comparado por lectura posterior; imports,
  CA DER y `CERT_REQUIRED` verificados en la placa.
- Se conservaron receiver/MAX30102 y la configuración. La contraseña del AP
  se simplificó por petición del usuario; permanece fuera de Git.
- `uart_commands:false`: GP0 sigue como entrada hasta confirmar el cableado.
- Portal `ResPaw-Setup-912E`, http://192.168.4.1, observado en arranque físico.
- Wi-Fi de 2,4 GHz aún pendiente de configurar. No se ha verificado WSS desde
  el Pico físico; la prueba pública anterior usó un cliente CPython.
- Respaldo previo privado en `/tmp/respaw-gateway-bidir-install-20261003/` y
  segunda copia en la Raspberry; los 18 archivos previos se verificaron por SHA256.

## Mega conectado

Se leyeron flash, EEPROM y configuración dos veces antes de una eventual carga.
Ambas lecturas coincidieron; el programa conectado era `respaw-autonomo`.
El respaldo completo también se copió y verificó en la Raspberry:

| Memoria | Tamaño | SHA256 |
| --- | --- | --- |
| Flash | 262144 bytes | `ef0fffb565ac100bed747580f80b77a879878189c8848cf7bfecf210410aa7dd` |
| EEPROM | 4096 bytes | `f47a8ec3e9aff2318d896942282ad4fe37d6391c82914f54a5da8a37de1300c6` |

El nuevo Mega v2 compiló con perfil AVR fijado: 33932 bytes de flash y 1254 de
RAM estática. HEX SHA256:
`4e6cbc4ecfc058cc191335d968a7c6bfab7c947cd244aa68d984b684ae00095c`.
Se cargó físicamente con avrdude 8.2 y se verificaron los 33932 bytes. El probe
USB físico pasó once comprobaciones: PING, cinco expresiones FACE, rechazo de
`bad_face`, STOP, vencimiento `host_timeout` y recuperación del controlador.
`ready` anunció `board:mega2560`, `commands:true`, `sensor:false` y `audio:false`.
El resultado está en `/tmp/respaw-mega-install-20261003/usb-probe.json`.
La lectura independiente posterior de toda la flash confirmó los 33932 bytes
del HEX y el bootloader intacto desde `0x3E000` hasta el final. EEPROM (4096
bytes), lock `cf` y fuses `ff/d8/fd` coincidieron con el respaldo anterior.
Resultado: `/tmp/respaw-mega-install-20261003/verification.json`. La segunda
copia contiene trece archivos con SHA256 idénticos en la Raspberry:
`~/.local/share/respaw/backups/20261003/respaw-mega-install-20261003/`.
Los originales recuperados no se modificaron.

La TFT no se observó. Hubo cambios de A8 sin interacción controlada, por lo
que no se interpretan como contacto confirmado; el pin podría estar flotante.
El usuario confirmó ausencia de cables UART entre Mega y Pico y que casi
ningún periférico está conectado. TFT, FSR, MAX30102 y DFPlayer requieren
revisión y montaje manual; no se declararon operativos en esta prueba.

La prueba con streams simulados ejecuta el despacho de producción y comprueba
fragmentación, separación USB/UART, propietario por PING, STOP, vencimiento y
contacto A8. No demuestra dibujo en TFT ni presión observada en la placa.

## Navegador y pendientes

`make check` pasó con 174 pruebas, validación de JavaScript y revisión del diff.
Pasaron los smokes habituales y `tests/browser_voice_smoke.py` en Chrome con
Playwright 1.62.0: WAV reproducible, STOP durante reproducción y síntesis,
audio tardío descartado al cambiar sesión, controles móviles y configuración
GPT sin claves. No hubo peticiones a proveedores desde esas pruebas.

La interfaz incorpora «Responder al contacto», apagado por defecto y disponible
solo para un robot real listo y compatible. El servidor usa una presión nueva
y fresca para solicitar `FACE listening`, sin GPT, voz ni memoria. STOP, cambio
de sesión, desconexión y 30 segundos sin presencia web lo desarman; la
generación de consentimiento impide rearmar con solicitudes viejas.

`tests/browser_contact_smoke.py` pasó en Chrome con la API local real y un
transporte fixture que declara capacidad de órdenes y pulsos sintéticos. Probó
simulación e incompatibilidad deshabilitadas, consentimiento explícito, estado
inicial sin gesto, presión nueva, deduplicación, STOP, rechazo HTTP 409 a
generaciones viejas, respuestas de activación y poll tardías tras STOP o cambio de sesión,
desconexión/reconexión, error de poll y disposición móvil. No invocó modelos,
voz ni guardado de recuerdos. Las capturas están en
`/tmp/respaw-contact-browser-evidence/`; no muestran hardware conectado.

Para completar el recorrido real faltan: configurar Wi-Fi del Pico, montar
D18→GP1 adaptado de 5 V a 3,3 V, GP0→D19 y GND común; habilitar TX después de
confirmar el montaje; comprobar contacto, ACK y cara en TFT; después probar
conversación, memoria y voz junto al cuerpo. GPT, recuperación de memoria y
generación de MP3 y STT de audio sintético ya pasaron con proveedores reales;
falta comprobar el micrófono físico, el ciclo hablado real y llevar el audio
del navegador al parlante del robot.
