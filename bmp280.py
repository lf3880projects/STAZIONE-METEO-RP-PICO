from ustruct import unpack

class BMP280:
    def __init__(self, i2c, addr=0x76):
        self.i2c = i2c
        self.addr = addr

        # --- Lettura completa dei coefficienti Bosch ---
        calib1 = self.i2c.readfrom_mem(self.addr, 0x88, 24)
        calib2 = self.i2c.readfrom_mem(self.addr, 0xE1, 16)
        calib = calib1 + calib2

        # --- Coefficienti temperatura ---
        self.dig_T1, self.dig_T2, self.dig_T3 = unpack("<Hhh", calib[0:6])

        # --- Coefficienti pressione ---
        self.dig_P1, self.dig_P2, self.dig_P3, self.dig_P4, self.dig_P5, \
        self.dig_P6, self.dig_P7, self.dig_P8, self.dig_P9 = unpack("<Hhhhhhhhh", calib[6:24])

        # --- Configurazione BMP280 ---
        # oversampling x1, modalità normale
        self.i2c.writeto_mem(self.addr, 0xF4, b'\x27')
        self.i2c.writeto_mem(self.addr, 0xF5, b'\xA0')

    def _read_raw(self):
        data = self.i2c.readfrom_mem(self.addr, 0xF7, 6)

        # Debug opzionale:
        # print("Raw data:", data)

        pres_raw = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        temp_raw = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        return temp_raw, pres_raw

    def read(self):
        temp_raw, pres_raw = self._read_raw()

        # --- Calcolo temperatura Bosch ---
        var1 = (((temp_raw >> 3) - (self.dig_T1 << 1)) * self.dig_T2) >> 11
        var2 = (((((temp_raw >> 4) - self.dig_T1) *
                 ((temp_raw >> 4) - self.dig_T1)) >> 12) * self.dig_T3) >> 14
        t_fine = var1 + var2
        temp = (t_fine * 5 + 128) >> 8

        # --- Calcolo pressione Bosch ---
        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * self.dig_P6 / 32768.0
        var2 = var2 + var1 * self.dig_P5 * 2.0
        var2 = var2 / 4.0 + self.dig_P4 * 65536.0
        var1 = (self.dig_P3 * var1 * var1 / 524288.0 + self.dig_P2 * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * self.dig_P1

        if var1 == 0:
            pres = 0
        else:
            p = 1048576.0 - pres_raw
            p = ((p - var2 / 4096.0) * 6250.0) / var1
            var1 = self.dig_P9 * p * p / 2147483648.0
            var2 = p * self.dig_P8 / 32768.0
            pres = p + (var1 + var2 + self.dig_P7) / 16.0

        return temp / 100.0, pres / 100.0


