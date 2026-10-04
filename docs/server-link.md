# Servidor de enlace en Raspberry Pi

Este servicio conecta el Pico por WebSocket. Es independiente del companion:
no ejecuta el modelo, guarda recuerdos ni sintetiza voz. Un servicio separado,
`respaw-companion.service`, integra GPT, memoria y voz mediante su operador
local: [guía del acompañante en Raspberry](companion-raspberry.md).

La instalación del 3 de octubre de 2026 usa:

- Release activo: `~/.local/share/respaw/releases/20261003-companion/`.
- Código activo: `~/.local/share/respaw/current`.
- Python: `~/.local/share/respaw/venv-link/`.
- Configuración: `~/.local/state/respaw/link.env`.
- Token: `~/.local/state/respaw/link.token`, permisos `0600`.
- Servicio: `respaw-link.service` de systemd **de usuario**, con linger habilitado.

## Instalación

Con el código del repositorio disponible, crear un entorno separado:

```sh
python3 -m venv ~/.local/share/respaw/venv-link
~/.local/share/respaw/venv-link/bin/python -m pip install -r requirements-link.txt
make check-link PYTHON="$HOME/.local/share/respaw/venv-link/bin/python"
```

`current` debe apuntar al checkout o release que contenga `tools/` y
`pico/source/respaw-v2/`. No se necesita instalar Ollama para probar el enlace.
Crear `link.env` con `RESPAW_ROBOT_ID=<unique_id_del_Pico>` y generar un token
fuera del repositorio. El token del instalador del Pico debe ser el mismo:

```sh
python3 - <<'PY'
import os, secrets
from pathlib import Path
directory = Path.home() / ".local/state/respaw"
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
directory.chmod(0o700)
with os.fdopen(os.open(directory / "link.token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
    stream.write(secrets.token_urlsafe(32) + "\n")
PY
mkdir -p ~/.config/systemd/user
cp tools/respaw-link.service ~/.config/systemd/user/
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now respaw-link.service
```

El servicio abre el enlace del robot en `127.0.0.1:8766/robot`, y el operador
en `127.0.0.1:8767/operator`. El token se exige antes de abrir WebSocket; el
`hello` debe coincidir con la identidad configurada. Se admite un solo Pico
emparejado a la vez. El operador permanece en loopback y no se publica.

## Acceso entre redes distintas

El Pico no ejecuta Tailscale. Funnel permite alcanzar el servidor por Internet:

```sh
tailscale funnel status --json
tailscale funnel --bg --https=8443 http://127.0.0.1:8766
```

Revisar la configuración existente antes de elegir el puerto. Esta instalación
añadió **8443** y conservó los servicios anteriores de 443 y 2222. El endpoint
del robot es:

```text
wss://duvet-raspberry-1.allosaurus-squeaker.ts.net:8443/robot
```

El Pico verifica TLS con hostname y una CA DER de confianza. La CA instalada
es ISRG Root X2, SHA-256
`69729b8e15a86efc177a57afb7171dfc64add28c2fca8cf1507e34453ccb1470`.
Un cambio de cadena puede requerir actualizar la CA. No se desactiva la
verificación para resolverlo. La [guía del gateway](../pico/source/respaw-gateway/README.md)
explica la configuración desde el celular.

Para retirar únicamente este endpoint:

```sh
tailscale funnel --https=8443 off
```

No usar `funnel reset` para retirar ResPaw: también eliminaría otros servicios.

## Estado y órdenes

Desde la Mac, usar el operador a través de SSH:

```sh
ssh raspberry-ts '~/.local/share/respaw/venv-link/bin/python ~/.local/share/respaw/current/tools/robot_link_client.py --token-file ~/.local/state/respaw/link.token status'
ssh raspberry-ts '~/.local/share/respaw/venv-link/bin/python ~/.local/share/respaw/current/tools/robot_link_client.py --token-file ~/.local/state/respaw/link.token action FACE warm'
```

El gateway bidireccional informa `forwarded` después de escribir UART y solo
devuelve `mega_accepted` al recibir un ACK real del Mega. `forwarded` no termina
la solicitud. Sin TX habilitado y un PING confirmado, devuelve un error y el CLI
sale con código 2. GP0 está deshabilitado en el Pico de esta instalación mientras
se confirma el montaje. Un ACK certifica aceptación, no observación de la TFT.
No se reenvían órdenes pendientes después de una desconexión. Las órdenes no
confirmadas caducan a los diez segundos.

`status.command_ready` exige el puente habilitado y estado fresco. Se reservan
plazas para STOP; el companion usa secuencias para descartar FACE antiguo si
STOP llegó antes. Contacto y capacidades vencen a los seis segundos (36 durante
una captura); una trama posterior no recupera un contacto anterior.

`--face-on-connect warm` sirve para una prueba explícita del servidor; el
servicio normal no ejecuta gestos al reconectar. Los eventos reales llevan
`source:uart`; solo el diagnóstico USB admite fixtures `source:synthetic`.

```sh
systemctl --user status respaw-link.service
systemctl --user restart respaw-link.service
systemctl --user stop respaw-link.service
journalctl --user -u respaw-link.service
```

El servidor informa conexión, IDs y resultado de órdenes, sin registrar el
token ni el contenido fisiológico. Los logs dependen de la configuración de
journald del equipo. Los respaldos originales del repositorio y del Pico están
fuera del release, en `~/.local/share/respaw/backups/20261003/`.
