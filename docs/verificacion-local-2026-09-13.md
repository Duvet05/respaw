# Verificación local del 13 de septiembre de 2026

Este informe registra la primera implementación. Las [mejoras posteriores de
conversación y memoria](mejoras-memoria.md) describen la búsqueda semántica,
las pruebas ampliadas y la evaluación de conversación actual.

Equipo: MacBook Air M4, 16 GB. Modelo de conversación:
`qwen3:4b-instruct-2507-q4_K_M`, Ollama 0.33.0, contexto de 8192 tokens.
Datos ficticios y bases temporales; robot en simulación.

| Comprobación | Resultado |
| --- | --- |
| `make check` | 29 pruebas pasaron; sintaxis JavaScript y `git diff --check` correctos |
| Núcleo C++ del firmware | Pruebas con AddressSanitizer y UndefinedBehaviorSanitizer correctas |
| Compilación AVR para Mega 2560 | 31 868 bytes de programa; 1004 bytes de variables globales; 12 % en ambos casos |
| Chrome con modelo de prueba | Perfil, dos sesiones, corrección, resolución, borrado y pantalla móvil correctos |
| Chrome con Ollama real | Mismo recorrido completo correcto; sin solicitudes externas de la página |
| Archivos de recuperación | Los 44 archivos de `SHA256SUMS` coinciden; fuente original intacta |
| Preparación de herramientas | Instaladores de Arduino y voz ejecutados; archivos descargados verificados con SHA-256 |

## Conversación con el modelo real

`tools/eval_local.py` ejecutó siete casos en español: primera conversación,
recuerdo ante un saludo, cambio de tema, asunto resuelto, recuperación de la
versión corregida, perfil distinto y recuerdo borrado. Los asserts y la revisión
manual de la última ejecución fueron satisfactorios para estos casos.

Después de guardar «Estoy triste porque desaprobé mi examen de cálculo. Solo
quiero conversar», en otra sesión bastó «Hola, volví». La respuesta fue:

> Me contaste que desaprobaste el examen de cálculo y que solo querías conversar.
> ¿Cómo te va ahora con eso?

Tras corregirlo a «Ya aprobé la recuperación de cálculo y me siento tranquila»
y marcarlo resuelto, un saludo no volvió a abrir el episodio. Ante una pregunta
explícita sobre el examen respondió:

> Me contaste que ya aprobaste la recuperación de cálculo y te sentías tranquila.
> ¿Cómo te va ahora con eso?

Al cambiar de perfil o borrar el recuerdo, reconoció no tenerlo. El tiempo de
respuesta de los siete turnos estuvo entre **1,55 y 4,26 segundos**. Son tiempos
de respuesta completa, medidos en una ejecución pequeña, no un benchmark de
carga o una garantía de latencia.

La evaluación real detectó referencias de memoria mal copiadas y formulaciones
que atribuían al robot experiencias del usuario. Se restringieron los ids
durante la generación y se aclaró la atribución en las instrucciones. Las
restricciones de ids tienen validación en código; la fidelidad y naturalidad
del texto siguen siendo propiedades probabilísticas. En el recorrido de
navegador apareció además una propuesta opcional de actividad al saludar:
todavía hay margen para reducir propuestas prematuras y preguntas repetidas.

## Voz local

Se sintetizó con Paulina un archivo de 4,29 segundos, sin reproducirlo por los
parlantes: «Hola, soy Ana. Ayer estaba triste por mi examen de cálculo».
Whisper base multilingüe transcribió correctamente el contenido.

La primera ejecución del flujo tardó 17,52 segundos. Las posteriores del CLI
tardaron 0,37 segundos con CPU y 0,29 con Metal; el flujo completo, incluida la
conversión de audio, tardó 0,49 segundos en la repetición. Estos números usan
voz sintética limpia. No evalúan ruido ambiental, acentos variados ni la
latencia desde un micrófono físico. Se rechazó una lista de reproducción
presentada como WAV y FFmpeg tiene prohibidos los protocolos de red.

## Pendiente de validación física y de experiencia

El firmware fue compilado, sin cargarlo en el Mega. Faltan pruebas del display,
cableado, órdenes USB, contacto y señal PPG en la placa real. La voz conversacional
es por turnos, con revisión de transcripción antes de enviar. Para afirmar que
acompaña mejor que el MVP faltan conversaciones consentidas con usuarios y una
comparación de naturalidad, errores de memoria, repeticiones e interrupciones.
