#include <Arduino.h>
#include <esp_camera.h>
#include <sensor.h>

namespace {
constexpr char kProtocol[] = "EPHYCAM/1";
constexpr char kProfile[] = "f-fixed-r0551-g0400-b0844-ag8-ex1560";
constexpr framesize_t kPreviewSize = FRAMESIZE_VGA;
constexpr framesize_t kCaptureSize = FRAMESIZE_QXGA;
constexpr int kPreviewQuality = 20;
constexpr int kCaptureQuality = 10;
constexpr size_t kCaptureWarmupFrames = 24;

constexpr int kBrightness = -1;
constexpr int kContrast = 1;
constexpr int kSaturation = -1;
constexpr int kSharpness = 0;
constexpr int kDenoise = 1;
constexpr int kAwbRed = 1361;
constexpr int kAwbGreen = 1024;
constexpr int kAwbBlue = 2116;
constexpr int kAgcGain = 8;
constexpr int kAecValue = 1560;

constexpr int kPwdn = -1;
constexpr int kReset = -1;
constexpr int kXclk = 10;
constexpr int kSiod = 40;
constexpr int kSioc = 39;
constexpr int kY9 = 48;
constexpr int kY8 = 11;
constexpr int kY7 = 12;
constexpr int kY6 = 14;
constexpr int kY5 = 16;
constexpr int kY4 = 18;
constexpr int kY3 = 17;
constexpr int kY2 = 15;
constexpr int kVsync = 38;
constexpr int kHref = 47;
constexpr int kPclk = 13;

bool camera_ready = false;
bool preview_mode = true;
uint32_t preview_sequence = 0;

bool writeReg16(sensor_t* sensor, int high_register, int value) {
  return sensor->set_reg(sensor, high_register, 0xff,
                         (value >> 8) & 0xff) == 0 &&
         sensor->set_reg(sensor, high_register + 1, 0xff, value & 0xff) == 0;
}

int readReg16(sensor_t* sensor, int high_register) {
  const int high = sensor->get_reg(sensor, high_register, 0xff);
  const int low = sensor->get_reg(sensor, high_register + 1, 0xff);
  if (high < 0 || low < 0) {
    return -1;
  }
  return ((high & 0xff) << 8) | (low & 0xff);
}

int readAgcGain(sensor_t* sensor) {
  const int high = sensor->get_reg(sensor, 0x350a, 0xff);
  const int low = sensor->get_reg(sensor, 0x350b, 0xff);
  if (high < 0 || low < 0) {
    return -1;
  }
  int gain = ((low & 0xf0) >> 4) | ((high & 0x03) << 4);
  return gain + (((low & 0x0f) != 0) ? 1 : 0);
}

int readAecValue(sensor_t* sensor) {
  const int high = sensor->get_reg(sensor, 0x3500, 0xff);
  const int middle = sensor->get_reg(sensor, 0x3501, 0xff);
  const int low = sensor->get_reg(sensor, 0x3502, 0xff);
  if (high < 0 || middle < 0 || low < 0) {
    return -1;
  }
  return ((high & 0x0f) << 12) | ((middle & 0xff) << 4) |
         ((low & 0xf0) >> 4);
}

bool applyFixedProfile(sensor_t* sensor) {
  if (sensor == nullptr || sensor->id.PID != OV3660_PID) {
    return false;
  }
  return sensor->set_brightness(sensor, kBrightness) == 0 &&
         sensor->set_contrast(sensor, kContrast) == 0 &&
         sensor->set_saturation(sensor, kSaturation) == 0 &&
         sensor->set_sharpness(sensor, kSharpness) == 0 &&
         sensor->set_denoise(sensor, kDenoise) == 0 &&
         sensor->set_special_effect(sensor, 0) == 0 &&
         writeReg16(sensor, 0x3400, kAwbRed) &&
         writeReg16(sensor, 0x3402, kAwbGreen) &&
         writeReg16(sensor, 0x3404, kAwbBlue) &&
         sensor->set_reg(sensor, 0x3406, 0xff, 0x01) == 0 &&
         sensor->set_gain_ctrl(sensor, 0) == 0 &&
         sensor->set_exposure_ctrl(sensor, 0) == 0 &&
         sensor->set_agc_gain(sensor, kAgcGain) == 0 &&
         sensor->set_aec_value(sensor, kAecValue) == 0;
}

bool setFrameMode(sensor_t* sensor, framesize_t size, int quality,
                  bool target_preview_mode) {
  if (preview_mode == target_preview_mode) {
    return true;
  }
  if (sensor == nullptr || sensor->set_framesize(sensor, size) != 0 ||
      sensor->set_quality(sensor, quality) != 0) {
    return false;
  }
  preview_mode = target_preview_mode;
  return true;
}

bool discardFrames(size_t count) {
  for (size_t index = 0; index < count; ++index) {
    camera_fb_t* frame = esp_camera_fb_get();
    if (frame == nullptr) {
      return false;
    }
    esp_camera_fb_return(frame);
  }
  return true;
}

size_t writeJpeg(camera_fb_t* frame) {
  size_t sent = 0;
  while (sent < frame->len) {
    const size_t remaining = frame->len - sent;
    const size_t chunk = remaining < 4096 ? remaining : 4096;
    const size_t written = Serial.write(frame->buf + sent, chunk);
    if (written == 0) {
      delay(1);
    } else {
      sent += written;
    }
  }
  Serial.flush();
  return sent;
}

void printStatus() {
  sensor_t* sensor = esp_camera_sensor_get();
  Serial.printf(
      "%s STATUS ready=%d camera=%s pid=0x%04x psram_found=%d "
      "psram_bytes=%u preview_frame=VGA capture_frame=QXGA profile=%s "
      "automatic_capture=disabled continuous_capture=disabled wifi=disabled "
      "microsd=disabled\n",
      kProtocol, camera_ready ? 1 : 0,
      sensor != nullptr && sensor->id.PID == OV3660_PID ? "OV3660" : "unknown",
      sensor != nullptr ? sensor->id.PID : 0, psramFound() ? 1 : 0,
      static_cast<unsigned int>(ESP.getPsramSize()), kProfile);
}

void previewOnce() {
  sensor_t* sensor = esp_camera_sensor_get();
  if (!camera_ready ||
      !setFrameMode(sensor, kPreviewSize, kPreviewQuality, true)) {
    Serial.printf("%s ERROR code=PREVIEW_MODE_FAILED\n", kProtocol);
    return;
  }

  ++preview_sequence;
  Serial.printf("%s PREVIEW_BEGIN sequence=%u\n", kProtocol,
                static_cast<unsigned int>(preview_sequence));
  const uint32_t capture_started = millis();
  camera_fb_t* frame = esp_camera_fb_get();
  const uint32_t capture_ms = millis() - capture_started;
  if (frame == nullptr || frame->format != PIXFORMAT_JPEG) {
    if (frame != nullptr) {
      esp_camera_fb_return(frame);
    }
    Serial.printf("%s ERROR code=PREVIEW_CAPTURE_FAILED\n", kProtocol);
    return;
  }
  Serial.printf(
      "%s PREVIEW_FRAME sequence=%u bytes=%u width=%u height=%u "
      "capture_ms=%u\n",
      kProtocol, static_cast<unsigned int>(preview_sequence),
      static_cast<unsigned int>(frame->len),
      static_cast<unsigned int>(frame->width),
      static_cast<unsigned int>(frame->height),
      static_cast<unsigned int>(capture_ms));
  Serial.flush();
  const uint32_t transfer_started = millis();
  const size_t sent = writeJpeg(frame);
  esp_camera_fb_return(frame);
  Serial.printf("\n%s PREVIEW_COMPLETE sequence=%u bytes=%u transfer_ms=%u\n",
                kProtocol, static_cast<unsigned int>(preview_sequence),
                static_cast<unsigned int>(sent),
                static_cast<unsigned int>(millis() - transfer_started));
}

void captureOnce() {
  sensor_t* sensor = esp_camera_sensor_get();
  if (!camera_ready) {
    Serial.printf("%s ERROR code=CAMERA_NOT_READY\n", kProtocol);
    return;
  }
  Serial.printf("%s CAPTURE_BEGIN frame=QXGA\n", kProtocol);
  if (!setFrameMode(sensor, kCaptureSize, kCaptureQuality, false) ||
      !applyFixedProfile(sensor)) {
    Serial.printf("%s ERROR code=CAPTURE_PROFILE_FAILED\n", kProtocol);
    return;
  }

  const uint32_t warmup_started = millis();
  if (!discardFrames(kCaptureWarmupFrames)) {
    Serial.printf("%s ERROR code=CAPTURE_WARMUP_FAILED\n", kProtocol);
    return;
  }
  const uint32_t warmup_ms = millis() - warmup_started;
  const uint32_t capture_started = millis();
  camera_fb_t* frame = esp_camera_fb_get();
  const uint32_t capture_ms = millis() - capture_started;
  if (frame == nullptr || frame->format != PIXFORMAT_JPEG) {
    if (frame != nullptr) {
      esp_camera_fb_return(frame);
    }
    Serial.printf("%s ERROR code=CAPTURE_FAILED\n", kProtocol);
    return;
  }

  const size_t frame_length = frame->len;
  Serial.printf(
      "%s CAPTURED bytes=%u width=%u height=%u capture_ms=%u warmup_ms=%u "
      "camera=OV3660 profile=%s agc_gain=%d aec_value=%d "
      "awb_red_gain=%d awb_green_gain=%d awb_blue_gain=%d\n",
      kProtocol, static_cast<unsigned int>(frame_length),
      static_cast<unsigned int>(frame->width),
      static_cast<unsigned int>(frame->height),
      static_cast<unsigned int>(capture_ms),
      static_cast<unsigned int>(warmup_ms), kProfile, readAgcGain(sensor),
      readAecValue(sensor), readReg16(sensor, 0x3400),
      readReg16(sensor, 0x3402), readReg16(sensor, 0x3404));
  Serial.printf("%s FRAME bytes=%u\n", kProtocol,
                static_cast<unsigned int>(frame_length));
  Serial.flush();
  const uint32_t transfer_started = millis();
  const size_t sent = writeJpeg(frame);
  esp_camera_fb_return(frame);

  bool preview_ready =
      setFrameMode(sensor, kPreviewSize, kPreviewQuality, true);
  if (preview_ready) {
    preview_ready = discardFrames(1);
  }
  Serial.printf(
      "\n%s COMPLETE bytes=%u transfer_ms=%u next_frame=VGA "
      "preview_ready=%d\n",
      kProtocol, static_cast<unsigned int>(sent),
      static_cast<unsigned int>(millis() - transfer_started),
      preview_ready ? 1 : 0);
}
}  // namespace

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(1000);
  delay(250);
  Serial.printf(
      "%s BOOT automatic_capture=disabled continuous_capture=disabled "
      "wifi=disabled microsd=disabled\n",
      kProtocol);
  if (!psramFound()) {
    Serial.printf("%s ERROR code=PSRAM_NOT_FOUND\n", kProtocol);
    return;
  }

  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = kY2;
  config.pin_d1 = kY3;
  config.pin_d2 = kY4;
  config.pin_d3 = kY5;
  config.pin_d4 = kY6;
  config.pin_d5 = kY7;
  config.pin_d6 = kY8;
  config.pin_d7 = kY9;
  config.pin_xclk = kXclk;
  config.pin_pclk = kPclk;
  config.pin_vsync = kVsync;
  config.pin_href = kHref;
  config.pin_sccb_sda = kSiod;
  config.pin_sccb_scl = kSioc;
  config.pin_pwdn = kPwdn;
  config.pin_reset = kReset;
  config.xclk_freq_hz = 20000000;
  config.frame_size = kCaptureSize;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = kCaptureQuality;
  config.fb_count = 1;

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("%s ERROR code=CAMERA_INIT_FAILED esp_err=0x%x\n", kProtocol,
                  static_cast<unsigned int>(error));
    return;
  }
  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor == nullptr || sensor->id.PID != OV3660_PID) {
    Serial.printf("%s ERROR code=UNSUPPORTED_CAMERA pid=0x%04x\n", kProtocol,
                  sensor != nullptr ? sensor->id.PID : 0);
    return;
  }
  sensor->set_vflip(sensor, 1);
  sensor->set_framesize(sensor, kPreviewSize);
  sensor->set_quality(sensor, kPreviewQuality);
  preview_mode = true;
  if (!applyFixedProfile(sensor)) {
    Serial.printf("%s ERROR code=PROFILE_APPLY_FAILED\n", kProtocol);
    return;
  }
  camera_ready = true;
  Serial.printf("%s READY camera=OV3660 profile=%s\n", kProtocol, kProfile);
  printStatus();
}

void loop() {
  if (Serial.available() <= 0) {
    delay(10);
    return;
  }
  String command = Serial.readStringUntil('\n');
  command.trim();
  if (command == "STATUS") {
    printStatus();
  } else if (command == "PREVIEW") {
    previewOnce();
  } else if (command == "CAPTURE") {
    captureOnce();
  } else if (command == "RESTART") {
    Serial.printf("%s RESTARTING\n", kProtocol);
    Serial.flush();
    delay(100);
    ESP.restart();
  } else if (command.length() > 0) {
    Serial.printf("%s ERROR code=UNKNOWN_COMMAND\n", kProtocol);
  }
}
