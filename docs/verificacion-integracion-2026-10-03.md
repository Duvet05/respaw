# Integración Raspberry, Pico y Mega: 3 de octubre de 2026

Esta entrega continúa la [verificación inicial del enlace](verificacion-enlace-2026-10-03.md).
El software está integrado; la prueba física completa sigue pendiente.

## Raspberry de Católica

- Release activo: `~/.local/share/respaw/releases/20261003-companion/`.
- `respaw-link.service` y `respaw-companion.service` habilitados y activos.
- Proveedor seleccionado: OpenAI `gpt-4o-mini`; STT OpenAI y TTS ElevenLabs.
- Ollama instalado durante la preparación y luego detenido/deshabilitado por
  instrucción del usuario. El ensayo Qwen3 1.7B no produjo respuesta en 180 s;
  no se verificó conversación ni recuerdo con ese modelo en la Raspberry.
- Interfaz comprobada desde la Mac a través de SSH en `127.0.0.1:8765`:
  `provider:openai`, `configured:false`, voz pendiente y robot desconectado.
- No se hicieron llamadas reales a OpenAI ni ElevenLabs: las claves siguen pendientes.
- 83 pruebas de enlace, transporte, memoria y APIs pasaron en Python 3.13.5.
  Después se comprobaron también 20 pruebas del proveedor y voz con campos
  de configuración vacíos. Las respuestas de IA y audio de esas pruebas son fixtures.

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
Está **pendiente de carga**. Los originales recuperados no se modificaron.

La prueba con streams simulados ejecuta el despacho de producción y comprueba
fragmentación, separación USB/UART, propietario por PING, STOP, vencimiento y
contacto A8. No demuestra dibujo en TFT ni presión observada en la placa.

## Navegador y pendientes

`make check` pasó con 148 pruebas, validación de JavaScript y revisión del diff.
Pasaron los smokes habituales y `tests/browser_voice_smoke.py` en Chrome con
Playwright 1.62.0: WAV reproducible, STOP durante reproducción y síntesis,
audio tardío descartado al cambiar sesión, controles móviles y configuración
GPT sin claves. No hubo peticiones a proveedores desde esas pruebas.

Para completar el recorrido real faltan: configurar Wi-Fi del Pico, confirmar
D18→GP1 adaptado de 5 V a 3,3 V, GP0→D19 y GND común; cargar Mega v2 y habilitar
TX; comprobar contacto, ACK y cara en TFT; después probar conversación, memoria
y voz con las claves. El audio del navegador aún no llega al parlante del robot.
