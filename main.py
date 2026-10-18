import network
import socket
import time
import json
from machine import Pin, ADC, I2C
from bme280 import BME280
import math
import ntptime   # <<< AGGIUNTA NTP >>>

# LED verde su GP9
ledGreen = Pin(9, Pin.OUT)
# LED rosso su GP14
ledRed = Pin(14, Pin.OUT)

# --- Parametri MQ135 ---
R1 = 15000
R2 = 9100
Vcc = 5.0
RL  = 10000
R0  = 84000

A = 116.6020682
B = -2.769034857

def mq135_ppm(mq_volt):
    Vadc = mq_volt
    VAO = Vadc * (R1 + R2) / R2
    Rs = RL * (Vcc - VAO) / VAO
    ratio = Rs / R0
    ppm = A * (ratio ** B)
    return ppm

def update_latest_readings():
    global latest_temp, latest_pres, latest_hum, latest_voc

    temp, pres, hum, voc = read_sensors()
    pres = pres + 38  # offset LAMMA
    ledGreen.toggle()

    latest_temp = temp
    latest_pres = pres
    latest_hum  = hum
    latest_voc  = voc

    return temp, pres, hum, voc

# --- WiFi ---
SSID = "TIM-29602335"
PASSWORD = "pFMdelqcpz0kQ020XEYe6fXW"
ledRed.value(1)
time.sleep(1)
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)

print("Connessione WiFi...")
timeout = time.time() + 8
while not wlan.isconnected() and time.time() < timeout:
    time.sleep(0.5)

if wlan.isconnected():
    print("Connesso:", wlan.ifconfig())
    ledRed.value(0)
    # --- SINCRONIZZAZIONE ORA VIA NTP ---
    try:
        ntptime.settime()
        print("Ora sincronizzata via NTP (UTC)")
    except Exception as e:
        print("Errore NTP:", e)
else:
    print("Avvio senza WiFi, tenterò più tardi")
    ledRed.value(1)

# <<< AGGIUNTA NTP >>>
last_ntp_sync = time.time()

def try_ntp_sync():
    global last_ntp_sync

    if time.time() - last_ntp_sync >= 3600:
        print("Tentativo di sincronizzazione NTP...")

        try:
            ntptime.settime()
            last_ntp_sync = time.time()
            print("Ora sincronizzata via NTP (UTC)")
        except Exception as e:
            print("Errore NTP:", e)
            last_ntp_sync += 60
# <<< FINE AGGIUNTA NTP >>>


# --- Sensori ---
i2c = I2C(0, scl=Pin(1), sda=Pin(0))
sensor = BME280(i2c)
mq_adc = ADC(26)

# --- Dashboard HTML ---
with open("indexRT.html", "r") as f:
    html_page = f.read()

# --- Variabili globali ---
latest_temp = 0
latest_pres = 0
latest_hum  = 0
latest_voc  = 0

cycle_count = 0
sum_temp = 0
sum_pres = 0
sum_hum  = 0
sum_voc  = 0

browser_connected = False
last_browser_request = 0
offline_buffer = []

# --- Lettura sensori ---
def read_sensors():
    temp, pres_hpa, hum = sensor.read()
    mq_raw = mq_adc.read_u16()
    mq_volt = mq_raw * 3.3 / 65535
    voc_ppm = mq135_ppm(mq_volt)
    return temp, pres_hpa, hum, voc_ppm

# --- TIMESTAMP CORRETTO IN UTC ---
def get_timestamp():
    t = time.localtime()
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )

# --- Invio a ThingSpeak ---
def send_to_thingspeak(record):
    import urequests

    API_KEY = "B350RZQDWFH1YD8E"

    url = (
        "https://api.thingspeak.com/update?"
        "api_key=" + API_KEY +
        "&field1=" + str(record["temp"]) +
        "&field2=" + str(record["pres"]) +
        "&field3=" + str(record["hum"]) +
        "&field4=" + str(record["voc"]) +
        "&created_at=" + record["timestamp"]
    )

    try:
        r = urequests.get(url)
        r.close()
        return True
    except Exception as e:
        print("Errore invio ThingSpeak:", e)
        return False

# --- Aggiornamento medie ---
def update_averages(temp, pres, hum, voc):
    global sum_temp, sum_pres, sum_hum, sum_voc, cycle_count

    sum_temp += temp
    sum_pres += pres
    sum_hum  += hum
    sum_voc  += voc

    cycle_count += 1

    if cycle_count == 150:
        avg_temp = sum_temp / 150
        avg_pres = sum_pres / 150
        avg_hum  = sum_hum  / 150
        avg_voc  = sum_voc  / 150

        ts = get_timestamp()

        cycle_count = 0
        sum_temp = 0
        sum_pres = 0
        sum_hum  = 0
        sum_voc  = 0

        record = {
            "timestamp": ts,
            "temp": avg_temp,
            "pres": avg_pres,
            "hum": avg_hum,
            "voc": avg_voc
        }

        if wlan.isconnected():
            ledRed.value(0)
            print("Medie 5 minuti ONLINE:", record)

            ok = send_to_thingspeak(record)

            if ok:
                print("Inviato a ThingSpeak")

                if offline_buffer:
                    print("Invio dati arretrati:")
                    for r in offline_buffer:
                        send_to_thingspeak(r)
                    offline_buffer.clear()
            else:
                print("Errore invio, salvo nel buffer")
                offline_buffer.append(record)

        else:
            ledRed.value(1)
            print("Medie 5 minuti OFFLINE, aggiungo al buffer")
            offline_buffer.append(record)

            print("Tentativo di riconnessione WiFi...")
            wlan.connect(SSID, PASSWORD)

            timeout = time.time() + 4
            while not wlan.isconnected() and time.time() < timeout:
                time.sleep(0.2)

            if wlan.isconnected():
                print("Connesso:", wlan.ifconfig())
                ledRed.value(0)
            else:
                print("Ancora offline, ritenterò più tardi")
                ledRed.value(1)


# --- Web server ROBUSTO ---
addr = socket.getaddrinfo("0.0.0.0", 8080)[0][-1]
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(addr)
s.listen(1)
s.settimeout(0.1)

print("Server web attivo su http://", wlan.ifconfig()[0], ":8080")

last_poll_time = time.time()

while True:

    try_ntp_sync()

    if browser_connected and (time.time() - last_browser_request > 10):
        browser_connected = False
        print("Browser scollegato")

    if not browser_connected:
        if time.time() - last_poll_time >= 2:
            last_poll_time = time.time()
            temp, pres, hum, voc = update_latest_readings()
            update_averages(temp, pres, hum, voc)

    try:
        client, addr = s.accept()
        client.settimeout(0.2)

        try:
            request = client.recv(1024).decode()
        except:
            request = ""

        # --- /data ---
        if "GET /data" in request:

            browser_connected = True
            last_browser_request = time.time()

            temp, pres, hum, voc = update_latest_readings()
            update_averages(temp, pres, hum, voc)

            data = {
                "temp": round(temp, 2),
                "pres": round(pres, 1),
                "hum":  round(hum, 1),
                "mq":   round(voc, 3)
            }

            response = json.dumps(data)

            client.send(
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Connection: close\r\n\r\n"
            )
            client.send(response)

        # --- Pagina HTML ---
        elif "GET /" in request:

            temp, pres, hum, voc = update_latest_readings()
            browser_connected = True
            last_browser_request = time.time()

            client.send(
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html\r\n"
                "Connection: close\r\n\r\n"
            )
            client.send(html_page)

        else:
            client.send(
                "HTTP/1.1 404 Not Found\r\n"
                "Connection: close\r\n\r\n"
            )

        client.close()

    except Exception:
        pass

