# Gateway WiFi del Pico W

Este gateway optativo conecta el Pico W directamente al servidor de la
Raspberry por `wss://`, incluso si están en redes distintas. El Mac se usa
para instalar y recuperar archivos; no interviene en el enlace normal.

Conserva `max30102`, `receiver.py` y `telemetry.py`. Lee UART0 en GP1 a
115200 baudios usando el receptor existente; GP0 sigue como entrada y no
transmite. El Mega actual procesa órdenes por USB y todavía no admite
órdenes por este enlace UART. Cada orden recibida se valida y obtiene
`action_error` con `mega_unavailable`. No se anuncia una actuación física.

## Instalación

Antes de escribir al dispositivo, copiar el repositorio a otro medio y
verificar esa copia, conforme al README principal. El instalador además
lee dos veces cada archivo de la placa, compara ambas lecturas y guarda
la copia en una carpeta privada fuera del repositorio.

```sh
python3 tools/pico_gateway_install.py \
  --port /dev/cu.usbmodem11101 \
  --expected-id e66368254f3e912e \
  --backup-dir /tmp/respaw-gateway-backup-20261003 \
  --server-url wss://duvet-raspberry-1.allosaurus-squeaker.ts.net:8443/robot \
  --token-file /tmp/respaw-link-token \
  --ca-file /tmp/respaw-isrg-root-x2.der
```

Los archivos privados de token y CA deben existir antes de ejecutar ese
comando. El ejemplo no descarga certificados ni genera confianza a partir
del certificado del servidor. El instalador genera una clave WPA2 del AP
y la guarda en `<backup-dir>/pairing.json`, con permisos `0600`; no imprime
el token, esa clave ni contraseñas de WiFi. También admite
`--ap-password-file` y `--config-file` para configuraciones privadas previas.

La CA DER se instala en `respaw_gateway/ca.der`. El instalador verifica los
archivos escritos, importa el gateway y carga esa CA con `CERT_REQUIRED`
en el firmware real antes de activar `main.py`. El informe de instalación
contiene tamaños, hashes y resultados, sin valores secretos.

## Configuración desde el teléfono

1. Conectar el teléfono a `ResPaw-Setup-912E` usando la clave de AP guardada
   en el archivo privado de emparejamiento. El sufijo cambia con la placa.
2. Abrir `http://192.168.4.1` manualmente. El AP no tiene salida a Internet;
   si el teléfono avisa, conservar esa conexión durante la configuración.
3. Escribir nombre y contraseña de una red WiFi de **2.4 GHz** y guardar.
   La dirección del servidor y su token ya están configurados. Los campos
   avanzados permiten sustituirlos; el token actual nunca aparece en el HTML.
4. El Pico se reinicia. Volver el teléfono a su red habitual.

Los ajustes se guardan de forma atómica en `respaw-gateway-config.json`.
La página solo escucha en la dirección privada del AP, requiere un formulario
de esa misma dirección y limita clientes, cabeceras y cuerpo de solicitud.
El AP se apaga después de una conexión WebSocket autenticada. Si fallan tres
intentos de enlace, reaparece para corregir la configuración.

## Comportamiento y límites

- TLS exige una CA confiable, un reloj válido y un hostname que coincida
  con el certificado. No existe una opción para desactivar la verificación.
  En arranque frío se consulta NTP; si no hay hora válida, no se conecta.
- Se envían `hello` con `transport=wifi` y latidos cada cinco segundos.
  La falta de confirmación de latidos o la caída del enlace provoca reintentos
  con esperas de 2 a 60 segundos. DNS de MicroPython es síncrono y su duración
  depende del driver; el timeout de `asyncio` no interrumpe esa consulta.
- Los mensajes y frames están limitados a 512 bytes. No hay compresión ni
  fragmentación. Se responde Ping/Pong sin enviarlo a la capa de JSON.
- Solo se reenvían eventos reales v1 aceptados por el receptor UART. No se
  generan mediciones ni eventos de prueba. Las mediciones del protocolo
  legado se conservan como diagnóstico local; carecen de los campos de
  calidad exigidos por el servidor y no se envían como mediciones v1.
- Hay una cola máxima de cuatro eventos durante la sesión, con antigüedad
  máxima de diez segundos; se descarta al perder el enlace. No se repiten
  acciones ni se reutilizan confirmaciones entre sesiones.
- Una configuración ausente o inválida vuelve al receptor UART original.

El proveedor de conversación puede ejecutarse en la Raspberry o en la nube;
este gateway transporta mensajes del robot y no ejecuta un modelo en el Pico.

## Dependencias y recuperación

El cliente WebSocket deriva de `aiohttp` oficial de `micropython-lib`, versión
de manifiesto `0.0.8`, fijado al commit
`220ca9de9af63589cf5f372dcd12d7c272e54584`. Se usa solo su módulo WebSocket
y una solicitud HTTP acotada propia. Los ajustes locales y la procedencia
están en `respaw_gateway/vendor/PROVENANCE.json`, junto con la licencia MIT.
`ntptime` también se conserva con commit y hash fijados.

El `main.py` previo se conserva como `respaw-main-before-gateway.py` y en el
backup externo. Para volver al receptor, restaurar `main.py` desde la copia
verificada mediante USB y reiniciar. No hace falta modificar el firmware
MicroPython ni escribir una imagen UF2.

La activación al final protege la primera instalación sobre el receptor.
Una actualización de un gateway ya activo reemplaza varios archivos del
paquete; si se interrumpe, puede necesitar restaurarlos desde el backup.

Referencias oficiales:
[TLS de MicroPython 1.26](https://docs.micropython.org/en/v1.26.0/library/ssl.html),
[WiFi de RP2](https://docs.micropython.org/en/v1.26.0/rp2/quickref.html),
[cliente WebSocket fijado](https://github.com/micropython/micropython-lib/blob/220ca9de9af63589cf5f372dcd12d7c272e54584/python-ecosys/aiohttp/aiohttp/aiohttp_ws.py).
