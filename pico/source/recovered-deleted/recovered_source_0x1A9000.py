from machine import Pin, I2C
import time
import max30102

# Inicializar I2C
i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=400000)

# Inicializar el sensor
sensor = max30102.MAX30102(i2c=i2c)

# Verificar que el sensor responde
print("MAX30102 encontrado:", sensor.check())

# Leer valores Red e IR continuamente
while True:
    red, ir = sensor.read_sequential()   # devuelve listas de datos
    if red and ir:
        print("RED:", red[-1], " IR:", ir[-1])
        if ir[-1] > 50000:   # umbral aproximado
            print("✅ Señal detectada (hay dedo)")
        else:
            print("❌ No hay dedo/sin señal")
    time.sleep(0.5)
