/* Standalone companion: Mega + original TFT + FSR, with no host dependency.
 * Text and guided choices, not speech recognition or a language model.
 * No EEPROM write until the person explicitly chooses to save or forget.
 */
#include <EEPROM.h>
#include <Adafruit_GFX.h>
#include <MCUFRIEND_kbv.h>
#include <string.h>
#include "autonomy.h"

using namespace respaw_auto;

constexpr uint8_t FSR_PIN = A8;
constexpr uint16_t BLACK = 0x0000, WHITE = 0xFFFF, CYAN = 0x07FF;
MCUFRIEND_kbv display;
Memory memory;
Journal journal;
Controller companion;
PressureButton button;
bool diagnostic = false;
uint8_t shownScreen = 255, shownSelection = 255, shownStep = 255;
char command[16] = {};
uint8_t commandLength = 0;
bool commandOverflow = false;

const __FlashStringHelper *activityName(uint8_t activity) {
  if (activity == 0) return F("Una pausa breve");
  if (activity == 1) return F("Un paso pequeno");
  return F("Solo compania");
}

void line(int16_t y, const __FlashStringHelper *text, uint16_t color = WHITE) {
  display.setTextColor(color, BLACK);
  display.setCursor(18, y);
  display.print(text);
}

void selection(const __FlashStringHelper *text) {
  display.drawRoundRect(12, 181, display.width() - 24, 44, 7, CYAN);
  line(195, text, CYAN);
}

void stateEvent() {
  Serial.print(F("{\"v\":1,\"type\":\"autonomous_state\",\"screen\":"));
  Serial.print(static_cast<uint8_t>(companion.screen));
  Serial.print(F(",\"profile\":")); Serial.print(companion.profile);
  Serial.print(F(",\"selected\":")); Serial.print(companion.selected);
  Serial.print(F(",\"activity\":")); Serial.print(companion.activity);
  Serial.print(F(",\"diagnostic\":")); Serial.print(diagnostic ? F("true") : F("false"));
  Serial.print(F(",\"saved\":")); Serial.print(companion.saved ? F("true") : F("false"));
  Serial.print(F(",\"storage_error\":")); Serial.print(companion.storageError ? F("true") : F("false"));
  Serial.println('}');
}

void render(uint32_t now) {
  const uint8_t step = companion.screen == Screen::ACTIVITY ?
      static_cast<uint8_t>((now - companion.since) / 10000UL) : 0;
  if (shownScreen == static_cast<uint8_t>(companion.screen) &&
      shownSelection == companion.selected && shownStep == step) return;
  shownScreen = static_cast<uint8_t>(companion.screen);
  shownSelection = companion.selected;
  shownStep = step;
  display.fillScreen(BLACK);
  display.setTextSize(2);
  display.setTextWrap(false);
  line(15, F("RESPAW - A TU RITMO"), CYAN);
  if (companion.screen == Screen::IDLE) {
    line(63, F("Un momento para ti."));
    display.fillRoundRect(100, 100, 90, 56, 12, CYAN);
    display.fillRoundRect(290, 100, 90, 56, 12, CYAN);
    selection(F("Toca para empezar"));
  } else if (companion.screen == Screen::PROFILE) {
    line(57, F("Quien esta aqui?"));
    line(92, F("Elige siempre tu misma persona."));
    line(123, F("Invitado no guarda preferencias."));
    if (!companion.selected) selection(F("Invitado"));
    else {
      selection(F("Persona"));
      display.print(' '); display.print(companion.selected);
    }
  } else if (companion.screen == Screen::CHOICE) {
    line(57, F("Que te vendria bien ahora?"));
    if (companion.profile) {
      const Preference &p = memory.profiles[companion.profile - 1];
      if (p.last != NO_ACTIVITY) {
        line(89, F("En tu ultima respuesta guardada:"));
        line(114, activityName(p.last));
        line(139, p.lastRating == 1 ? F("Marcaste: me ayudo.") : F("Marcaste: no me ayudo."));
      } else line(104, F("Aun no guardaste preferencias."));
    } else line(104, F("Puedes probar o cambiar de idea."));
    if (companion.selected < ACTIVITY_COUNT) selection(activityName(companion.selected));
    else if (companion.profile && companion.selected == 3) selection(F("Olvidar mis preferencias"));
    else selection(F("Volver al inicio"));
  } else if (companion.screen == Screen::ACTIVITY) {
    line(57, activityName(companion.activity), CYAN);
    if (companion.activity == 0) {
      if (step == 0) {
        line(99, F("Si te resulta comodo,"));
        line(127, F("apoya los pies un momento."));
      } else if (step == 1) {
        line(99, F("Mira a tu alrededor."));
        line(127, F("No hace falta hacerlo perfecto."));
      } else {
        line(99, F("Date unos segundos mas."));
        line(127, F("Puedes terminar cuando quieras."));
      }
    } else if (companion.activity == 1) {
      if (step == 0) {
        line(99, F("Piensa en una tarea pendiente."));
        line(127, F("Solo una, por ahora."));
      } else if (step == 1) {
        line(99, F("Elige una parte muy pequena"));
        line(127, F("con la que puedas empezar."));
      } else {
        line(99, F("No tienes que acabarla ahora."));
        line(127, F("Puedes empezar cuando decidas."));
      }
    } else {
      line(99, F("Podemos estar en silencio"));
      line(127, F("un rato, sin pedirte nada."));
    }
    selection(F("Toca para terminar"));
  } else if (companion.screen == Screen::FEEDBACK) {
    line(57, F("Te sirvio este momento?"));
    line(99, companion.profile ? F("Tu respuesta puede orientar") : F("Estas en modo invitado."));
    line(127, companion.profile ? F("lo que te proponga otra vez.") : F("La respuesta no se guardara."));
    if (companion.selected == 0)
      selection(companion.profile ? F("Me ayudo - guardar") : F("Me ayudo"));
    else if (companion.selected == 1)
      selection(companion.profile ? F("No me ayudo - guardar") : F("No me ayudo"));
    else selection(F("Terminar sin guardar"));
  } else if (companion.screen == Screen::FORGET) {
    line(57, F("Olvidar esta persona?"));
    line(99, F("Se borraran sus preferencias."));
    line(127, F("Las otras personas se conservan."));
    selection(companion.selected ? F("Si, olvidar") : F("No, volver"));
  } else {
    line(75, companion.storageError ? F("No pude guardar el cambio.") :
         companion.forgotten ? F("Preferencias olvidadas.") :
         companion.saved ? F("Tu respuesta quedo guardada.") : F("Terminamos sin guardar."));
    line(119, F("Puedes volver cuando quieras."));
  }
  if (companion.screen != Screen::IDLE && companion.screen != Screen::ACTIVITY &&
      companion.screen != Screen::DONE) {
    line(248, F("Toque: cambiar  |  1s: elegir"));
  }
  line(280, F("Mantener 3s: volver al inicio"));
  stateEvent();
}

void interact(Gesture gesture, uint32_t now) {
  if (gesture == Gesture::NONE) return;
  const Memory before = memory;
  const Persist action = companion.handle(gesture, memory, now);
  if (action != Persist::NONE) {
    if (journal.save(EEPROM, memory)) {
      companion.saved = action == Persist::SAVE;
      companion.forgotten = action == Persist::FORGET;
      if (companion.forgotten) journal.clearOldSlots(EEPROM);
    } else {
      memory = before;
      companion.storageError = true;
    }
  }
}

void diagnostics(uint32_t now) {
  // Fixed diagnostic vocabulary; no model, arbitrary strings or actuator access.
  // DIAG ON is needed before simulated gestures, to isolate an unwired FSR.
  for (uint8_t n = 0; n < 32 && Serial.available(); ++n) {
    const char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (c < 32 || c > 126 || commandLength >= sizeof(command) - 1) commandOverflow = true;
      else if (!commandOverflow) command[commandLength++] = c;
      continue;
    }
    command[commandLength] = 0;
    if (!commandOverflow) {
      if (!strcmp(command, "DIAG ON")) {
        diagnostic = true;
        interact(Gesture::STOP, now);
      } else if (!strcmp(command, "DIAG OFF")) {
        diagnostic = false;
        button = PressureButton();
        interact(Gesture::STOP, now);
      } else if (!strcmp(command, "STOP")) interact(Gesture::STOP, now);
      else if (diagnostic && !strcmp(command, "NEXT")) interact(Gesture::NEXT, now);
      else if (diagnostic && !strcmp(command, "SELECT")) interact(Gesture::SELECT, now);
      else if (strcmp(command, "STATE")) Serial.println(F("{\"error\":\"unknown_command\"}"));
      stateEvent();
    }
    commandLength = 0;
    commandOverflow = false;
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(FSR_PIN, INPUT);
  journal.load(EEPROM, memory);
  uint16_t id = display.readID();
  if (id == 0xD3D3) id = 0x9488;
  display.begin(id);
  display.setRotation(1);
  companion.randomState = micros() | 1;
  Serial.println(F("{\"v\":1,\"type\":\"autonomous_ready\",\"host_required\":false}"));
  render(millis());
}

void loop() {
  const uint32_t now = millis();
  companion.tick(now);
  diagnostics(now);
  if (!diagnostic) interact(button.sample(analogRead(FSR_PIN), now), now);
  render(now);
}
