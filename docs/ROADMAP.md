# Pendientes y pruebas de integración

Este roadmap aplica a la [arquitectura objetivo](ARCHITECTURE.md). La evidencia
de una prueba debe identificar hardware, transporte y datos sintéticos; una
prueba de parser o un log del Pico no certifica la cara dibujada por el Mega.

## Hitos, en orden

| Hito | Resultado requerido | Estado |
| --- | --- | --- |
| 1. Servidor de enlace | Raspberry recibe `hello`, autentica el robot, mantiene latidos y rechaza entradas inválidas | Desplegado y verificado por Internet; once pruebas pasaron en la Raspberry |
| 2. Pico por Wi-Fi | Configuración móvil, conexión WSS con TLS verificado, reinicio y reconexión | Gateway instalado; pendiente de configurar una red de 2,4 GHz real y verificar conexión física |
| 3. UART bidireccional | Mega procesa órdenes en Serial1 y emite sus confirmaciones por el mismo enlace | Pendiente; conservar el USB para diagnóstico con parser y respuestas separados |
| 4. Primer gesto | Server → Pico → Mega → TFT; confirmación del Mega y observación de la cara | Pendiente del Mega, TFT y cableado con adaptación de nivel |
| 5. Primer evento físico | Contacto → Mega → Pico → servidor con ubicación y estado | Pendiente; definir sensor y publicar evento sin duplicar una pulsación |
| 6. Cerebro y memoria | Companion usa el enlace del robot y recupera recuerdos del perfil correcto entre sesiones | Pendiente de integrar el transporte; la memoria del companion ya existe |
| 7. Modelo en nube | Proveedor explícito, credencial en el servidor, mismas validaciones de respuesta y cancelación | Autorizado por el usuario; proveedor y credencial pendientes |
| 8. Voz en el robot | Micrófono → STT → respuesta → TTS → parlante, con STOP | Pendiente de decidir hardware y transporte; DFPlayer reproduce pistas, no voz generada arbitraria |

Para el hito 3 hay que extender el componente responsable: el parser del Mega.
Enviar al D19 una línea que hoy solo se procesa por USB no completa el hito.
Cada transporte necesita su buffer y la respuesta dirigida al emisor. Antes de
permitir varios controladores simultáneos hay que definir quién controla al robot.

## Pruebas del Pico

| ID | Prueba | Criterio de aceptación |
| --- | --- | --- |
| PICO-01 | Arranque | Anuncia identidad y estado; sigue funcionando sin servidor y sin Mega |
| PICO-02 | Acción válida | Traduce únicamente órdenes conocidas; una confirmación distingue validación, envío y aceptación |
| PICO-03 | Evento de UART | Acepta telemetría válida y rechaza líneas inválidas con el receptor existente |
| PICO-04 | UART repetido | 1.000 mensajes en ambos sentidos, límites de buffer y contadores documentados; pendiente de firmware/cableado |
| PICO-05 | Wi-Fi conocido | Arranca y se conecta a la red configurada de 2,4 GHz |
| PICO-06 | Wi-Fi incorrecto | Tiempo de conexión acotado; ofrece setup y mantiene el diagnóstico |
| PICO-07 | Setup móvil | Formulario usable desde iPhone/Android por URL manual; no exige una app |
| PICO-08 | Persistencia | Tras corte de alimentación conserva configuración válida; secretos ausentes de logs y Git |
| PICO-09 | WSS | `hello` y latidos llegan a la Raspberry; TLS verifica cadena y hostname |
| PICO-10 | Servidor → Pico | Comando llega al Pico físico; no declarar movimiento hasta recibir confirmación del Mega |
| PICO-11 | Pico → servidor | Distingue eventos reales de fixtures sintéticas |
| PICO-12 | Servidor caído | Pico continúa leyendo UART, limita colas y reintenta sin bloquear indefinidamente |
| PICO-13 | Servidor recuperado | Reabre conexión, se identifica y actualiza estado sin repetir acciones anteriores |
| PICO-14 | Router caído | Recupera Wi-Fi y WSS; indicar si se probó con router físico o simulación |
| PICO-15 | Mensaje inválido | Rechaza tamaño, versión, tipo, IDs y expresiones inválidas sin escribir órdenes libres |
| PICO-16 | Certificado inválido | Falla cerrado sin pasar a `ws://` ni desactivar la validación |
| PICO-17 | Autenticación | Token equivocado o identidad distinta no pueden usar el enlace |

## Servidor, memoria y operación

La Raspberry debe reiniciar el servicio de enlace después de un fallo y de un
reinicio del sistema. Su despliegue mantiene un entorno Python separado, token
fuera del código, logs sin credenciales y el operador en loopback. Funnel debe
exponer únicamente el puerto del robot. El companion local no se vuelve público
por publicar ese servicio.

Antes de conectar conversación y memoria al robot, probar STOP durante una
respuesta pendiente, aislamiento entre perfiles, corrección/olvido y una
reconexión sin duplicar la decisión anterior. Un evento de sensor no crea una
identidad personal ni autoriza guardar su historial. El proveedor de IA debe
devolver la misma estructura validada que el modelo local; sus errores deben
permitir seguir deteniendo el robot.

Para configuración/estado persistente, definir clave de operación, ACK,
deduplicación, caducidad y recuperación tras reinicios. Las acciones actuales
no ofrecen una garantía de ejecución exactamente una vez. Los gestos efímeros
caducan; no se almacena una cola sin límite de parpadeos o expresiones.

## Verificación reproducible

```sh
make check
python3 -m venv /tmp/respaw-link-venv
/tmp/respaw-link-venv/bin/python -m pip install -r requirements-link.txt
make check-link PYTHON=/tmp/respaw-link-venv/bin/python
```

El comando habitual conserva las pruebas offline. `check-link` exige la versión
fijada de WebSocket y añade pruebas de sockets reales, autenticación, mensajes
inválidos, latidos y conexiones nuevas. Las pruebas físicas y la configuración
del gateway se documentan por separado, con resultados obtenidos, no esperados.
No requieren descargar un LLM para validar el enlace.

Consultar el [informe de verificación del 3 de octubre](verificacion-enlace-2026-10-03.md).
