#include <Servo.h>

// ==========================
// CREACIÓN DE LOS SERVOS
// ==========================
Servo servo1;
Servo servo2;

// Pines de señal
const int pinServo1 = 9;
const int pinServo2 = 10;

// Posiciones iniciales
int anguloServo1 = 220;
int anguloServo2 = 0;


// ==========================
// SETUP
// ==========================
void setup() {

  Serial.begin(9600);

  // Conectar cada servo a su pin
  servo1.attach(pinServo1);
  servo2.attach(pinServo2);

  // Posición inicial
  servo1.write(anguloServo1);
  servo2.write(anguloServo2);

  delay(500);

  Serial.println("================================");
  Serial.println("      CONTROL DE 2 SERVOS");
  Serial.println("================================");
  Serial.println();
  Serial.println("Formato:");
  Serial.println("numeroServo angulo");
  Serial.println();
  Serial.println("Ejemplos:");
  Serial.println("1 120   -> Servo 1 a 120 grados");
  Serial.println("2 40    -> Servo 2 a 40 grados");
  Serial.println();
}


// ==========================
// LOOP
// ==========================
void loop() {

  if (Serial.available() > 0) {

    // Leer qué servo quieres controlar
    int servoSeleccionado = Serial.parseInt();

    // Leer el ángulo deseado
    int angulo = Serial.parseInt();


    // ==========================
    // VERIFICAR ÁNGULO
    // ==========================
    if (angulo >= 0 && angulo <= 180) {


      // ==========================
      // SERVO 1
      // ==========================
      if (servoSeleccionado == 1) {

        anguloServo1 = angulo;

        servo1.write(anguloServo1);

        Serial.print("Servo 1 -> ");
        Serial.print(anguloServo1);
        Serial.println(" grados");
      }


      // ==========================
      // SERVO 2
      // ==========================
      else if (servoSeleccionado == 2) {

        anguloServo2 = angulo;

        servo2.write(anguloServo2);

        Serial.print("Servo 2 -> ");
        Serial.print(anguloServo2);
        Serial.println(" grados");
      }


      // ==========================
      // SERVO NO VÁLIDO
      // ==========================
      else {

        Serial.println("ERROR: selecciona Servo 1 o Servo 2.");
      }

    }


    // ==========================
    // ÁNGULO NO VÁLIDO
    // ==========================
    else {

      Serial.println("ERROR: usa un angulo entre 0 y 180.");
    }


    // Limpiar caracteres restantes
    while (Serial.available() > 0) {
      Serial.read();
    }

    Serial.println("------------------------------");
    Serial.println("Ingresa otro comando:");
  }
}