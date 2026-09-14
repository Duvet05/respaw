# Conversación y memoria semántica

ResPaw puede recuperar algunos recuerdos aunque la persona use otras palabras.
Por ejemplo, «¿Qué te comenté de mis dificultades para cubrir la renta?» recuperó
«No me alcanza para pagar el alquiler este mes». La búsqueda por palabras sola
no encontraba ese caso. La respuesta se genera con el modelo de conversación;
no hay una frase pregrabada asociada al recuerdo.

## Comportamiento

- **Recuerdos por significado y palabras.** Qwen3-Embedding 0.6B local se combina
  con SQLite FTS5. Se filtra por persona antes de codificar o consultar recuerdos.
  Un máximo de cuatro recuerdos llega al modelo de conversación.
- **Continuidad.** Preguntas cortas como «¿Por qué me sentía así?» vuelven a leer
  los episodios citados en la última respuesta. Un cambio de tema que deja de
  citarlos limpia ese contexto. Esta regla de seguimiento complementa los
  embeddings; no pretende resolver cualquier referencia ambigua. Si una sesión
  empieza con un tema nuevo, no se añaden episodios solo por ser recientes.
- **Preferencias.** Hasta dos plazas están reservadas para preferencias activas,
  evitando que varios episodios las desplacen. En **Mis recuerdos** se puede
  convertir un episodio a preferencia, pausarla y reactivarla. El prompt indica
  que la petición actual prevalece sobre preferencias anteriores.
- **Conversación.** Las instrucciones permiten responder sin terminar siempre
  con una pregunta, respetar «no me hagas preguntas» y evitar pedir de nuevo una
  causa ya explicada. El contexto identifica al autor de cada declaración y su
  fecha de actualización para ayudar a atribuirla correctamente. Ante fórmulas
  explícitas como «sin hacerme preguntas», el esquema excluye puntuación
  interrogativa y el código la valida de nuevo. Esta regla acotada al turno no
  interpreta todas las maneras posibles de expresar una preferencia.
- **Corrección y olvido.** Modificar o borrar un recuerdo invalida su índice
  semántico, el contexto del perfil y las respuestas pendientes. Una escritura
  tardía de embeddings verifica que el recuerdo siga existiendo sin cambios.
  **Detener** no espera a que termine la inferencia de embeddings.

La elección de recuerdos que persisten sigue siendo explícita. El modelo no
convierte automáticamente las conversaciones ni las lecturas de sensores en
hechos sobre una persona.

## Funcionamiento local

El modelo de embeddings instalado ocupa unos 639 MB. Los vectores de recuerdos
se reutilizan desde SQLite y están asociados al digest del modelo y a la firma
del contenido. Las consultas de búsqueda no se guardan. Cada turno prepara
hasta 16 recuerdos pendientes, conservando búsqueda léxica sobre todos mientras
termina el índice. Si falta el modelo o falla, continúa la búsqueda por palabras
y contexto; no se llama a un proveedor remoto. La [guía](offline-companion.md)
explica instalación, controles y `--no-semantic-memory`.

Se usa coseno mínimo `0.42` y fusión de rangos con `k=60`. Se eligió ese umbral
con un conjunto pequeño de calibración; no expresa certeza de que dos textos
hablen del mismo evento y debe revisarse si cambia el modelo.

## Medición en esta Mac, 13/09/2026

La comparación de recuperación usa ocho paráfrasis nuevas y seis consultas sin
relación. Las paráfrasis se eligieron sin coincidencias de palabras útiles con
su recuerdo. No usa saludos, recencia ni un modelo de conversación para encontrar
el recuerdo; así la recuperación semántica no recibe ayuda del caso de apertura.

| Recuperación | Recuerdo correcto en las 8 paráfrasis | Consultas sin relación que trajeron candidatos |
| --- | --- | --- |
| Solo palabras | 0/8 | 0/6 |
| Palabras y significado | 6/8 | 0/6 |

En los seis aciertos, el recuerdo correcto fue el primer candidato. Los fallos
fueron «dificultades con las matemáticas» frente a una evaluación de cálculo y
«muerte de mi mascota» frente a la perrita Luna: no superaron el filtro. Este
resultado describe estos ejemplos, no un benchmark general. La primera consulta
con indexación tardó 1,10 s; las siguientes, con el índice disponible, 34–85 ms.

`make check` pasó 59 pruebas. Las pruebas de contrato cubren embeddings, caché
entre reinicios, cambio de versión del modelo, separación de perfiles,
corrección, pausa, borrado, fallos del modelo e inferencias concurrentes con
Detener/Olvidar. El recorrido de Chrome con un modelo de prueba pasó los controles
de recuerdos, preferencias y presentación móvil sin peticiones externas.
El recorrido final con conversación y embeddings reales pasó también la consulta
«renta» → «alquiler», verificó que el índice se generase y completó corrección,
resolución, borrado y controles de preferencias, sin solicitudes externas de la página.

La evaluación del modelo de conversación comprueba también la atribución de
recuerdos, la escucha sin preguntas y cambios de preferencia. Exporta los textos
y los fallos aunque una comprobación no pase. Recuperar evidencia correcta no
garantiza que el modelo la use bien: las respuestas deben revisarse además de
los checks automáticos. Esa distinción está alineada con evaluar recuperación,
actualizaciones y abstención por separado en
[LongMemEval](https://arxiv.org/abs/2410.10813), sin reproducir su benchmark.

La última ejecución con Qwen3 y embeddings pasó los checks de los **12 casos**.
Ante «No quiero resolverlo ahora ni que me hagas preguntas…», respondió:

> Entiendo que te dio rabia. Gracias por compartir eso.

Cuando después se le pidieron ideas, propuso estudiar un tema durante cinco
minutos sin formular otra pregunta. El tiempo completo de respuesta tuvo una
mediana de **4,43 s**, con un rango de **2,58–9,04 s**. Persisten frases torpes,
alguna confusión de atribución y respuestas poco naturales en la revisión manual;
pasar los checks no equivale a demostrar fidelidad de cada frase.

También se instaló y probó Qwen3.5 4B (3,4 GB), conservando Qwen3 4B como
predeterminado. En los ensayos, Qwen3.5 respetó algunas peticiones de estilo,
pero también inventó un dato ante un perfil sin recuerdos y produjo atribuciones
torpes. Es evidencia de estos ensayos, no una clasificación general de modelos.
Ambos se pueden elegir con `--model`; ninguno elimina la necesidad de revisar
fidelidad. La configuración actual usa respuesta directa (`think: false`) y
penalizaciones explícitas para permitir repetir nombres o causas guardadas.
[Modelo alternativo](https://ollama.com/library/qwen3.5:4b).

```sh
make check
PYTHONPATH=companion python3 tools/eval_retrieval.py --output /tmp/respaw-retrieval.json
PYTHONPATH=companion python3 tools/eval_local.py --semantic-memory --output /tmp/respaw-conversation.json
```
