# =============================================================================
# BlynkLib.py — MicroPython Blynk Client (порт 80, без SSL)
# Съвместима с: ESP32 + MicroPython + blynk.cloud (нов Blynk IoT)
# =============================================================================

import socket
import time

# --- Blynk протокол константи ---
MSG_RSP        = 200
MSG_LOGIN      = 2
MSG_PING       = 6
MSG_HW_SYNC    = 16
MSG_INTERNAL   = 17
MSG_HW         = 20
MSG_APP_SYNC   = 48
MSG_REDIRECT   = 41
MSG_BRIDGE     = 15
MSG_HW_LOGIN   = 29
MSG_EVENT_LOG  = 64

STATUS_OK      = 200
STATUS_INVALID_TOKEN = 9

# Размер на заглавката на Blynk съобщение (bytes)
HDR_LEN = 5

import struct

def _pack_header(msg_type, msg_id, msg_len):
    return struct.pack('!BHH', msg_type, msg_id, msg_len)

def _unpack_header(data):
    return struct.unpack('!BHH', data)

class BlynkError(Exception):
    pass

class Blynk:
    def __init__(self, token, server='blynk.cloud', port=80, insecure=True, log=None):
        self._token    = token
        self._server   = server
        self._port     = port
        self._log      = log if log else lambda *a, **k: None
        self._sock     = None
        self._msg_id   = 0
        self._events   = {}
        self._connected = False
        self._last_ping = 0
        self._connect()

    # -------------------------------------------------------------------------
    # Вътрешни помощни методи
    # -------------------------------------------------------------------------
    def _next_id(self):
        self._msg_id = (self._msg_id % 0xFFFF) + 1
        return self._msg_id

    def _send(self, msg_type, *args):
        data = '\0'.join(str(a) for a in args)
        data = data.encode('utf-8')
        hdr  = _pack_header(msg_type, self._next_id(), len(data))
        try:
            self._sock.send(hdr + data)
        except Exception as e:
            self._log(f'[Blynk] send error: {e}')
            self._connected = False

    def _recv(self, length, timeout_ms=1000):
        """Чете точно 'length' байта от сокета с таймаут."""
        self._sock.settimeout(timeout_ms / 1000.0)
        buf = b''
        try:
            while len(buf) < length:
                chunk = self._sock.recv(length - len(buf))
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        return buf

    # -------------------------------------------------------------------------
    # Свързване и логин
    # -------------------------------------------------------------------------
    def _connect(self):
        self._log(f'[Blynk] Свързване към {self._server}:{self._port}...')
        try:
            addr = socket.getaddrinfo(self._server, self._port)[0][-1]
            self._sock = socket.socket()
            self._sock.connect(addr)
            self._sock.setblocking(False)
        except Exception as e:
            self._log(f'[Blynk] Грешка при свързване: {e}')
            self._connected = False
            return

        # Изпрати Auth токен
        self._msg_id = 0
        auth_data = self._token.encode('utf-8')
        hdr = _pack_header(MSG_HW_LOGIN, self._next_id(), len(auth_data))
        try:
            self._sock.setblocking(True)
            self._sock.send(hdr + auth_data)
        except Exception as e:
            self._log(f'[Blynk] Грешка при изпращане на токен: {e}')
            self._connected = False
            return

        # Изчакай потвърждение
        resp = self._recv(HDR_LEN, timeout_ms=4000)
        if len(resp) < HDR_LEN:
            self._log('[Blynk] Няма отговор от сървъра!')
            self._connected = False
            return

        msg_type, msg_id, status = _unpack_header(resp)
        if status == STATUS_INVALID_TOKEN:
            self._log('[Blynk] ❌ Невалиден Auth Token!')
            self._connected = False
            return
        if status != STATUS_OK:
            self._log(f'[Blynk] ❌ Логин отказан, статус: {status}')
            self._connected = False
            return

        self._sock.setblocking(False)
        self._connected = True
        self._last_ping  = time.ticks_ms()
        self._log('[Blynk] ✅ Успешно свързан!')

        # Извикай connect callback ако има
        cb = self._events.get('connect')
        if cb:
            cb()

    # -------------------------------------------------------------------------
    # Публичен API
    # -------------------------------------------------------------------------
    def connected(self):
        return self._connected

    def virtual_write(self, pin, *val):
        """Изпраща стойност към виртуален пин в Blynk."""
        if not self._connected:
            return
        self._send(MSG_HW, 'vw', pin, *val)

    def sync_virtual(self, *pins):
        """Синхронизира виртуални пинове от облака."""
        if not self._connected:
            return
        for pin in pins:
            self._send(MSG_HW_SYNC, 'vr', pin)

    def notify(self, message):
        """Изпраща push известие."""
        if not self._connected:
            return
        self._send(MSG_EVENT_LOG, message)

    # -------------------------------------------------------------------------
    # Главен run() — извиква се в основния цикъл
    # -------------------------------------------------------------------------
    def run(self):
        if not self._connected:
            # Опитай повторно свързване след 5 секунди
            self._log('[Blynk] Опит за повторно свързване...')
            time.sleep(5)
            self._connect()
            return

        # --- Ping за поддържане на връзката (всеки 20 сек) ---
        if time.ticks_diff(time.ticks_ms(), self._last_ping) > 20000:
            self._send(MSG_PING)
            self._last_ping = time.ticks_ms()

        # --- Четене на входящи съобщения ---
        try:
            hdr = self._recv(HDR_LEN, timeout_ms=10)
            if len(hdr) < HDR_LEN:
                return  # Няма ново съобщение

            msg_type, msg_id, msg_len = _unpack_header(hdr)

            if msg_len == 0:
                body = b''
            else:
                body = self._recv(msg_len, timeout_ms=200)

            self._process_message(msg_type, msg_id, msg_len, body)

        except Exception as e:
            self._log(f'[Blynk] run() грешка: {e}')
            self._connected = False
            cb = self._events.get('disconnect')
            if cb:
                cb()

    # -------------------------------------------------------------------------
    # Обработка на входящи съобщения
    # -------------------------------------------------------------------------
    def _process_message(self, msg_type, msg_id, msg_len, body):
        # Ping от сървъра — отговори
        if msg_type == MSG_PING:
            hdr = _pack_header(MSG_RSP, msg_id, STATUS_OK)
            try:
                self._sock.setblocking(True)
                self._sock.send(hdr)
                self._sock.setblocking(False)
            except:
                pass
            return

        # Redirect — нов сървър
        if msg_type == MSG_REDIRECT:
            self._log('[Blynk] Redirect получен.')
            return

        # Response — потвърждение
        if msg_type == MSG_RSP:
            return

        # Hardware съобщение (vw / vr)
        if msg_type in (MSG_HW, MSG_HW_SYNC, MSG_BRIDGE, MSG_INTERNAL):
            parts = body.decode('utf-8').split('\0')
            if len(parts) < 2:
                return

            cmd = parts[0]   # 'vw' или 'vr'
            pin = parts[1]   # номер на пина като стринг

            if cmd == 'vw':
                value = parts[2:]
                event_key = f'write v{pin}'
                cb = self._events.get(event_key)
                if cb:
                    cb(int(pin), value)

            elif cmd == 'vr':
                event_key = f'read v{pin}'
                cb = self._events.get(event_key)
                if cb:
                    cb(int(pin))
                    # Потвърди на сървъра
                    hdr = _pack_header(MSG_RSP, msg_id, STATUS_OK)
                    try:
                        self._sock.setblocking(True)
                        self._sock.send(hdr)
                        self._sock.setblocking(False)
                    except:
                        pass


