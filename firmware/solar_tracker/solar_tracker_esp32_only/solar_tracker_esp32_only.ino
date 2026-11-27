#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <math.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <esp_timer.h>
#include "solar_telemetry.h"

// Full NREL SPA C implementation: place spa.c and spa.h in this sketch folder.
extern "C" {
  #include "spa.h"
}

// ============================================================================
// CONFIGURATION (Fixed installation coordinates when GPS is disabled)
// ============================================================================
constexpr bool USE_GPS_FOR_TRACKING = false;
double LATITUDE  = 3.007416667;    // Degrees North (+N, -S)
double LONGITUDE = 101.720583333;  // Degrees East (+E, -W)
const int TZ_OFFSET_HOURS = 8; // Malaysia = UTC+8

// NREL SPA environmental/time inputs. Update Delta T and DUT1 from the
// latest USNO/IERS Earth-orientation bulletin when maintaining the system.
// Values below use the IERS Bulletin A prediction for 25 Nov 2026:
// TAI-UTC=37 s, DUT1=-0.08417 s. Delta T = TT-UT1 =
// 32.184 + (TAI-UTC) - DUT1 = 69.26817 s. Update for later dates.
const double SPA_DELTA_T_SECONDS  = 69.26817;
const double SPA_DELTA_UT1_SECONDS = -0.08417;
const double SPA_PRESSURE_MBAR    = 1013.25;
const double SPA_TEMPERATURE_C    = 25.0;
const double SPA_ATMOS_REFRACT_DEG = 0.5667;

bool USE_MANUAL_TEST_ANGLES = false;
double MANUAL_TARGET_AZ  = 120.0;
double MANUAL_TARGET_ZEN = 40.0;

// Control thresholds & speeds
const double AZ_CONTROL_THRESHOLD_DEG  = 1.0;
const double ZEN_CONTROL_THRESHOLD_DEG = 1.0;
const int    AZ_SPEED_PWM              = 64;   // 64/255 = 25.10%
const int    ZEN_SPEED_PWM             = 51;   // 51/255 = 20.00%
const double AZ_STOP_DEADBAND          = 1.0;
const double AZ_BACK_ALLOW_DEG         = 10.0;
const double ZEN_STOP_DEADBAND         = 1.0;

// ============================================================================
// PIN DEFINITIONS (Updated per Dual-axis solar tracker schematic)
// ============================================================================
#define I2C_SDA 21       // ESP32 GPIO21 -> MPU-6050 SDA
#define I2C_SCL 22       // ESP32 GPIO22 -> MPU-6050 SCL

#define PWM_A   25       // ESP32 GPIO25 -> MDD10A PWM1 (yaw)
#define DIR_A   26       // ESP32 GPIO26 -> MDD10A DIR1 (yaw)
#define PWM_B   27       // ESP32 GPIO27 -> MDD10A PWM2 (pitch)
#define DIR_B   14       // ESP32 GPIO14 -> MDD10A DIR2 (pitch)

#define GPS_RX2 16       // ESP32 GPIO16 / RX2 <- GPS GY-GPS6MU2 TX
#define GPS_TX2 17       // ESP32 GPIO17 / TX2 -> GPS GY-GPS6MU2 RX
#define GPS_BAUD 9600

#define PWM_FREQ       1000
#define PWM_RESOLUTION 8

// ============================================================================
// I2C SENSORS: Two MPU-6050 devices (0x68 and 0x69) + optional INA219
// ============================================================================
#define AZIMUTH_ADDR 0x68     // Schematic specifies MPU-6050 AD0 -> GND (0x68)
#define ZENITH_ADDR  0x69     // Required second MPU-6050 (AD0 -> 3.3V)

#define MPU_PWR_MGMT_1   0x6B
#define MPU_ACCEL_XOUT_H 0x3B
#define MPU_GYRO_CONFIG  0x1B
#define MPU_GYRO_ZOUT_H  0x47

Adafruit_INA219 ina219;
bool ina219Ready = false;
bool azimuthMpuReady = false;
bool legacyYawCalibrated = false;
bool zenithMpuReady = false;
bool ina219SampleAvailable = false;
bool azimuthMpuSampleAvailable = false;
bool zenithMpuSampleAvailable = false;
bool motorPwmReady = false;
bool yawPwmReady = false;
bool localYawControl = false;
bool fixedYawMode = true;
SolarFixedYaw fixedYaw;
SolarYawSequence yawSequence;

// ============================================================================
// SENSOR FILTER, ENERGY, GPS & CLOCK STATE
// ============================================================================
double filteredZenith = 0.0;
bool zenithFilterInitialized = false;
const double ALPHA_ZEN = 0.97;

double gyroBiasRaw = 0.0;
double yawAngleDeg = 0.0;
double yawOffsetDeg = 0.0;
double filteredGzDegPerSec = 0.0;
const double ALPHA_GYRO = 0.90;
const double GYRO_DEADBAND_DPS = 0.30;
unsigned long lastGyroMicros = 0;

double energyWh = 0.0;
unsigned long lastEnergyMillis = 0;

bool gpsHasFix = false;
bool gpsTimeValid = false;
unsigned long lastGpsFixMillis = 0;
int64_t lastGpsFixUs = -1;
unsigned long lastGpsTimeMillis = 0;
double GPS_ALTITUDE_M = 50.0;  // GGA altitude replaces this when available
bool gpsAltitudeValid = false;
const unsigned long GPS_FIX_TIMEOUT_MS = 10000UL;
char nmeaBuf[128];
size_t nmeaIdx = 0;

// Local clock (UTC+8) for SPA and Serial output.
bool clockSetBySerial = false;
int simYear   = 2026;
int simMonth  = 11;
int simDay    = 25;
int simHour   = 11;
int simMinute = 18;
int simSecond = 0;
unsigned long lastClockMillis = 0;
time_t fixedRealAnchor = 0;
time_t fixedSolarAnchor = 0;
int64_t fixedClockAnchorUs = 0;
uint16_t fixedDemoSpeed = 1;
double fixedTargetAz = NAN;
double fixedTargetZen = NAN;
bool fixedSpaError = false;

// ============================================================================
// LOW-LEVEL I2C & MPU-6050 HELPERS
// ============================================================================
bool i2cDevicePresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return (Wire.endTransmission() == 0);
}

bool writeMPU8(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

int16_t readWordMPU(uint8_t addr, uint8_t reg) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  const bool addressed = Wire.endTransmission(false) == 0;
  if (!addressed || Wire.requestFrom(addr, (uint8_t)2, (uint8_t)true) != 2 || Wire.available() < 2) {
    if (addr == AZIMUTH_ADDR) azimuthMpuSampleAvailable = false;
    if (addr == ZENITH_ADDR) zenithMpuSampleAvailable = false;
    return 0;
  }
  uint8_t high = Wire.read();
  uint8_t low  = Wire.read();
  return (int16_t)((high << 8) | low);
}

void initAzimuthMpu() {
  // Same base gyro clock and ±250 deg/s scale as the rotation-only sketch.
  legacyYawCalibrated = false;
  azimuthMpuReady = writeMPU8(AZIMUTH_ADDR, MPU_PWR_MGMT_1, 0x01) &&
      writeMPU8(AZIMUTH_ADDR, MPU_GYRO_CONFIG, 0x00);
  azimuthMpuSampleAvailable = azimuthMpuReady;
  if (azimuthMpuReady) {
    Serial.println("Azimuth MPU-6050 (0x68) : Detected");
  } else {
    Serial.println("ERROR: Azimuth MPU-6050 (0x68) not detected");
  }
  delay(50);
}

void initMPU6050() {
  initAzimuthMpu();

  // Use a dedicated second MPU6050 for zenith feedback.
  // Do not substitute the azimuth sensor when the second sensor is absent.
  zenithMpuReady = i2cDevicePresent(ZENITH_ADDR);
  zenithMpuSampleAvailable = zenithMpuReady;
  if (zenithMpuReady) {
    writeMPU8(ZENITH_ADDR, MPU_PWR_MGMT_1, 0x00);
    Serial.println("Zenith MPU-6050 (0x69) : Detected");
  } else {
    Serial.println("ERROR: Zenith MPU-6050 (0x69) not detected; tracking will remain stopped");
  }
  delay(100);
}

void calibrateAzimuthGyro(int samples = 1000) {
  legacyYawCalibrated = false;
  if (!azimuthMpuReady) {
    Serial.println("ERROR: Azimuth MPU-6050 unavailable; gyro calibration skipped.");
    return;
  }
  Serial.println("===================================");
  Serial.println("KEEP MPU-6050 STILL");
  Serial.println("CALIBRATING AZIMUTH GYRO...");
  Serial.println("===================================");

  long sum = 0;
  azimuthMpuSampleAvailable = true;
  for (int i = 0; i < samples; i++) {
    sum += readWordMPU(AZIMUTH_ADDR, MPU_GYRO_ZOUT_H);
    if (!azimuthMpuSampleAvailable) {
      azimuthMpuReady = false;
      Serial.println("Azimuth gyro calibration failed. Check wiring and reset.");
      return;
    }
    delay(2);
  }
  gyroBiasRaw = (double)sum / (double)samples;
  legacyYawCalibrated = true;
  lastGyroMicros = micros();

  Serial.print("Gyro Bias: ");
  Serial.println(gyroBiasRaw, 2);
  Serial.println("Calibration Complete\n");
}

double getZenithTilt0To90() {
  if (!zenithMpuReady) return NAN;
  const uint8_t addr = ZENITH_ADDR;
  double accX = readWordMPU(addr, MPU_ACCEL_XOUT_H)     / 16384.0;
  double accY = readWordMPU(addr, MPU_ACCEL_XOUT_H + 2) / 16384.0;
  double accZ = readWordMPU(addr, MPU_ACCEL_XOUT_H + 4) / 16384.0;

  double horiz = sqrt(accX * accX + accY * accY);
  double angleDeg = atan2(horiz, accZ) * 180.0 / M_PI;

  if (!zenithFilterInitialized) {
    filteredZenith = angleDeg;
    zenithFilterInitialized = true;
  } else {
    filteredZenith = ALPHA_ZEN * filteredZenith + (1.0 - ALPHA_ZEN) * angleDeg;
  }
  return filteredZenith;
}

double wrapBearing(double bearing) {
  const double wrapped = fmod(bearing, 360.0);
  return wrapped < 0.0 ? wrapped + 360.0 : wrapped;
}

double correctedYawDeg() {
  return wrapBearing(yawAngleDeg + yawOffsetDeg);
}

double getAzimuthYawDeg() {
  if (!azimuthMpuReady || !legacyYawCalibrated) {
    azimuthMpuSampleAvailable = false;
    return NAN;
  }
  unsigned long nowMicros = micros();
  double dt = (nowMicros - lastGyroMicros) / 1000000.0;
  lastGyroMicros = nowMicros;
  if (dt <= 0.0 || dt > 1.0) dt = 0.01;

  int16_t rawGz = readWordMPU(AZIMUTH_ADDR, MPU_GYRO_ZOUT_H);
  double gzDps = ((double)rawGz - gyroBiasRaw) / 131.0;

  filteredGzDegPerSec = ALPHA_GYRO * filteredGzDegPerSec + (1.0 - ALPHA_GYRO) * gzDps;

  // Clear the filtered rate inside the gyro deadband.
  if (fabs(filteredGzDegPerSec) < GYRO_DEADBAND_DPS) {
    filteredGzDegPerSec = 0.0;
  }

  yawAngleDeg += filteredGzDegPerSec * dt;
  yawAngleDeg = fmod(yawAngleDeg, 360.0);
  if (yawAngleDeg < 0.0) yawAngleDeg += 360.0;

  return correctedYawDeg();
}

// ============================================================================
// INA219 SOLAR POWER & ENERGY (OPTIONAL IF WIRED ON I2C)
// ============================================================================
void initINA219() {
  if (ina219.begin()) {
    ina219Ready = true;
    Serial.println("INA219 : Connected");
  } else {
    ina219Ready = false;
    Serial.println("INA219 : Not detected");
  }
  ina219SampleAvailable = ina219Ready;
}

void readSolarPower(float &voltageV, float &currentA, float &powerW) {
  if (!ina219Ready) {
    voltageV = 0.0f;
    currentA = 0.0f;
    powerW   = 0.0f;
    return;
  }
  voltageV = ina219.getBusVoltage_V();
  const bool voltageRead = ina219.success();
  currentA = ina219.getCurrent_mA() / 1000.0f;
  ina219SampleAvailable = voltageRead && ina219.success();
  powerW   = voltageV * currentA;
}

void updateEnergyWh(float powerW) {
  unsigned long nowMs = millis();
  double dtSec = (nowMs - lastEnergyMillis) / 1000.0;
  lastEnergyMillis = nowMs;
  if (dtSec > 0.0 && dtSec < 10.0 && powerW > 0.0f) {
    energyWh += (double)powerW * (dtSec / 3600.0);
  }
}

// ============================================================================
// NMEA GPS PARSER (GY-GPS6MU2 / RMC & GGA)
// ============================================================================
bool isLeapYear(int y) {
  return ((y % 4 == 0 && y % 100 != 0) || (y % 400 == 0));
}

int daysInMonth(int y, int m) {
  static const int days[12] = {31,28,31,30,31,30,31,31,30,31,30,31};
  if (m == 2 && isLeapYear(y)) return 29;
  return days[(m - 1 + 12) % 12];
}

void setLocalTimeFromUTC(int y, int mo, int d, int h, int mi, int s) {
  h += TZ_OFFSET_HOURS;
  while (h >= 24) {
    h -= 24;
    d += 1;
    int dim = daysInMonth(y, mo);
    if (d > dim) {
      d = 1;
      mo += 1;
      if (mo > 12) {
        mo = 1;
        y += 1;
      }
    }
  }
  while (h < 0) {
    h += 24;
    d -= 1;
    if (d < 1) {
      mo -= 1;
      if (mo < 1) {
        mo = 12;
        y -= 1;
      }
      d = daysInMonth(y, mo);
    }
  }
  simYear = y; simMonth = mo; simDay = d;
  simHour = h; simMinute = mi; simSecond = s;
  lastClockMillis = millis();
}

double nmeaCoordToDecimal(const char* field, char hemi) {
  if (!field || field[0] == '\0') return NAN;
  double val = atof(field);
  int deg = (int)(val / 100.0);
  double minutes = val - (deg * 100.0);
  double dec = (double)deg + (minutes / 60.0);
  if (hemi == 'S' || hemi == 'W') dec = -dec;
  return dec;
}

bool verifyNmeaChecksum(const char* sentence) {
  if (sentence[0] != '$') return false;
  const char* star = strchr(sentence, '*');
  if (!star || strlen(star) < 3) return false;

  uint8_t cs = 0;
  for (const char* p = sentence + 1; p < star; p++) {
    cs ^= (uint8_t)(*p);
  }
  uint8_t expected = (uint8_t)strtol(star + 1, nullptr, 16);
  return (cs == expected);
}

void parseNmeaSentence(char* sentence) {
  if (!verifyNmeaChecksum(sentence)) return;

  char* star = strchr(sentence, '*');
  if (star) *star = '\0';

  char* fields[20] = {nullptr};
  int count = 0;
  char* p = sentence;
  fields[count++] = p;
  while (*p && count < 20) {
    if (*p == ',') {
      *p = '\0';
      fields[count++] = p + 1;
    }
    p++;
  }

  // Match *RMC (fields: 1=UTC hhmmss.ss, 2=A/V, 3=lat, 4=N/S, 5=lon, 6=E/W, 9=ddmmyy)
  if (count >= 10 && strlen(fields[0]) >= 6 && strcmp(fields[0] + strlen(fields[0]) - 3, "RMC") == 0) {
    if (fields[2] && fields[2][0] == 'A') {
      double lat = nmeaCoordToDecimal(fields[3], fields[4] ? fields[4][0] : 'N');
      double lon = nmeaCoordToDecimal(fields[5], fields[6] ? fields[6][0] : 'E');
      if (!isnan(lat) && !isnan(lon)) {
        LATITUDE = lat;
        LONGITUDE = lon;
        gpsHasFix = true;
        lastGpsFixMillis = millis();
        lastGpsFixUs = esp_timer_get_time();
      }
      if (fields[1] && strlen(fields[1]) >= 6 && fields[9] && strlen(fields[9]) == 6) {
        int hh = (fields[1][0] - '0') * 10 + (fields[1][1] - '0');
        int mm = (fields[1][2] - '0') * 10 + (fields[1][3] - '0');
        int ss = (fields[1][4] - '0') * 10 + (fields[1][5] - '0');
        int dd = (fields[9][0] - '0') * 10 + (fields[9][1] - '0');
        int mo = (fields[9][2] - '0') * 10 + (fields[9][3] - '0');
        int yy = 2000 + (fields[9][4] - '0') * 10 + (fields[9][5] - '0');
        if (hh <= 23 && mm <= 59 && ss <= 59 && mo >= 1 && mo <= 12 &&
            dd >= 1 && dd <= daysInMonth(yy, mo)) {
          setLocalTimeFromUTC(yy, mo, dd, hh, mm, ss);
          gpsTimeValid = true;
          lastGpsTimeMillis = millis();
        }
      }
    }
  }
  // Match *GGA (fields: 2=lat, 3=N/S, 4=lon, 5=E/W, 6=fix quality > 0)
  else if (count >= 7 && strlen(fields[0]) >= 6 && strcmp(fields[0] + strlen(fields[0]) - 3, "GGA") == 0) {
    int fixQual = fields[6] ? atoi(fields[6]) : 0;
    if (fixQual > 0) {
      double lat = nmeaCoordToDecimal(fields[2], fields[3] ? fields[3][0] : 'N');
      double lon = nmeaCoordToDecimal(fields[4], fields[5] ? fields[5][0] : 'E');
      if (!isnan(lat) && !isnan(lon)) {
        LATITUDE = lat;
        LONGITUDE = lon;
        gpsHasFix = true;
        lastGpsFixMillis = millis();
        lastGpsFixUs = esp_timer_get_time();

        // GGA field 9 is altitude above mean sea level; field 10 should be M.
        if (count >= 11 && fields[9] && fields[9][0] != '\0' &&
            fields[10] && strcmp(fields[10], "M") == 0) {
          double altitude = atof(fields[9]);
          if (isfinite(altitude) && altitude > -500.0 && altitude < 10000.0) {
            GPS_ALTITUDE_M = altitude;
            gpsAltitudeValid = true;
          }
        }
      }
    }
  }
}

void pollGPS() {
  while (Serial2.available() > 0) {
    char c = (char)Serial2.read();
    if (c == '\r') continue;
    if (c == '\n') {
      nmeaBuf[nmeaIdx] = '\0';
      if (nmeaIdx > 6) {
        parseNmeaSentence(nmeaBuf);
      }
      nmeaIdx = 0;
    } else if (nmeaIdx < sizeof(nmeaBuf) - 1) {
      nmeaBuf[nmeaIdx++] = c;
    } else {
      nmeaIdx = 0;
    }
  }

  const unsigned long now = millis();
  if (gpsHasFix && (now - lastGpsFixMillis > GPS_FIX_TIMEOUT_MS)) {
    gpsHasFix = false;
  }
  if (gpsTimeValid && (now - lastGpsTimeMillis > GPS_FIX_TIMEOUT_MS)) {
    gpsTimeValid = false;
  }
}

// ============================================================================
// FULL NREL SOLAR POSITION ALGORITHM (SPA)
// ============================================================================
// This calls the official NREL spa_calculate() implementation from spa.c.
// Keep the original NREL copyright/licence notice in spa.c and spa.h.
// The full algorithm computes topocentric solar azimuth and zenith, including
// nutation, aberration, parallax and the configured atmospheric refraction.
// The tracking clock supplies Malaysia local time and SPA receives timezone=+8.

bool spaCalculate(int year, int month, int day,
                  int hour, int minute, int second,
                  double &outAzimuth, double &outZenith) {
  spa_data spa = {};

  spa.year = year;
  spa.month = month;
  spa.day = day;
  spa.hour = hour;
  spa.minute = minute;
  spa.second = (double)second;

  spa.delta_ut1 = SPA_DELTA_UT1_SECONDS;
  spa.delta_t = SPA_DELTA_T_SECONDS;
  spa.timezone = (double)TZ_OFFSET_HOURS;
  spa.longitude = LONGITUDE;
  spa.latitude = LATITUDE;
  spa.elevation = GPS_ALTITUDE_M;
  spa.pressure = SPA_PRESSURE_MBAR;
  spa.temperature = SPA_TEMPERATURE_C;
  spa.slope = 0.0;
  spa.azm_rotation = 0.0;
  spa.atmos_refract = SPA_ATMOS_REFRACT_DEG;
  spa.function = SPA_ZA;

  const int result = spa_calculate(&spa);
  if (result != 0 || !isfinite(spa.azimuth) || !isfinite(spa.e0)) {
    return false;
  }

  // Match the original pvlib solar_position["zenith"] output: geometric
  // topocentric zenith (no atmospheric refraction). NREL spa.e0 is the
  // uncorrected topocentric elevation; spa.zenith is apparent/refracted.
  outAzimuth = spa.azimuth;
  outZenith = 90.0 - spa.e0;
  return true;
}

// ============================================================================
// MOTOR CONTROL (MDD10A)
// ============================================================================
void setupMotors() {
  pinMode(PWM_A, OUTPUT);
  pinMode(PWM_B, OUTPUT);
  digitalWrite(PWM_A, LOW);
  digitalWrite(PWM_B, LOW);
  pinMode(DIR_A, OUTPUT);
  pinMode(DIR_B, OUTPUT);
  digitalWrite(DIR_A, LOW);
  digitalWrite(DIR_B, LOW);

  yawPwmReady = ledcAttach(PWM_A, PWM_FREQ, PWM_RESOLUTION);
  const bool tiltAttached = ledcAttach(PWM_B, PWM_FREQ, PWM_RESOLUTION);
  motorPwmReady = yawPwmReady && tiltAttached;

  ledcWrite(PWM_A, 0);
  ledcWrite(PWM_B, 0);
  if (!tiltAttached) {
    pinMode(PWM_B, OUTPUT);
    digitalWrite(PWM_B, LOW);
  }
  if (!motorPwmReady) Serial.println("Dual-axis PWM setup incomplete.");
}

void stopAzimuth() {
  ledcWrite(PWM_A, 0);
}

void stopZenith() {
  ledcWrite(PWM_B, 0);
}

void leaveFixedYawMode() {
  if (!fixedYawMode) return;
  stopAzimuth();
  stopZenith();
  if (fixedYaw.referenced()) {
    yawOffsetDeg = wrapBearing(fixedYaw.bearing());
    yawAngleDeg = 0;
  }
  lastGyroMicros = micros();
  filteredGzDegPerSec = 0;
  fixedYaw.resetReference();
  fixedYawMode = false;
}

void prepareLegacyYaw() {
  if (legacyYawCalibrated || !azimuthMpuReady) return;
  stopAzimuth();
  stopZenith();
  calibrateAzimuthGyro();
}

void startYawSequence(bool shortTest) {
  if (yawSequence.active()) {
    Serial.println("Yaw sequence already active. Send STOP to cancel.");
    return;
  }
  leaveFixedYawMode();
  localYawControl = true;
  stopAzimuth();
  stopZenith();
  if (motorPwmReady) prepareLegacyYaw();
  if (!motorPwmReady || !azimuthMpuReady || !legacyYawCalibrated) {
    yawSequence.reset();
    Serial.println("Cannot start yaw. Check PWM and base MPU, then reset.");
    return;
  }
  const uint32_t startedUs = micros();
  lastGyroMicros = startedUs;
  filteredGzDegPerSec = 0.0;
  yawSequence.start(shortTest, startedUs, millis());
  Serial.println(shortTest ? "Yaw TEST started: 10 deg CW, then 10 deg CCW."
                           : "Yaw RUN started: 360 deg CW, then 300 deg CCW.");
}

void moveAzimuthStep(double targetAz, double currentAz) {
  double forwardError  = fmod(targetAz - currentAz, 360.0);
  if (forwardError < 0.0) forwardError += 360.0;

  double backwardError = fmod(currentAz - targetAz, 360.0);
  if (backwardError < 0.0) backwardError += 360.0;

  if (fabs(forwardError) <= AZ_STOP_DEADBAND || fabs(backwardError) <= AZ_STOP_DEADBAND) {
    stopAzimuth();
    return;
  }

  int dir = HIGH;
  if (backwardError <= AZ_BACK_ALLOW_DEG) {
    dir = LOW;
  }

  digitalWrite(DIR_A, dir);
  ledcWrite(PWM_A, AZ_SPEED_PWM);
}

void moveZenithStep(double targetZen, double currentZen) {
  double error = targetZen - currentZen;
  if (fabs(error) <= ZEN_STOP_DEADBAND) {
    stopZenith();
    return;
  }

  digitalWrite(DIR_B, (error > 0.0) ? HIGH : LOW);
  ledcWrite(PWM_B, ZEN_SPEED_PWM);
}

// ============================================================================
// CLOCK INITIALISATION (GPS is the runtime time source)
// ============================================================================
int monthNumberFromText(const char *monthText) {
  const char *months[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  for (int i = 0; i < 12; i++) {
    if (strncmp(monthText, months[i], 3) == 0) return i + 1;
  }
  return 1;
}

void initialiseClockFromBuildTime() {
  char monthText[4] = {0};
  int day = 1, year = 2026;
  int hour = 0, minute = 0, second = 0;

  sscanf(__DATE__, "%3s %d %d", monthText, &day, &year);
  sscanf(__TIME__, "%d:%d:%d", &hour, &minute, &second);

  simYear = year;
  simMonth = monthNumberFromText(monthText);
  simDay = day;
  simHour = hour;
  simMinute = minute;
  simSecond = second;
  lastClockMillis = millis();
}

// ============================================================================
// CLOCK & SERIAL COMMANDS
// ============================================================================
void advanceClock() {
  unsigned long nowMs = millis();
  while (nowMs - lastClockMillis >= 1000UL) {
    lastClockMillis += 1000UL;
    simSecond++;
    if (simSecond >= 60) { simSecond = 0; simMinute++; }
    if (simMinute >= 60) { simMinute = 0; simHour++; }
    if (simHour >= 24) {
      simHour = 0;
      simDay++;
      int dim = daysInMonth(simYear, simMonth);
      if (simDay > dim) {
        simDay = 1;
        simMonth++;
        if (simMonth > 12) {
          simMonth = 1;
          simYear++;
        }
      }
    }
  }
}

bool syncFixedSiteClock() {
  if (clockSetBySerial) return true;
  const time_t utc = time(nullptr);
  tm now{};
  if (utc < kEarliestSolarNetworkTime || gmtime_r(&utc, &now) == nullptr) return false;
  setLocalTimeFromUTC(now.tm_year + 1900, now.tm_mon + 1, now.tm_mday,
                      now.tm_hour, now.tm_min, now.tm_sec);
  return true;
}

void enterFixedYawMode() {
  if (fixedYawMode) return;
  stopAzimuth();
  stopZenith();
  localYawControl = false;
  yawSequence.reset();
  USE_MANUAL_TEST_ANGLES = false;
  fixedYaw.resetReference();
  fixedYawMode = true;
}

void startFixedYawReference(float bearing, const char *description) {
  enterFixedYawMode();
  const bool retryMpu = !azimuthMpuReady || !azimuthMpuSampleAvailable ||
      fixedYaw.status() == SolarFixedYawStatus::FeedbackUnavailable;
  fixedYaw.resetReference();
  stopAzimuth();
  stopZenith();
  if (retryMpu) initAzimuthMpu();
  if (!ina219SampleAvailable) initINA219();
  if (!azimuthMpuReady) {
    Serial.println("Cannot reference yaw: base MPU 0x68 unavailable. Check sensor power and I2C.");
    return;
  }
  fixedYaw.startReference(bearing, millis());
  if (fixedYaw.calibrating()) {
    Serial.printf("Referencing %s. Hold the base still for 3 seconds.\n", description);
  } else {
    Serial.printf("Cannot reference yaw: %s. Correct the fault and reset.\n",
                  solarFixedYawStatusName(fixedYaw.status()));
  }
}

void selectDualAxisMode() {
  leaveFixedYawMode();
  prepareLegacyYaw();
  localYawControl = false;
  yawSequence.reset();
}

bool setFixedTrackerTime(int year, int month, int day, int hour, int minute, int second) {
  fixedYaw.resetReference();
  stopAzimuth();
  stopZenith();
  fixedRealAnchor = fixedSolarAnchor = 0;
  fixedTargetAz = fixedTargetZen = NAN;
  fixedSpaError = false;
  fixedDemoSpeed = 1;
  if (year < 2024 || year > 2037 || month < 1 || month > 12 ||
      day < 1 || day > daysInMonth(year, month) || hour < 0 || hour > 23 ||
      minute < 0 || minute > 59 || second < 0 || second > 59) return false;

  // Convert Malaysia civil time to UTC independently of the system's timezone.
  int64_t days = day - 1;
  for (int y = 1970; y < year; ++y) days += isLeapYear(y) ? 366 : 365;
  for (int m = 1; m < month; ++m) days += daysInMonth(year, m);
  fixedRealAnchor = fixedSolarAnchor =
      days * 86400 + (hour - TZ_OFFSET_HOURS) * 3600 + minute * 60 + second;
  fixedClockAnchorUs = esp_timer_get_time();
  return true;
}

void handleFixedClockCommand(const String &line) {
  enterFixedYawMode();
  bool digits = line.length() == 15;
  for (size_t offset = 1; digits && offset < line.length(); ++offset) {
    digits = line[offset] >= '0' && line[offset] <= '9';
  }
  int year = 0, month = 0, day = 0, hour = 0, minute = 0, second = 0;
  if (digits) {
    sscanf(line.c_str() + 1, "%4d%2d%2d%2d%2d%2d", &year, &month, &day, &hour, &minute, &second);
  }
  Serial.println(setFixedTrackerTime(year, month, day, hour, minute, second)
      ? "Clock set. Point the base true south and send h."
      : "Invalid Malaysia time. Send tYYYYMMDDhhmmss.");
}

void setFixedDemoSpeed(const String &line) {
  const char *value = line.c_str() + 1;
  char *end;
  const long speed = strtol(value, &end, 10);
  if (!fixedYawMode || !fixedRealAnchor || end == value || *end || speed < 1 || speed > 600) {
    Serial.println("Set the fixed-tilt clock first. Use x1 to x600.");
    return;
  }
  fixedYaw.disarm();
  stopAzimuth();
  const int64_t now = esp_timer_get_time();
  const int64_t elapsed = now - fixedClockAnchorUs;
  fixedRealAnchor += elapsed / 1000000;
  fixedSolarAnchor = speed == 1 ? fixedRealAnchor
      : fixedSolarAnchor + fixedDemoSpeed * elapsed / 1000000;
  fixedClockAnchorUs = now;
  fixedDemoSpeed = static_cast<uint16_t>(speed);
  fixedTargetAz = fixedTargetZen = NAN;
  fixedSpaError = false;
  Serial.printf("Solar clock x%u. Send a again when ready.\n",
                static_cast<unsigned int>(fixedDemoSpeed));
}

void handleSerialCommand(String line) {
  line.trim();
  if (line.length() == 0) return;

  if (line.equalsIgnoreCase("STOP") || line.equalsIgnoreCase("s")) {
    if (fixedYawMode) {
      fixedYaw.disarm();
    } else {
      localYawControl = true;
      yawSequence.stop();
    }
    stopAzimuth();
    stopZenith();
    Serial.println("Stopped. Motors held off.");
  } else if (line.equalsIgnoreCase("TEST")) {
    startYawSequence(true);
  } else if (line.equalsIgnoreCase("RUN")) {
    startYawSequence(false);
  } else if (yawSequence.active()) {
    Serial.println("Send STOP before changing tracker settings or mode.");
  } else if ((line[0] == 't' || line[0] == 'T') && line.charAt(1) != ' ') {
    handleFixedClockCommand(line);
  } else if (line[0] == 'x' || line[0] == 'X') {
    setFixedDemoSpeed(line);
  } else if (line.equalsIgnoreCase("a")) {
    enterFixedYawMode();
    if (!yawPwmReady || !azimuthMpuReady) {
      Serial.println("Arm rejected: check yaw PWM and base MPU.");
    } else if (!fixedYaw.armed() && fixedYaw.status() != SolarFixedYawStatus::Ready) {
      Serial.printf("Arm rejected: %s.\n", solarFixedYawStatusName(fixedYaw.status()));
    } else {
      fixedYaw.requestArm();
    }
  } else if (line.equalsIgnoreCase("h")) {
    startFixedYawReference(180.0f, "true south (180 deg)");
  } else if (line[0] == 'b' || line[0] == 'B') {
    const char *value = line.c_str() + 1;
    char *end;
    const double bearing = strtod(value, &end);
    if (end == value || *end || !isfinite(bearing) || bearing < 0 || bearing >= 360) {
      Serial.println("Invalid bearing. Example: b90 for east.");
      return;
    }
    startFixedYawReference(static_cast<float>(bearing), "known bearing");
  } else if (line.startsWith("AZ ")) {
    yawOffsetDeg = line.substring(3).toFloat();
    Serial.print("Updated yawOffsetDeg to: ");
    Serial.println(yawOffsetDeg, 2);
  } else if (line.startsWith("T ")) {
    int y, mo, d, h, mi, s;
    if (sscanf(line.c_str(), "T %d %d %d %d %d %d", &y, &mo, &d, &h, &mi, &s) == 6) {
      if (fixedYawMode) {
        Serial.println(setFixedTrackerTime(y, mo, d, h, mi, s)
            ? "Clock set. Point the base true south and send h."
            : "Invalid Malaysia time. Send tYYYYMMDDhhmmss.");
        return;
      }
      if (y < 1900 || y > 6000 || mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) ||
          h < 0 || h > 23 || mi < 0 || mi > 59 || s < 0 || s > 59) {
        Serial.println("Invalid local date or time.");
        return;
      }
      simYear = y; simMonth = mo; simDay = d;
      simHour = h; simMinute = mi; simSecond = s;
      lastClockMillis = millis();
      clockSetBySerial = true;
      Serial.println("Updated clock time.");
    }
  } else if (line.startsWith("MANUAL ")) {
    double a, z;
    if (sscanf(line.c_str(), "MANUAL %lf %lf", &a, &z) == 2) {
      selectDualAxisMode();
      USE_MANUAL_TEST_ANGLES = true;
      MANUAL_TARGET_AZ = a;
      MANUAL_TARGET_ZEN = z;
      Serial.println("Switched to MANUAL target angles.");
    }
  } else if (line.equalsIgnoreCase("AUTO")) {
    selectDualAxisMode();
    USE_MANUAL_TEST_ANGLES = false;
    Serial.println("Switched back to AUTO SPA tracking.");
  } else if (line.equalsIgnoreCase("RESET_WH")) {
    energyWh = 0.0;
    Serial.println("Reset accumulated energy (Wh) to 0.");
  }
}

void handleSerialCommands() {
  static char command[80];
  static size_t length = 0;
  static bool overflow = false;
  for (size_t count = 0; count < sizeof(command) && Serial.available(); ++count) {
    const char next = static_cast<char>(Serial.read());
    if (next == '\n' || next == '\r') {
      if (length > 0 && !overflow) {
        command[length] = '\0';
        handleSerialCommand(String(command));
      }
      length = 0;
      overflow = false;
    } else if (length + 1 < sizeof(command)) {
      command[length++] = next;
    } else {
      overflow = true;
    }
  }
}

// ============================================================================
// SETUP & LOOP
// ============================================================================
void captureSolarTelemetry(const SolarTrackingSample &tracking, const SolarPowerSample &power) {
  if (!solarTelemetryDue(millis())) return;
  const int64_t capturedAtUs = esp_timer_get_time();
  const bool telemetryHasFix = USE_GPS_FOR_TRACKING && gpsHasFix && isfinite(LATITUDE) &&
      isfinite(LONGITUDE) && fabs(LATITUDE) <= 90 && fabs(LONGITUDE) <= 180;

  SolarTelemetrySnapshot snapshot{};
  snapshot.capturedAtUs = capturedAtUs;
  snapshot.lastFixAgeMs = USE_GPS_FOR_TRACKING && lastGpsFixUs >= 0
      ? (capturedAtUs - lastGpsFixUs) / 1000 : -1;
  snapshot.gpsEnabled = USE_GPS_FOR_TRACKING;
  snapshot.gpsFix = telemetryHasFix;
  snapshot.gpsTimeValid = USE_GPS_FOR_TRACKING && gpsTimeValid;
  snapshot.latitude = telemetryHasFix ? LATITUDE : NAN;
  snapshot.longitude = telemetryHasFix ? LONGITUDE : NAN;
  snapshot.manualMode = localYawControl || USE_MANUAL_TEST_ANGLES;
  snapshot.azimuthPwm = ledcRead(PWM_A);
  snapshot.zenithPwm = ledcRead(PWM_B);
  snapshot.tracking = tracking;
  if (!azimuthMpuSampleAvailable) snapshot.tracking.actualAzimuth = NAN;
  if (!zenithMpuSampleAvailable) snapshot.tracking.actualZenith = NAN;
  if (ina219SampleAvailable) snapshot.solarPower = power;
  snapshot.ina219Available = ina219SampleAvailable;
  snapshot.azimuthMpuAvailable = azimuthMpuSampleAvailable;
  snapshot.zenithMpuAvailable = zenithMpuSampleAvailable;
  queueSolarTelemetry(snapshot);
}

SolarPowerSample readSolarPowerSample() {
  float voltage, current, power;
  readSolarPower(voltage, current, power);
  updateEnergyWh(ina219SampleAvailable ? power : 0.0f);
  return {voltage, current, power, energyWh};
}

void sampleTrackingAngles(SolarTrackingSample &tracking) {
  azimuthMpuSampleAvailable = azimuthMpuReady;
  zenithMpuSampleAvailable = zenithMpuReady;
  tracking.actualAzimuth = getAzimuthYawDeg();
  tracking.actualZenith = getZenithTilt0To90();
}

void updateFixedYawMode() {
  const uint32_t nowMs = millis();
  if (fixedYaw.gyroSampleDue(nowMs)) {
    azimuthMpuSampleAvailable = azimuthMpuReady;
    const int16_t raw = azimuthMpuReady ? readWordMPU(AZIMUTH_ADDR, MPU_GYRO_ZOUT_H) : 0;
    fixedYaw.updateGyro(raw, azimuthMpuSampleAvailable, nowMs);
    if (!fixedYaw.duty()) stopAzimuth();
  }

  const bool clockSet = fixedRealAnchor != 0;
  const time_t solarUtc = clockSet
      ? fixedSolarAnchor + fixedDemoSpeed * (esp_timer_get_time() - fixedClockAnchorUs) / 1000000
      : 0;
  static uint32_t lastSunMs = 0;
  if (clockSet && ((!fixedSpaError && !isfinite(fixedTargetAz)) || nowMs - lastSunMs >= 1000)) {
    lastSunMs = nowMs;
    const time_t malaysia = solarUtc + TZ_OFFSET_HOURS * 3600;
    tm local{};
    fixedSpaError = gmtime_r(&malaysia, &local) == nullptr ||
        !spaCalculate(local.tm_year + 1900, local.tm_mon + 1, local.tm_mday,
                      local.tm_hour, local.tm_min, local.tm_sec, fixedTargetAz, fixedTargetZen);
    if (fixedSpaError) {
      fixedTargetAz = fixedTargetZen = NAN;
      fixedYaw.disarm();
      stopAzimuth();
    }
  }

  if (fixedYaw.controlDue(nowMs)) {
    const bool wasArmed = fixedYaw.armed();
    fixedYaw.updateControl(clockSet, fixedTargetAz, fixedTargetZen < 90.0, nowMs);
    const uint8_t duty = yawPwmReady ? fixedYaw.duty() : 0;
    if (duty) {
      const int direction = fixedYaw.clockwise() ? HIGH : LOW;
      if (digitalRead(DIR_A) != direction) {
        stopAzimuth();
        digitalWrite(DIR_A, direction);
      }
      ledcWrite(PWM_A, duty);
    } else {
      stopAzimuth();
    }
    if (!wasArmed && fixedYaw.armed()) Serial.println("Fixed-tilt automatic yaw armed.");
  }

  static uint32_t lastReportMs = 0;
  if (nowMs - lastReportMs < 1000) return;
  lastReportMs = nowMs;
  const SolarFixedYawStatus status = yawPwmReady ? fixedYaw.status()
                                               : SolarFixedYawStatus::FeedbackUnavailable;
  SolarTrackingSample tracking;
  tracking.targetAzimuth = fixedTargetAz;
  tracking.targetZenith = fixedTargetZen;
  if (fixedYaw.referenced()) tracking.actualAzimuth = wrapBearing(fixedYaw.bearing());
  zenithMpuSampleAvailable = zenithMpuReady;
  tracking.actualZenith = getZenithTilt0To90();
  if (fixedSpaError) tracking.stopReason = SolarStopReason::SpaError;
  else if (status == SolarFixedYawStatus::FeedbackUnavailable) {
    tracking.stopReason = SolarStopReason::FeedbackUnavailable;
  } else if (!clockSet) tracking.stopReason = SolarStopReason::WaitingForClock;
  else if (status == SolarFixedYawStatus::SunBelowHorizon) {
    tracking.stopReason = SolarStopReason::ZenithLimit;
  } else if (!fixedYaw.armed()) tracking.stopReason = SolarStopReason::LocalHold;
  tracking.fixedYaw = {true, clockSet, fixedYaw.referenced(), fixedYaw.calibrating(),
                       fixedYaw.armed(), fixedYaw.reachable(), status, fixedDemoSpeed, solarUtc};
  const SolarPowerSample power = readSolarPowerSample();
  captureSolarTelemetry(tracking, power);

  char localTime[24] = "clock not set";
  const time_t malaysia = solarUtc + TZ_OFFSET_HOURS * 3600;
  tm local{};
  if (clockSet && gmtime_r(&malaysia, &local) != nullptr) {
    strftime(localTime, sizeof(localTime), "%Y-%m-%d %H:%M:%S", &local);
  }
  Serial.printf("%s MYT | Fixed tilt | yaw %.2f / %.2f deg | solar zenith %.2f deg | %s | PWM %u\n",
                localTime, tracking.actualAzimuth, tracking.targetAzimuth, tracking.targetZenith,
                status == SolarFixedYawStatus::Ready ? "ready: send a" : solarFixedYawStatusName(status),
                static_cast<unsigned int>(fixedYaw.duty()));
  if (ina219SampleAvailable) {
    Serial.printf("loadVoltage=%.3f V current=%.3f A loadPower=%.3f W E=%.4f Wh\n",
                  power.voltage, power.current, power.power, power.energy);
  } else {
    Serial.println("INA219 power unavailable.");
  }
}

void updateLocalYawControl() {
  const uint32_t nowUs = micros();
  const uint32_t nowMs = millis();
  if (yawSequence.sampleDue(nowUs)) {
    const SolarYawPhase previousPhase = yawSequence.report().phase;
    const double previousRotation = yawSequence.report().netRotationDeg;
    int16_t raw = 0;
    if (!yawSequence.sampleInterrupted(nowUs)) {
      azimuthMpuSampleAvailable = azimuthMpuReady;
      raw = readWordMPU(AZIMUTH_ADDR, MPU_GYRO_ZOUT_H);
    }
    yawSequence.sample(raw, azimuthMpuSampleAvailable, gyroBiasRaw, nowUs, nowMs);
    const SolarYawReport &report = yawSequence.report();
    const double deltaClockwise = report.netRotationDeg - previousRotation;
    // The sequence counts clockwise travel as positive. The tracker retains the raw gyro convention.
    yawAngleDeg = fmod(yawAngleDeg - deltaClockwise, 360.0);
    lastGyroMicros = nowUs;
    if (report.result == SolarYawResult::FeedbackUnavailable) azimuthMpuReady = false;

    const uint8_t duty = yawSequence.duty();
    if (duty == 0) {
      stopAzimuth();
    } else {
      const int direction = yawSequence.clockwise() ? HIGH : LOW;
      if (digitalRead(DIR_A) != direction) {
        stopAzimuth();
        digitalWrite(DIR_A, direction);
      }
      ledcWrite(PWM_A, duty);
    }

    static uint32_t lastPrintMs = 0;
    if (report.phase != previousPhase || nowMs - lastPrintMs >= 500) {
      lastPrintMs = nowMs;
      const char *result = solarYawResultName(report.result);
      Serial.printf("Yaw %s | leg %.1f / %.1f deg | net %.1f deg\n",
                    result ? result : solarYawPhaseName(report.phase), report.legProgressDeg,
                    report.legTargetDeg, report.netRotationDeg);
    }
  }

  // Leave time for the 100 Hz yaw samples while measuring the other sensors for MQTT.
  static uint32_t lastMonitorMs = 0;
  if (nowMs - lastMonitorMs >= 250) {
    lastMonitorMs = nowMs;
    SolarTrackingSample tracking;
    if (yawSequence.active()) {
      tracking.actualAzimuth = correctedYawDeg();
      zenithMpuSampleAvailable = zenithMpuReady;
      tracking.actualZenith = getZenithTilt0To90();
    } else {
      sampleTrackingAngles(tracking);
      tracking.stopReason = SolarStopReason::LocalHold;
    }
    tracking.yawSequence = yawSequence.report();
    captureSolarTelemetry(tracking, readSolarPowerSample());
  }
}

SolarStopReason resolveTrackingTargets(double &targetAz, double &targetZen) {
  if (USE_MANUAL_TEST_ANGLES) {
    targetAz = MANUAL_TARGET_AZ;
    targetZen = MANUAL_TARGET_ZEN;
    return SolarStopReason::None;
  }
  if (USE_GPS_FOR_TRACKING && (!gpsHasFix || !gpsTimeValid)) {
    Serial.println("Waiting for valid GPS position and UTC date/time; motors stopped.");
    return SolarStopReason::WaitingForGps;
  }
  if (!USE_GPS_FOR_TRACKING && !syncFixedSiteClock()) {
    Serial.println("Waiting for network time or Serial T command; motors stopped.");
    return SolarStopReason::WaitingForClock;
  }
  if (!spaCalculate(simYear, simMonth, simDay, simHour, simMinute, simSecond, targetAz, targetZen)) {
    Serial.println("NREL SPA input/calculation error; motors stopped.");
    return SolarStopReason::SpaError;
  }
  return SolarStopReason::None;
}

void setup() {
  Serial.begin(115200);
  setupMotors();
  delay(500);

  if (USE_GPS_FOR_TRACKING) Serial2.begin(GPS_BAUD, SERIAL_8N1, GPS_RX2, GPS_TX2);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  Wire.setTimeOut(10);

  initMPU6050();
  calibrateAzimuthGyro(1000);
  initINA219();

  yawOffsetDeg = 0.0;
  initialiseClockFromBuildTime();
  lastEnergyMillis = millis();

  Serial.println("SolarPilot ESP32 tracker ready.");
  Serial.println("Fixed tilt, motors disarmed. Send tYYYYMMDDhhmmss, h (true south), then a (arm). s stops.");
  if (!USE_GPS_FOR_TRACKING) {
    Serial.printf("GPS disabled. AUTO uses fixed coordinates %.5f, %.5f and network time.\n",
                  LATITUDE, LONGITUDE);
  }
  Serial.println("Other commands: b<deg>, x1-x600, AZ <deg>, MANUAL <az> <zen>, AUTO, RESET_WH.");
  Serial.println("Yaw rotation: TEST = 10/10 deg, RUN = 360/300 deg, STOP = hold.");
  startSolarTelemetry();
}

void loop() {
  if (!fixedYawMode && USE_GPS_FOR_TRACKING) pollGPS();
  advanceClock();
  handleSerialCommands();
  if (fixedYawMode) {
    updateFixedYawMode();
    delay(1);
    return;
  }
  if (localYawControl) {
    updateLocalYawControl();
    delay(1);
    return;
  }

  SolarTrackingSample tracking;
  tracking.stopReason = motorPwmReady
      ? resolveTrackingTargets(tracking.targetAzimuth, tracking.targetZenith)
      : SolarStopReason::LocalHold;
  if (tracking.stopReason != SolarStopReason::None) {
    stopAzimuth();
    stopZenith();
    sampleTrackingAngles(tracking);
    captureSolarTelemetry(tracking, readSolarPowerSample());
    delay(tracking.stopReason == SolarStopReason::SpaError ? 50 : 250);
    return;
  }

  // One sensor sample per loop is shared by display, error calculation and motor control.
  sampleTrackingAngles(tracking);
  const double targetAz = tracking.targetAzimuth;
  const double targetZen = tracking.targetZenith;
  const double actualAz = tracking.actualAzimuth;
  const double actualZen = tracking.actualZenith;
  if (!azimuthMpuSampleAvailable || !zenithMpuSampleAvailable ||
      !isfinite(actualAz) || !isfinite(actualZen)) {
    stopAzimuth();
    stopZenith();
    Serial.println("Feedback sensor unavailable; motors stopped.");
    tracking.stopReason = SolarStopReason::FeedbackUnavailable;
    captureSolarTelemetry(tracking, readSolarPowerSample());
    delay(50);
    return;
  }

  double azErrorSigned = fmod(targetAz - actualAz + 180.0, 360.0);
  if (azErrorSigned < 0.0) azErrorSigned += 360.0;
  azErrorSigned -= 180.0;

  double zenErrorSigned = targetZen - actualZen;
  double azErrorAbs  = fabs(azErrorSigned);
  double zenErrorAbs = fabs(zenErrorSigned);

  const char* azStatus  = "Idle";
  const char* zenStatus = "Idle";

  // The tilt mechanism is limited to 0-90 degrees. Stop at night rather than
  // commanding an unreachable target when the geometric zenith exceeds 90 deg.
  if (targetZen >= 90.0) {
    stopAzimuth();
    stopZenith();
    azStatus = "Night/Stopped";
    zenStatus = "Night/Stopped";
    tracking.stopReason = SolarStopReason::ZenithLimit;
  } else {
    if (azErrorAbs >= AZ_CONTROL_THRESHOLD_DEG) {
      moveAzimuthStep(targetAz, actualAz);
      azStatus = "Running";
    } else {
      stopAzimuth();
    }

    if (zenErrorAbs >= ZEN_CONTROL_THRESHOLD_DEG) {
      moveZenithStep(targetZen, actualZen);
      zenStatus = "Running";
    } else {
      stopZenith();
    }
  }

  const SolarPowerSample power = readSolarPowerSample();
  captureSolarTelemetry(tracking, power);

  static unsigned long lastPrint = 0;
  if (millis() - lastPrint >= 500UL) {
    lastPrint = millis();
    Serial.printf("%04d-%02d-%02d %02d:%02d:%02d | GPS:%s (%.5f,%.5f) | Alt:%.1fm%s | Az:%.2f/%.2f (%s) | Zen:%.2f/%.2f (%s) | V:%.2fV I:%.3fA P:%.2fW E:%.4fWh\n",
                  simYear, simMonth, simDay, simHour, simMinute, simSecond,
                  USE_GPS_FOR_TRACKING ? ((gpsHasFix && gpsTimeValid) ? "Locked" : "NoFix/LastKnown")
                                       : "Disabled/FixedSite",
                  LATITUDE, LONGITUDE, GPS_ALTITUDE_M,
                  gpsAltitudeValid ? "" : "(default)",
                  targetAz, actualAz, azStatus,
                  targetZen, actualZen, zenStatus,
                  power.voltage, power.current, power.power, power.energy);
  }

  delay(50);
}
