# GPT, memoria y voz en Raspberry

La Raspberry de Católica ejecuta `respaw-companion.service` y `respaw-link.service`.
El companion usa GPT, conserva los recuerdos en SQLite y envía expresiones
al operador local mediante `NetworkRobot`. Solo un ACK real del Mega termina
una orden con `mega_accepted`; Detener no espera el ACK de una expresión anterior.

El proveedor activo es `openai`, modelo `gpt-4o-mini`, con voz `cloud`.
Ollama quedó detenido y deshabilitado a petición del usuario. Faltan las claves:
la interfaz arranca y explica qué configuración falta; no consulta proveedores
sin sus credenciales.

## Servicio y configuración

Preparar el enlace y su token según [server-link.md](server-link.md). El release
debe contener `companion/`, `tools/`, `pico/source/respaw-v2/` y las dependencias
de `requirements-link.txt`. Guardar `~/.local/state/respaw/companion.env` con
permisos 0600:

```text
RESPAW_PROVIDER=openai
RESPAW_MODEL=gpt-4o-mini
RESPAW_MODEL_TIMEOUT=45
RESPAW_SPEECH_PROVIDER=cloud
```

Instalar el servicio desde la raíz del repositorio:

```sh
mkdir -p ~/.local/share/respaw/companion-data ~/.config/systemd/user
chmod 700 ~/.local/share/respaw/companion-data
cp tools/respaw-companion.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now respaw-companion.service
```

## Credenciales

Crear `~/.local/state/respaw/cloud.env`, fuera de Git y del release, con permisos
0600. Systemd carga el archivo sin ejecutar su contenido. Campos vacíos dejan
esas funciones pendientes:

```text
OPENAI_API_KEY=
ELEVENLABS_API_KEY=
RESPAW_ELEVENLABS_VOICE_ID=
```

OpenAI usa una clave de proyecto creada en [API Keys](https://platform.openai.com/api-keys).
ElevenLabs necesita una clave en **Developers → API Keys**, con acceso a
text-to-speech; el enlace de facturación no autentica al servidor.
[Autenticación de ElevenLabs](https://elevenlabs.io/docs/api-reference/authentication).
El ID de voz es opcional; vacío selecciona George, la voz de su
[ejemplo oficial](https://elevenlabs.io/docs/eleven-api/quickstart).

```sh
chmod 600 ~/.local/state/respaw/cloud.env
systemctl --user restart respaw-companion.service
```

`configured` indica presencia de la clave. GPT informa `ready` solo después de
una respuesta válida real. El adaptador usa Responses, `store:false`, esquema
JSON estricto y validación local de expresiones e IDs de recuerdos. Rechazos,
cuota, autenticación fallida o JSON inválido no generan acciones. No hay fallback
automático a Ollama. [Structured Outputs de OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs).

## Acceso privado y voz

La web y la API escuchan en `127.0.0.1:8765`. Desde la Mac mantener este túnel
y abrir http://127.0.0.1:8765:

```sh
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 raspberry-ts
```

Usar el mismo puerto local y remoto conserva las comprobaciones Host/Origin.
Si 8765 está ocupado, cambiar `--port` del companion y ambos extremos del túnel.
Funnel publica solo el enlace del robot en 8443; la web y el operador son privados.

**Hablar** graba desde el navegador y OpenAI transcribe; la persona revisa el
texto antes de enviarlo. **Leer las respuestas en voz alta** sintetiza el último
mensaje real de ResPaw mediante ElevenLabs y reproduce MP3 en ese navegador.
STOP detiene el reproductor e invalida resultados tardíos; el proveedor puede
terminar una solicitud que ya recibió.

El audio sale por el dispositivo que abre la web. Falta conectar audio generado
al parlante del robot y definir su micrófono. El DFPlayer del Mega reproduce
pistas de su tarjeta y no recibe estos MP3.

## Memoria y contacto

Los recuerdos elegidos con **Recordar este mensaje** persisten en
`~/.local/share/respaw/companion-data/memory.sqlite3`. Invitados e historial
corriente permanecen en RAM. Corregir u olvidar invalida respuestas pendientes.
El proveedor recibe historial acotado y recuerdos seleccionados para el turno;
SQLite permanece en la Raspberry.

GPT usa búsqueda por palabras y contexto, sin exigir embeddings Ollama. El
contexto físico contiene disponibilidad y presión actual de `fsr_a8` solo con
estado fresco. El contacto no identifica personas, no se guarda como recuerdo
y no se interpreta como emoción.

Consultar la [verificación del despliegue](verificacion-integracion-2026-10-03.md)
para distinguir pruebas con fixtures de conexión y conversación físicas.
