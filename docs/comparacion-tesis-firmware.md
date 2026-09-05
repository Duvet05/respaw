# Cruce entre tesis, fuente candidata y firmware recuperado

## Documento consultado

- Archivo conservado: [`tesis-amir-flores.pdf`](tesis-amir-flores.pdf)
- Nombre original: `[Tesis2][20201315][Flores][Amir] (4) (1).pdf`
- Tamaño: 44,079,206 bytes; 184 páginas.
- SHA-256:
  `32db65c47cf2b23868441985faa230154c1cb4cae5caada03da976b8cd13f725`
- Fecha de modificación interna del PDF: 23 de noviembre de 2025.

El PDF completo se conserva junto a este informe por solicitud de su propietario.
Las páginas citadas son las impresas en la tesis.

## Coincidencias

| Tema | Tesis | Sketch y flash |
| --- | --- | --- |
| Controlador | Mega 2560 seleccionado en §4.1.2, pp. 104–106 | Firma física ATmega2560 y sketch dirigido al Mega |
| Adquisición | MAX30102, ventana de 30 s y muestreo de 100 Hz, §4.1.1–4.1.4 | `MEASUREMENT_TIME=30000`, `SAMPLE_DELAY=10`, `setSampleRate(100)` |
| Filtrado | Media móvil y umbral adaptativo, pp. 113–114 | Ventana móvil de 8 muestras y umbral al 60 % del rango observado |
| Intervalos RR | Aceptar aproximadamente 300–1500 ms, p. 114 | `MIN_INTERVAL=300`, `MAX_INTERVAL=1500` |
| Clasificación | Relajado 60–75 BPM/RMSSD >40; neutro 75–90/25–40; estresado >90/<25–30, pp. 118–120 | Los mismos puntos de corte; los casos ambiguos pasan a `NEUTRO` |
| Interacción | TFT, DFPlayer, FSR y arrays `EyeFrame`, §4.3.4 y Tabla 4.16, pp. 136–142 | Las mismas familias de rutinas y audios 0001, 0004–0009 |
| Telemetría | Entrega de BPM y HRV por serie, p. 115 | `Serial1` envía BPM, SDNN, RMSSD y estado |

La evidencia binaria es especialmente fuerte: las 17 cadenas de texto
significativas del sketch aparecen byte por byte en la flash. Véase
`mega2560/analysis/firmware-string-comparison.json`.

## Diferencias entre lo descrito y lo implementado

| Requisito de la tesis | Estado en el sketch entregado |
| --- | --- |
| Confirmar fuerza entre 0.5 y 1.5 N durante toda la ventana | Sólo comprueba `analogRead(A8) > 300` antes de comenzar; no hay límite superior ni recalibración a newtons |
| Dos FSR-402 en el presupuesto y esquemas | Sólo existe `pinFSR A8` |
| MPU6050 para invalidar mediciones con movimiento | No hay librería, objeto ni lectura de IMU |
| Invalidar y repetir una ventana contaminada | No existe bandera de ventana inválida ni estado de reintento |
| Exigir al menos 10 RR válidos | No se valida una cantidad mínima antes de calcular |
| Histéresis entre mediciones, §4.2.2 | No conserva el estado anterior ni implementa bandas de histéresis |
| Estados separados de saludo, intervención y cierre | `ESTADO_SALUDO` está declarado pero no se usa; la máquina real sólo conmuta hibernación, medición y análisis |
| Servomotores y movimientos, §4.3–4.4 | No se incluye `Servo` ni control de servos |
| Rutina `rutinaEstresConAudio()` y array `estres[]` de Tabla 4.16 | El sketch los denomina `rutinaEstresAnsiosoConAudio()` y `estres_ansioso[]` |
| Receptor/procesamiento en el Pico W | El Mega transmite por `Serial1`, pero el Pico no tiene actualmente `main.py`; el script borrado recuperado sólo prueba el MAX30102 y no recibe el protocolo |

La tesis describe una arquitectura objetivo más amplia que el prototipo
capturado. No debe afirmarse que IMU, doble FSR, servos, reintentos o histéresis
están implementados en esta versión.

## Riesgos técnicos encontrados en la fuente candidata

1. `bpmValues[50]` se escribe mediante `bpmCount++` sin comprobar el límite. Una
   ventana con más de 50 intervalos válidos corrompería memoria SRAM.
2. Con cero intervalos RR se divide entre cero al calcular BPM, media RR y
   SDNN; con uno se divide entre cero al calcular RMSSD.
3. `drawEyesHappy()` calcula `eyelidHeight = h * 40`; con `h=170` intenta
   dibujar un párpado de 6,800 píxeles. Parece faltar una división por 100.
4. Si el DFPlayer o MAX30102 no responden, la inicialización entra en un bucle
   infinito. El bucle que espera BUSY del DFPlayer tampoco tiene timeout.
5. El pin BUSY se configura como `INPUT`, sin pull-up; si queda flotante o
   permanentemente bajo puede bloquear la rutina.
6. No se reinician `lastBeatTime`, `rising`, `lastValue`, `irBuffer` ni
   `irIndex` al iniciar una nueva medición. La nueva ventana hereda estado del
   ciclo anterior.
7. `particleSensor.setup()` usa parámetros predeterminados de una versión de
   librería no registrada. El sketch fija después tasa y rango ADC, pero no
   deja explícitos todos los parámetros que la tesis da por definidos.

Estas observaciones no modifican el sketch preservado. Conviene crear una
segunda versión corregida y mantener intacta esta copia como referencia del
firmware real.
