# ============================================================================
# ESP32 SMART HOME SECURITY SYSTEM v3.2
# MicroPython + Blynk IoT Cloud
# Схема на виртуалните пинове:
#   V0 — Осветление вкл/изкл   (Switch button)
#   V1 — Гараж Toggle 90°      (Push button)
#   V2 — Вход Toggle 90°       (Push button)
#   V5 — Статус аларма         (Label)
#   V6 — Статус врати          (Label)
#   V7 — Температура           (Chart)
#   V8 — Влажност              (Chart)
# ============================================================================

import machine
import time
import dht
import network
import BlynkLib

# ============================================================================
# === КОНФИГУРАЦИЯ НА ПИНОВЕТЕ ===============================================
# ============================================================================

# --- Сензори (входове) ---
MIC_PIN = 34          # Микрофон SY-213 за детекция на плясък (аналогов)
PIR_PIN = 18          # PIR датчик HC-SR501 за движение (дигитален)
SMOKE_PIN = 35        # Датчик за дим/газ MQ-2 (аналогов)
DHT_PIN = 4           # Датчик за температура и влажност DHT22 (дигитален)

# --- Изходи за управление ---
RELAY_PIN = 26        # Реле модул за управление на осветление
BUZZER_PIN = 19       # Бъзер модул за звукова аларма
SERVO_GARAGE_PIN = 5  # Сервомотор за гаражна врата (PWM)
SERVO_ENTRY_PIN = 23  # Сервомотор за входна врата (PWM)

# ============================================================================
# === НАСТРОЙКИ НА СИСТЕМАТА =================================================
# ============================================================================

# --- 🔐 Wi-Fi и Blynk настройки (ПРОМЕНИ ТУК!) ---
WIFI_SSID = "UKTC"          # ⚠️ Замени с твоето Wi-Fi име!
WIFI_PASSWORD = "uktc1234"           # ⚠️ Замени с твоята Wi-Fi парола!
BLYNK_AUTH_TOKEN = "rTZjrN8aXXD3o9Ek8kYbjPEETH7i6q_Q"    # ⚠️ Замени с токена от blynk.cloud!

# --- ⚡ Настройки за плясък (осветление) ---
PEAK_THRESHOLD = 2500           # Праг за детекция на плясък (0-4095)
CLAP_DEBOUNCE_MS = 1500         # Защита от ехо: игнорирай нови плясъци за 1.5 сек

# --- 🚨 Настройки за аларма (обща) ---
ALARM_DURATION_SEC = 10         # Колко дълго да трае алармата
ALARM_COOLDOWN_SEC = 30         # Пауза между две задействания на алармата

# --- 🔥 Настройки за датчик за дим ---
SMOKE_THRESHOLD = 3000          # Праг за детекция на дим (калибрирай!)
SMOKE_DEBOUNCE_MS = 2000        # Защита от фалшиви тревоги за дим

# --- 🔊 Настройки за звуков патърн на алармата ---
SMOKE_BEEP_ON_MS = 500          # Звук при дим (милисекунди)
SMOKE_BEEP_OFF_MS = 500         # Пауза при дим (милисекунди)
MOTION_BEEP_CONTINUOUS = True   # True = непрекъснат звук за движение

# --- 🚪 Настройки за сервомотори ---
SERVO_FREQ = 50                 # PWM честота за сервомотори (50Hz)
SERVO_MIN_DUTY = 26             # Duty cycle за 0° (затворено) — ~0.5ms пулс
SERVO_MAX_DUTY = 128            # Duty cycle за 180° (отворено) — ~2.5ms пулс
SERVO_CLOSED = 26               # Позиция: заключена врата (0°   → ~0.5ms)
SERVO_OPEN = 77                 # Позиция: отворена врата (90°  → ~1.5ms)
# ⚠️ SG90: duty 26=0°, 77=90°, 128=180°. За по-голям ъгъл увеличи SERVO_OPEN.

# --- 🌡️ Настройки за DHT сензор ---
DHT_READ_INTERVAL_MS = 2000     # Чети DHT на всеки 2 секунди

# ============================================================================
# === ИНИЦИАЛИЗАЦИЯ НА ХАРДУЕРА ==============================================
# ============================================================================

# --- 📡 Wi-Fi връзка ---
wlan = network.WLAN(network.STA_IF)
wlan.active(True)

def connect_wifi():
    """Функция за свързване към Wi-Fi мрежата."""
    print("📡 Свързване към Wi-Fi...", end="")
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        timeout = 0
        while not wlan.isconnected() and timeout < 30:
            time.sleep(1)
            print(".", end="")
            timeout += 1
    
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print(f"\n✅ Успешно свързване! IP адрес: {ip}")
        return True
    else:
        print("\n❌ Грешка: Не може да се свърже към Wi-Fi!")
        return False

# --- 🎤 Микрофон (аналогов вход) ---
mic_adc = machine.ADC(machine.Pin(MIC_PIN))
mic_adc.atten(machine.ADC.ATTN_11DB)

# --- 🔥 Датчик за дим (аналогов вход) ---
smoke_adc = machine.ADC(machine.Pin(SMOKE_PIN))
smoke_adc.atten(machine.ADC.ATTN_11DB)

# --- 🌡️ Датчик за температура и влажност (дигитален) ---
dht_sensor = dht.DHT22(machine.Pin(DHT_PIN))

# --- 💡 Реле за осветление (дигитален изход) ---
relay = machine.Pin(RELAY_PIN, machine.Pin.OUT)
relay.value(1)  # 1 = HIGH = ИЗКЛЮЧЕНО

# --- 🚶 PIR датчик за движение (дигитален вход) ---
pir_sensor = machine.Pin(PIR_PIN, machine.Pin.IN)
# ⚠️ БЕЗ PULL_DOWN! HC-SR501 има вграден pull-up резистор.
# Ако добавиш PULL_DOWN, ESP32 потиска сигнала → сензорът никога не чете HIGH.

# --- 🔊 Бъзер за аларма (дигитален изход) ---
buzzer = machine.Pin(BUZZER_PIN, machine.Pin.OUT)
buzzer.value(0)  # 0 = LOW = ИЗКЛЮЧЕНО

# --- 🚪 Сервомотори (PWM изход) ---
servo_garage = machine.PWM(machine.Pin(SERVO_GARAGE_PIN), SERVO_FREQ)
servo_garage.duty(SERVO_CLOSED)

servo_entry = machine.PWM(machine.Pin(SERVO_ENTRY_PIN), SERVO_FREQ)
servo_entry.duty(SERVO_CLOSED)

# ============================================================================
# === ПРОМЕНЛИВИ ЗА СЪСТОЯНИЕТО НА СИСТЕМАТА =================================
# ============================================================================

light_state = False
last_clap_time = 0
last_alarm_time = -1            # -1 = аларма никога не е задействана (без изчакване при старт)
alarm_active = False
alarm_start_time = 0
last_smoke_time = 0
alarm_reason = ""
alarm_type = ""
last_beep_change_time = 0
buzzer_state_in_pattern = False
garage_door_open = False
entry_door_open = False
last_dht_read_time = 0
last_temperature = 0.0
last_humidity = 0.0
blynk = None  # ⚠️ Ще се инициализира в main()

# ============================================================================
# === 📱 BLYNK ФУНКЦИИ (БЕЗ ДЕКОРАТОРИ - ръчна регистрация) ==================
# ============================================================================

# --- Функции за управление (дефинирани преди main) ---

def on_light_control(pin, value):
    """V0: Ръчно управление на осветлението от Blynk."""
    global light_state
    if value[0] == "1":
        light_state = True
        relay.value(0)
        print(">>> 💡 ОСВЕТЛЕНИЕ ВКЛ (Blynk) <<<")
    else:
        light_state = False
        relay.value(1)
        print(">>> 💡 ОСВЕТЛЕНИЕ ИЗКЛ (Blynk) <<<")

def on_garage_toggle(pin, value):
    """V1: Toggle гаражна врата — 1 бутон, 90° завъртане."""
    global garage_door_open
    if value[0] == "1":
        if garage_door_open:
            move_servo(servo_garage, SERVO_CLOSED)
            garage_door_open = False
            print(">>> 🚗 ГАРАЖ ЗАКЛЮЧЕН (Blynk) <<<")
        else:
            move_servo(servo_garage, SERVO_OPEN)
            garage_door_open = True
            print(">>> 🚗 ГАРАЖ ОТВОРЕН (Blynk) <<<")
        # Актуализира статуса на вратите в Blynk
        if blynk and blynk.connected():
            g = "🔓" if garage_door_open else "🔒"
            e = "🔓" if entry_door_open else "🔒"
            blynk.virtual_write(6, f"Гараж:{g} Вход:{e}")

def on_entry_toggle(pin, value):
    """V2: Toggle входна врата — 1 бутон, 90° завъртане."""
    global entry_door_open
    if value[0] == "1":
        if entry_door_open:
            move_servo(servo_entry, SERVO_CLOSED)
            entry_door_open = False
            print(">>> 🚪 ВХОД ЗАКЛЮЧЕН (Blynk) <<<")
        else:
            move_servo(servo_entry, SERVO_OPEN)
            entry_door_open = True
            print(">>> 🚪 ВХОД ОТВОРЕН (Blynk) <<<")
        # Актуализира статуса на вратите в Blynk
        if blynk and blynk.connected():
            g = "🔓" if garage_door_open else "🔒"
            e = "🔓" if entry_door_open else "🔒"
            blynk.virtual_write(6, f"Гараж:{g} Вход:{e}")

def on_temperature_read(pin):
    """V7: Изпращане на температура към Blynk (при заявка)."""
    blynk.virtual_write(7, f"{last_temperature:.1f}")

def on_humidity_read(pin):
    """V8: Изпращане на влажност към Blynk (при заявка)."""
    blynk.virtual_write(8, f"{last_humidity:.1f}")

def on_alarm_status_read(pin):
    """V5: Статус на алармата."""
    if alarm_active:
        blynk.virtual_write(5, f"⚠️ {alarm_reason}")
    else:
        blynk.virtual_write(5, "✅ OK")

def on_door_status_read(pin):
    """V6: Статус на вратите."""
    g = "🔓" if garage_door_open else "🔒"
    e = "🔓" if entry_door_open else "🔒"
    blynk.virtual_write(6, f"Гараж:{g} Вход:{e}")

def on_blynk_connected():
    """Извиква се при успешно свързване."""
    print("✅ 📱 Blynk свързан!")

def on_blynk_disconnected():
    """Извиква се при прекъсване на връзката."""
    print("❌ 📱 Blynk разкачен!")

def register_blynk_callbacks():
    """
    Функция за ръчна регистрация на Blynk събития.
    Извиква се САМО след като blynk обектът е инициализиран.
    """
    if not blynk:
        print("⚠️ Blynk не е инициализиран! Пропускам регистрация.")
        return
    
    # Регистриране на WRITE събития (от приложението към ESP32)
    blynk._events['write v0'] = on_light_control
    blynk._events['write v1'] = on_garage_toggle  # V1 = Гараж Toggle (90°)
    blynk._events['write v2'] = on_entry_toggle   # V2 = Вход Toggle  (90°)

    # Регистриране на READ събития (при заявка от приложението)
    blynk._events['read v7'] = on_temperature_read
    blynk._events['read v8'] = on_humidity_read
    blynk._events['read v5'] = on_alarm_status_read
    blynk._events['read v6'] = on_door_status_read
    
    # Регистриране на системни събития
    blynk._events['connect'] = on_blynk_connected
    blynk._events['disconnect'] = on_blynk_disconnected
    
    print("✅ Blynk callbacks registered!")

# ============================================================================
# === 🔧 ФУНКЦИИ ПОМОЩНИЦИ ===================================================
# ============================================================================

def move_servo(servo, duty):
    """Движи сервомотора до позиция и спира PWM сигнала след 1000ms."""
    # 1000ms е минимум за надеждно 180° движение при SG90/MG90S.
    # Ако моторът не достига крайната позиция, увеличи до 1200ms.
    servo.duty(duty)
    time.sleep_ms(1000)
    servo.duty(0)  # Спира PWM - предотвратява вибрации и прегряване

def read_dht_sensor():
    """Чете температура и влажност от DHT сензора."""
    global last_temperature, last_humidity
    try:
        dht_sensor.measure()
        temp = dht_sensor.temperature()
        hum  = dht_sensor.humidity()
        if temp is None or hum is None:
            return None, None
        # Някои версии на драйвера връщат суровата стойност x10
        if temp > 100 or temp < -40:
            temp = temp / 10.0
        if hum > 100 or hum < 0:
            hum = hum / 10.0
        # Финална проверка за реалистични стойности
        if -40 <= temp <= 80 and 0 <= hum <= 100:
            last_temperature = temp
            last_humidity    = hum
            return temp, hum
        else:
            print(f"⚠️ DHT нереалистични стойности: {temp}°C {hum}%")
    except Exception as e:
        print(f"⚠️ DHT грешка: {e}")
    return None, None

def trigger_alarm(reason, alarm_type_value):
    """Задейства алармата."""
    global alarm_active, alarm_start_time, last_alarm_time, alarm_reason, alarm_type
    global last_beep_change_time, buzzer_state_in_pattern
    
    alarm_active = True
    alarm_start_time = time.ticks_ms()
    last_alarm_time = time.ticks_ms()
    alarm_reason = reason
    alarm_type = alarm_type_value
    last_beep_change_time = alarm_start_time
    buzzer_state_in_pattern = True
    buzzer.value(1)
    
    print(f">>> 🚨 АЛАРМА! {reason}! (Тип: {alarm_type_value}) <<<")
    
    if blynk and blynk.connected():
        blynk.virtual_write(5, f"⚠️ {reason}")

def stop_alarm():
    """Спира алармата."""
    global alarm_active, alarm_reason, alarm_type, buzzer_state_in_pattern
    
    buzzer.value(0)
    alarm_active = False
    alarm_reason = ""
    alarm_type = ""
    buzzer_state_in_pattern = False
    
    print(">>> 🚨 Аларма спряна <<<")
    
    if blynk and blynk.connected():
        blynk.virtual_write(5, "✅ Система активна")

def update_buzzer_pattern():
    """Обновява звуковия патърн на бъзера."""
    global last_beep_change_time, buzzer_state_in_pattern
    
    current_time = time.ticks_ms()
    
    if alarm_type == "SMOKE":
        beep_on_time = SMOKE_BEEP_ON_MS
        beep_off_time = SMOKE_BEEP_OFF_MS
    elif alarm_type == "MOTION":
        if MOTION_BEEP_CONTINUOUS:
            buzzer.value(1)
            return
        else:
            beep_on_time = 300
            beep_off_time = 300
    else:
        return
    
    time_since_last_change_ms = time.ticks_diff(current_time, last_beep_change_time)
    
    if buzzer_state_in_pattern:
        if time_since_last_change_ms >= beep_on_time:
            buzzer.value(0)
            buzzer_state_in_pattern = False
            last_beep_change_time = current_time
    else:
        if time_since_last_change_ms >= beep_off_time:
            buzzer.value(1)
            buzzer_state_in_pattern = True
            last_beep_change_time = current_time

def check_pir_sensor():
    """Проверява PIR датчика за движение."""
    return pir_sensor.value() == 1

def check_smoke_sensor():
    """Проверява датчика за дим."""
    smoke_value = smoke_adc.read()
    if smoke_value > SMOKE_THRESHOLD:
        return True, smoke_value
    return False, smoke_value

def scan_for_clap():
    """Сканира микрофона за плясък (50ms прозорец)."""
    max_val = 0
    start_time = time.ticks_us()
    while time.ticks_diff(time.ticks_us(), start_time) < 50000:
        val = mic_adc.read()
        if val > max_val:
            max_val = val
    return max_val

# ============================================================================
# === 🚀 ОСНОВНА ПРОГРАМА (MAIN LOOP) ========================================
# ============================================================================

def main():
    """Основна функция на програмата."""
    global blynk, last_dht_read_time, light_state, last_clap_time, last_alarm_time
    global alarm_active, alarm_start_time, last_smoke_time, alarm_reason, alarm_type
    global last_beep_change_time, buzzer_state_in_pattern, garage_door_open, entry_door_open
    global last_temperature, last_humidity
    
    # --- 1. 📡 СВЪРЗВАНЕ КЪМ WI-FI ---
    if not connect_wifi():
        print("⚠️ Продължаване без Wi-Fi...")
    
    # --- 2. 📱 ИНИЦИАЛИЗАЦИЯ НА BLYNK ---
    try:
        blynk = BlynkLib.Blynk(
            BLYNK_AUTH_TOKEN,
            server='blynk.cloud',
            port=80,
            #port=443,
            log=print,
            insecure=True
        )
        print("✅ Blynk обект създаден!")
    except Exception as e:
        print(f"⚠️ Blynk грешка: {e}")
        blynk = None
    
    # --- 3. 🔑 РЕГИСТРАЦИЯ НА BLYNK СЪБИТИЯТА (СЛЕД ИНИЦИАЛИЗАЦИЯ!) ---
    register_blynk_callbacks()
    
    # --- 4. 📋 НАЧАЛНО СЪОБЩЕНИЕ ---
    print("\n" + "="*60)
    print("   🏠 ESP32 SMART HOME v3.1 (FIXED) 🏠")
    print("="*60)
    print("Time(ms) | MicPeak | Smoke | Motion | Light | Alarm")
    print("-"*60)
    
    # --- 5. 🔄 ГЛАВЕН ЦИКЪЛ ---
    try:
        while True:
            current_time = time.ticks_ms()
            
            # --- 5.1. 📱 ОБНОВЯВАНЕ НА BLYNK ---
            if blynk:
                try:
                    blynk.run()
                except Exception as e:
                    print(f"⚠️ Blynk run error: {e}")
            
            # --- 5.2. 🌡️ ЧЕТЕНЕ НА DHT ---
            if time.ticks_diff(current_time, last_dht_read_time) >= DHT_READ_INTERVAL_MS:
                temp, hum = read_dht_sensor()
                if temp is not None:
                    print(f"🌡️ {temp:.1f}°C | 💧 {hum:.1f}%")
                    # Push данните директно към Chart widget-а в Blynk
                    if blynk and blynk.connected():
                        blynk.virtual_write(7, round(temp, 1))
                        blynk.virtual_write(8, round(hum, 1))
                last_dht_read_time = current_time
            
            # --- 5.3. 🔊 ОБНОВЯВАНЕ НА ПАТЪРНА НА БЪЗЕРА ---
            if alarm_active:
                update_buzzer_pattern()
                elapsed_sec = time.ticks_diff(current_time, alarm_start_time) / 1000
                if elapsed_sec >= ALARM_DURATION_SEC:
                    stop_alarm()
            
            # --- Дефолтни стойности (предпазват от NameError) ---
            smoke_detected = False
            smoke_value = 0
            max_val = 0

            # --- 5.4. 🔥 ПРОВЕРКА ЗА ДИМ ---
            if not alarm_active:
                smoke_detected, smoke_value = check_smoke_sensor()
                if smoke_detected:
                    if time.ticks_diff(current_time, last_smoke_time) > SMOKE_DEBOUNCE_MS:
                        last_smoke_time = current_time
                        trigger_alarm("ЗАСЕЧЕН ДИМ", "SMOKE")
            else:
                smoke_detected, smoke_value = check_smoke_sensor()
            
            # --- 5.5. 🚶 ПРОВЕРКА ЗА ДВИЖЕНИЕ ---
            # Алармата се активира САМО ако И ДВЕТЕ врати са заключени (охранителен режим)
            # OR беше грешка — при OR алармата щеше да бие дори докато си вкъщи с отворена врата
            security_mode = (not garage_door_open) and (not entry_door_open)
            if not alarm_active and security_mode:
                if check_pir_sensor():
                    # last_alarm_time == -1 означава НИКОГА не е имало аларма → позволи веднага
                    # Иначе чакай ALARM_COOLDOWN_SEC секунди между две аларми
                    cooldown_ok = (last_alarm_time < 0) or \
                                  (time.ticks_diff(current_time, last_alarm_time) / 1000 >= ALARM_COOLDOWN_SEC)
                    if cooldown_ok:
                        trigger_alarm("ЗАСЕЧЕНО ДВИЖЕНИЕ", "MOTION")
            
            # --- 5.6. 🎤 ПРОВЕРКА ЗА ПЛЯСЪК ---
            if not alarm_active:
                max_val = scan_for_clap()
                if max_val > PEAK_THRESHOLD and time.ticks_diff(current_time, last_clap_time) > CLAP_DEBOUNCE_MS:
                    last_clap_time = current_time
                    light_state = not light_state
                    relay.value(0 if light_state else 1)
                    print(f">>> 💡 СВЕТЛИНА {'ВКЛ' if light_state else 'ИЗКЛ'} (Пик: {max_val}) <<<")
            else:
                max_val = scan_for_clap()
            
            # --- 5.7. 📊 ИЗВЕЖДАНЕ В КОНЗОЛАТА ---
            smoke_str = "YES" if smoke_detected else "no "
            motion = check_pir_sensor()  # за дисплея
            motion_str = "YES" if motion else "no "
            light_str = " ON " if light_state else " OFF"
            alarm_str = f"ON ({alarm_type})" if alarm_active else "off"
            
            print(f"{current_time:6d} | {max_val:5d} | {smoke_value:5d} |  {motion_str}   |  {light_str}  | {alarm_str}")
            
            # time.sleep_ms(10) премахнато — scan_for_clap() вече блокира 50ms,
            # допълнителното забавяне намаляваше отзивчивостта на Blynk бутоните.
    
    except KeyboardInterrupt:
        print("\n⚠️ Спиране на системата...")
        relay.value(1)
        buzzer.value(0)
        move_servo(servo_garage, SERVO_CLOSED)
        move_servo(servo_entry, SERVO_CLOSED)
        servo_garage.deinit()
        servo_entry.deinit()
        print("✅ Системата е изключена.")

# ============================================================================
# === ▶️ СТАРТ ===
# ============================================================================

if __name__ == "__main__":
    main()
