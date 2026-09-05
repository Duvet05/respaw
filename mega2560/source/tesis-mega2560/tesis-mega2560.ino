/*********************************************************************
 * 🧩  PROYECTO: ROBOT EMOCIONAL HRV
 * 🧠  TESIS DE AMIR FLORES
 * 
 * Descripción general:
 * Este código integra la medición fisiológica (HRV), 
 * el análisis emocional, y las respuestas multisensoriales 
 * (pantalla TFT + audio DFPlayer + FSR interactivo).
 * 
 * Flujo de estados principal:
 *  HIBERNACIÓN → SALUDO → MEDICIÓN → ANÁLISIS → RUTINA → HIBERNACIÓN
 *********************************************************************/

// ======================= LIBRERÍAS NECESARIAS =======================
#include <Wire.h>
#include "MAX30105.h"
#include <Adafruit_GFX.h>
#include <MCUFRIEND_kbv.h>
#include <DFRobotDFPlayerMini.h>
#include <SoftwareSerial.h>
#include <math.h>

// ======================== DEFINICIÓN DE PINES =======================
#define pinFSR   A8     // Sensor FSR402
#define pinBusy  A13    // Pin BUSY DFPlayer Mini
#define RX_DF    A15
#define TX_DF    A14

// ======================== DECLARACIÓN DE OBJETOS ====================
MAX30105 particleSensor;             // Sensor de frecuencia cardíaca MAX30102
MCUFRIEND_kbv tft;                   // Pantalla TFT 3.5” MCUFRIEND
SoftwareSerial mySerial(RX_DF, TX_DF);
DFRobotDFPlayerMini myDFPlayer;      // Módulo de audio DFPlayer Mini

// ===================== COLORES BÁSICOS (TFT) =======================
#define BLACK   0x0000
#define BLUE    0x001F
#define RED     0xF800
#define GREEN   0x07E0
#define CYAN    0x07FF
#define WHITE   0xFFFF

int centerX, centerY; // Centro de la pantalla para dibujar los ojos

// ===================================================================
//  ESTRUCTURA PARA LOS FRAMES DE ANIMACIÓN DE LOS OJOS
// ===================================================================
/*
 * EyeFrame define cada "cuadro" de una animación emocional.
 * tipo: indica qué dibujar (ojos, corazón, círculo, etc.)
 * xOffset, yOffset: desplazamiento para animación de movimiento
 * w, h: ancho y alto del elemento
 * tiempo: duración del frame (ms)
 */
struct EyeFrame {
  int tipo;     
  int xOffset;
  int yOffset;
  int w;
  int h;
  int tiempo;
};

// ===================================================================
//  FUNCIONES DE DIBUJO PARA LA PANTALLA TFT
// ===================================================================
void drawEyes(int w, int h, int xOffset, int yOffset) {
  int eyeSpacing = 220;
  int xLeft  = centerX - (eyeSpacing / 2) + xOffset;
  int xRight = centerX + (eyeSpacing / 2) + xOffset;
  int yPos   = centerY + yOffset;
  tft.fillRoundRect(xLeft  - (w / 2), yPos - (h / 2), w, h, h / 3, CYAN);
  tft.fillRoundRect(xRight - (w / 2), yPos - (h / 2), w, h, h / 3, CYAN);
}

void drawEyesHappy(int w, int h) {
  int eyeSpacing = 220;
  int xLeft  = centerX - (eyeSpacing / 2);
  int xRight = centerX + (eyeSpacing / 2);
  int yPos   = centerY;

  // Base
  tft.fillRoundRect(xLeft  - (w / 2), yPos - (h / 2), w, h, h / 3, CYAN);
  tft.fillRoundRect(xRight - (w / 2), yPos - (h / 2), w, h, h / 3, CYAN);

  // Párpado inferior (feliz)
  int eyelidHeight = h * 40;
  int eyelidYOffset = h / -10;
  tft.fillRoundRect(xLeft  - (w / 2), yPos + eyelidYOffset, w, eyelidHeight, h / 3, BLACK);
  tft.fillRoundRect(xRight - (w / 2), yPos + eyelidYOffset, w, eyelidHeight, h / 3, BLACK);
}

void drawHeart(int w, int h) {
  int x = centerX, y = centerY;
  tft.fillCircle(x - w / 4, y - h / 4, w / 4, RED);
  tft.fillCircle(x + w / 4, y - h / 4, w / 4, RED);
  tft.fillTriangle(x - w / 2, y - h / 4, x + w / 2, y - h / 4, x, y + h / 2, RED);
}

void drawCircleCenter(int r, uint16_t color) {
  tft.fillCircle(centerX, centerY, r, color);
}

void drawEyesClosed(int w, int h, int xOffset, int yOffset) {
  int eyeSpacing = 220;
  int xLeft  = centerX - (eyeSpacing / 2) + xOffset;
  int xRight = centerX + (eyeSpacing / 2) + xOffset;
  int yPos   = centerY + yOffset;
  int lineThickness = h / 8;
  tft.fillRect(xLeft - w / 2, yPos - (lineThickness / 2), w, lineThickness, CYAN);
  tft.fillRect(xRight - w / 2, yPos - (lineThickness / 2), w, lineThickness, CYAN);
}

// ===================================================================
//  FUNCIÓN PRINCIPAL PARA MOSTRAR UN FRAME INDIVIDUAL
// ===================================================================
void mostrarFrame(EyeFrame f) {
  tft.fillScreen(BLACK);
  switch (f.tipo) {
    case 0: drawEyes(f.w, f.h, f.xOffset, f.yOffset); break;
    case 1: drawEyesHappy(f.w, f.h); break;
    case 2: drawHeart(f.w, f.h); break;
    case 3: drawCircleCenter(f.w / 2, WHITE); break;
    case 4: drawEyesClosed(f.w, f.h, f.xOffset, f.yOffset); break;
  }
  delay(f.tiempo);
}

// ===================================================================
//  MOTOR GENERAL DE RUTINAS (ANIMACIÓN + AUDIO)
// ===================================================================
void reproducirRutina(EyeFrame *secuencia, int totalFrames, int numeroAudio) {
  myDFPlayer.play(numeroAudio);    // 🔊 Reproduce el audio asociado
  for (int i = 0; i < totalFrames; i++) {
    mostrarFrame(secuencia[i]);    // 👁️ Dibuja frame a frame
  }
  // Espera hasta que el audio termine
  while (digitalRead(pinBusy) == LOW) delay(100);
  tft.fillScreen(BLACK);
}

// ===================================================================
//  SECUENCIAS DE ANIMACIÓN (declaradas en la versión anterior)
// ===================================================================

EyeFrame saludo[] = {
  { 1, 0, 0, 150, 170, 3000 },   // ojos felices
  { 0, 0, 0, 150, 170, 5000 },   // ojos normales
  { 2, 0, 0, 120, 120, 14723 },  // corazón
};
const int totalFramesSaludo = sizeof(saludo) / sizeof(saludo[0]);

EyeFrame relajado_esperando_confirmacion[] = {
  { 0, 0, 0, 150, 170, 12000 },   // ojos normal
  { 1, 0, 0, 150, 170, 3000 },   // ojos felices
  { 3, 0, 0, 100, 0, 1000 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 280, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 250, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 100, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 70, 0, 500 },  // círculo de diametro 4ta coma
  { 0, 0, 0, 150, 170, 4000 },   // ojos normal

  { 3, 0, 0, 100, 0, 2000 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 280, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 250, 0, 1000 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 160, 0, 1000 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 100, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 70, 0, 500 },  // círculo de diametro 4ta coma
  { 1, 0, 0, 150, 170, 7000 },   // ojos felices
  { 0, 0, 0, 150, 170, 8000 },   // ojos normal
  { 3, 0, 0, 100, 0, 1000 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 280, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 250, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 100, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 70, 0, 500 },  // círculo de diametro 4ta coma
  { 1, 0, 0, 150, 170, 5000 },   // ojos felices
};
const int totalFramesRelajado = sizeof(relajado_esperando_confirmacion) / sizeof(relajado_esperando_confirmacion[0]);

EyeFrame respuesta_no_relajacion[] = {
  { 0, 0, 0, 150, 170, 1500 },   // ojos normales
  { 1, 0, 0, 150, 170, 1500 },   // ojos felices
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 1, 0, 0, 150, 170, 1000 },   // ojos felices

};
const int totalFramesNo = sizeof(respuesta_no_relajacion) / sizeof(respuesta_no_relajacion[0]);

EyeFrame respuesta_si_relajado[] = {
  { 0, 0, 0, 150, 170, 7000 },   // ojos normales
  { 4, 0, 0, 150, 170, 31000 },  // ojos cerrados

};
const int totalFramesSi = sizeof(respuesta_si_relajado) / sizeof(respuesta_si_relajado[0]);


EyeFrame neutro[] = {
  { 0, 0, 0, 150, 170, 8000 },   // ojos normales
  { 1, 0, 0, 150, 170, 5000 },   // ojos felices
  { 3, 0, 0, 100, 0, 1000 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 280, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 250, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 100, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 70, 0, 500 },  // círculo de diametro 4ta coma
  { 1, 0, 0, 150, 170, 7000 },   // ojos felices
  { 3, 0, 0, 100, 0, 1000 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 280, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 250, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 220, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 190, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 100, 0, 1000 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 70, 0, 1000 },  // círculo de diametro 4ta coma
  { 1, 0, 0, 150, 170, 4000 },   // ojos felices
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 1, 0, 0, 150, 170, 4000 },   // ojos felices
  { 0, 0, 0, 150, 170, 8000 },   // ojos normales
  { 4, 0, 0, 150, 170, 22000 },  // ojos cerrados
  { 2, 0, 0, 120, 120, 15000 },  // corazón
  { 1, 0, 0, 150, 170, 6000 },   // ojos felices
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, -20, 0, 150, 170, 500 },   // ojos normales movido
  { 0, 0, 0, 150, 170, 13000 },   // ojos normales
};
const int totalFramesNeutro = sizeof(neutro) / sizeof(neutro[0]);

EyeFrame estres_ansioso[] = {
  { 0, 0, 0, 150, 170, 4000 },     // ojos normales
  { 1, 0, 0, 150, 170, 17000 },   // ojos felices
  { 0, 0, 0, 150, 170, 7000 },     // ojos normales
  { 3, 0, 0, 100, 0, 1000 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 190, 0, 250 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 220, 0, 250 },  // círculo de diametro 4ta coma
      { 3, 0, 0, 280, 0, 9500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 250, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 220, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 190, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 160, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 130, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 100, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 70, 0, 1500 },  // círculo de diametro 4ta coma

  { 0, 0, 0, 150, 170, 4000 },     // ojos normales

  { 3, 0, 0, 100, 0, 1000 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 130, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 160, 0, 500 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 190, 0, 250 },  // círculo de diametro 4ta coma
    { 3, 0, 0, 220, 0, 250 },  // círculo de diametro 4ta coma
      { 3, 0, 0, 280, 0, 9500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 250, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 220, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 190, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 160, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 130, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 100, 0, 1500 },  // círculo de diametro 4ta coma
  { 3, 0, 0, 70, 0, 1500 },  // círculo de diametro 4ta coma

  { 1, 0, 0, 150, 170, 13000 },   // ojos felices

  { 4, 0, 0, 150, 170, 88000 },  // ojos cerrados

  { 1, 0, 0, 150, 170, 34000 },   // ojos felices


};
const int totalFramesEstres = sizeof(estres_ansioso) / sizeof(estres_ansioso[0]);

// Prototipos (se definen al final o en un archivo aparte)
// 👋 Saludo inicial con ojos felices
void rutinaSaludoConAudio() {
  int totalFramesSaludo = sizeof(saludo) / sizeof(saludo[0]);
  reproducirRutina(saludo, totalFramesSaludo, 1);   // 0001.mp3
}

// 😌 Estado relajado (base)
void rutinaRelajadoConAudio() {
  int totalFrames = sizeof(relajado_esperando_confirmacion) / sizeof(relajado_esperando_confirmacion[0]);
  reproducirRutina(relajado_esperando_confirmacion, totalFrames, 4);   // 0004.mp3
}

// 🧘 Usuario relajado dice SÍ a consejo
void rutinaSiRelajadoConAudio() {
  int totalFrames = sizeof(respuesta_si_relajado) / sizeof(respuesta_si_relajado[0]);
  reproducirRutina(respuesta_si_relajado, totalFrames, 8);   // 0008.mp3
}

// 😴 Usuario relajado dice NO al consejo
void rutinaNoRelajacionConAudio() {
  int totalFrames = sizeof(respuesta_no_relajacion) / sizeof(respuesta_no_relajacion[0]);
  reproducirRutina(respuesta_no_relajacion, totalFrames, 9);   // 0009.mp3
}

// 😐 Estado neutro
void rutinaNeutroConAudio() {
  int totalFrames = sizeof(neutro) / sizeof(neutro[0]);
  reproducirRutina(neutro, totalFrames, 5);   // 0005.mp3
}

// 😣 Estado estresado / ansioso
void rutinaEstresAnsiosoConAudio() {
  int totalFrames = sizeof(estres_ansioso) / sizeof(estres_ansioso[0]);
  reproducirRutina(estres_ansioso, totalFrames, 6);   // 0006.mp3
}

// ===================================================================
//  CONFIGURACIÓN Y FUNCIONES DE HARDWARE
// ===================================================================

void inicializarTFT() {
  uint16_t ID = tft.readID();
  if (ID == 0xD3D3) ID = 0x9488;
  tft.begin(ID);
  tft.setRotation(1);
  tft.fillScreen(BLACK);
  centerX = tft.width() / 2;
  centerY = tft.height() / 2;
  Serial.println("✅ Pantalla TFT lista.");
}

void inicializarAudio() {
  mySerial.begin(9600);
  if (!myDFPlayer.begin(mySerial)) {
    Serial.println("❌ DFPlayer no detectado.");
    while (true);
  }
  myDFPlayer.volume(25);
  pinMode(pinBusy, INPUT);
  Serial.println("✅ DFPlayer Mini listo.");
}

void inicializarMAX30102() {
  if (!particleSensor.begin(Wire, I2C_SPEED_STANDARD)) {
    Serial.println("❌ No se detecta el MAX30102.");
    while (1);
  }
  particleSensor.setup();
  particleSensor.setPulseAmplitudeIR(0x1F);
  particleSensor.setPulseAmplitudeRed(0x0A);
  particleSensor.setSampleRate(100);
  particleSensor.setADCRange(16384);
  Serial.println("✅ MAX30102 inicializado correctamente.");
}

// ===================================================================
//  VARIABLES Y CONSTANTES PARA MEDICIÓN HRV
// ===================================================================
#define SAMPLE_DELAY 10
#define MIN_INTERVAL 300
#define MAX_INTERVAL 1500
#define THRESHOLD_FACTOR 0.6
#define MEASUREMENT_TIME 30000
#define MA_WINDOW 8

long irBuffer[MA_WINDOW];
int irIndex = 0;
unsigned long lastBeatTime = 0;
bool rising = false;
long lastValue = 0;
long maxIR = 0, minIR = 1000000;
float bpmValues[50];
float rrIntervals[50];
int rrCount = 0;
float avgBPM = 0, sdnn = 0, rmssd = 0;
String estadoEmocional = "";

// ===================================================================
//  FUNCIONES DE MEDICIÓN Y ANÁLISIS HRV
// ===================================================================

bool fsrPresionado() {
  return (analogRead(pinFSR) > 300);
}

void medirBiometria() {
  Serial.println("\n🩺 Midiendo durante 30 s...");
  unsigned long startTime = millis();
  rrCount = 0; int bpmCount = 0;
  maxIR = 0; minIR = 1000000;

  while (millis() - startTime < MEASUREMENT_TIME) {
    long irValue = particleSensor.getIR();
    irBuffer[irIndex] = irValue;
    irIndex = (irIndex + 1) % MA_WINDOW;
    long smoothIR = 0;
    for (int i = 0; i < MA_WINDOW; i++) smoothIR += irBuffer[i];
    smoothIR /= MA_WINDOW;
    maxIR = max(maxIR, smoothIR);
    minIR = min(minIR, smoothIR);
    long threshold = minIR + (long)((maxIR - minIR) * THRESHOLD_FACTOR);

    if (smoothIR > threshold && !rising && smoothIR > lastValue) rising = true;
    if (rising && smoothIR < lastValue) {
      unsigned long now = millis();
      unsigned long interval = now - lastBeatTime;
      if (interval > MIN_INTERVAL && interval < MAX_INTERVAL) {
        float bpm = 60000.0 / interval;
        bpmValues[bpmCount++] = bpm;
        if (rrCount < 50) rrIntervals[rrCount++] = interval;
      }
      lastBeatTime = now;
      rising = false;
    }
    lastValue = smoothIR;
    delay(SAMPLE_DELAY);
  }
  Serial.println("✅ Medición completada.");
}

void analizarEstado() {
  avgBPM = 0;
  for (int i = 0; i < rrCount; i++) avgBPM += bpmValues[i];
  avgBPM /= rrCount;

  float meanRR = 0;
  for (int i = 0; i < rrCount; i++) meanRR += rrIntervals[i];
  meanRR /= rrCount;

  sdnn = 0;
  for (int i = 0; i < rrCount; i++) sdnn += pow(rrIntervals[i] - meanRR, 2);
  sdnn = sqrt(sdnn / rrCount);

  rmssd = 0;
  for (int i = 1; i < rrCount; i++) rmssd += pow(rrIntervals[i] - rrIntervals[i - 1], 2);
  rmssd = sqrt(rmssd / (rrCount - 1));

  if (avgBPM > 90 && rmssd < 30) estadoEmocional = "ESTRESADO / ANSIOSO";
  else if (avgBPM >= 75 && avgBPM <= 90 && rmssd >= 25 && rmssd <= 40) estadoEmocional = "NEUTRO";
  else if (avgBPM >= 60 && avgBPM <= 75 && rmssd > 40) estadoEmocional = "RELAJADO";
  else estadoEmocional = "NEUTRO";
}

void enviarDatosPico() {
  Serial1.print("<BPM=");
  Serial1.print(avgBPM, 1);
  Serial1.print(";SDNN=");
  Serial1.print(sdnn, 1);
  Serial1.print(";RMSSD=");
  Serial1.print(rmssd, 1);
  Serial1.print(";ESTADO=");
  Serial1.print(estadoEmocional);
  Serial1.println(">");
  Serial.println("📤 Datos enviados al Pico W.");
}

// ===================================================================
//  BLOQUE PRINCIPAL DE RESPUESTA EMOCIONAL (ANIMACIÓN + AUDIO)
// ===================================================================
bool esperarConfirmacionFSR(unsigned long tiempo) {
  unsigned long inicio = millis();
  while (millis() - inicio < tiempo) {
    if (fsrPresionado()) return true;
    delay(50);
  }
  return false;
}

void ejecutarRutinaSegunEstado() {
  if (estadoEmocional == "ESTRESADO / ANSIOSO") {
    rutinaEstresAnsiosoConAudio();

  } else if (estadoEmocional == "RELAJADO") {
    rutinaRelajadoConAudio();

    myDFPlayer.play(7); // pregunta
    bool respuesta = esperarConfirmacionFSR(5000);

    if (respuesta) rutinaSiRelajadoConAudio();
    else rutinaNoRelajacionConAudio();

  } else if (estadoEmocional == "NEUTRO") {
    rutinaNeutroConAudio();
  }

  myDFPlayer.stop();
  tft.fillScreen(BLACK);
  Serial.println("🟡 Finalizando rutina, entrando a hibernación...");
}

// ===================================================================
//  CICLO PRINCIPAL (MAQUINA DE ESTADOS)
// ===================================================================
#define ESTADO_HIBERNACION 0
#define ESTADO_SALUDO 1
#define ESTADO_MEDICION 2
#define ESTADO_ANALISIS 3
int estadoActual = ESTADO_HIBERNACION;

void hibernar() {
  tft.fillScreen(BLACK);
  myDFPlayer.stop();
  delay(1000);
}

void reproducirSaludo() {
  rutinaSaludoConAudio(); // Usa animación y audio 0001.mp3
}

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200);
  Wire.begin();
  pinMode(pinFSR, INPUT);
  inicializarTFT();
  inicializarAudio();
  inicializarMAX30102();
  Serial.println("🔋 Sistema iniciado en modo HIBERNACIÓN...");
}

void loop() {
  switch (estadoActual) {
    case ESTADO_HIBERNACION:
      hibernar();
      if (fsrPresionado()) {
        reproducirSaludo();
        estadoActual = ESTADO_MEDICION;
      }
      break;

    case ESTADO_MEDICION:
      medirBiometria();
      estadoActual = ESTADO_ANALISIS;
      break;

    case ESTADO_ANALISIS:
      analizarEstado();
      enviarDatosPico();
      ejecutarRutinaSegunEstado();
      estadoActual = ESTADO_HIBERNACION;
      break;
  }
}

