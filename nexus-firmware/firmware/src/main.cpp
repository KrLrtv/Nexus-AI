#include "esp_system.h"
#include "esp_heap_caps.h"
#include <M5Unified.h>
#include "esp_attr.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <math.h>

// Build secrets are injected by GitHub Actions into config_generated.h.
// Local build uses example placeholders in config_local.h.
#if __has_include("config_generated.h")
#include "config_generated.h"
#else
#include "config_local.h"


#endif
// NEXUS_BOOTFIX_R1: use M5 directly; do not cache button reference members.
#include "BleFallback.h"
void updateBridgeAddress();

static const char *WIFI_SSID = NEXUS_WIFI_SSID;
static const char *WIFI_PASSWORD = NEXUS_WIFI_PASSWORD;
static String bridgeUrl = "";
// NEXUS_R8_MINIMAL_CONTROL: device settings are hydrated from /status.
static bool nexusTtsEnabled=true;
static bool nexusLocalSttEnabled=false;
static bool nexusStreamingEnabled=false; // never enabled automatically
static bool nexusAutoScroll=true;

static const char *DEVICE_TOKEN = NEXUS_DEVICE_TOKEN;

static constexpr int AUDIO_RATE = 16000;
static constexpr int AUDIO_SECONDS = 4;
static constexpr size_t AUDIO_SAMPLES = AUDIO_RATE * AUDIO_SECONDS;
static constexpr int AUDIO_CHUNK_SAMPLES = 512;
// NEXUS_AUDIOFIX_R2: one PSRAM-backed WAV/PCM buffer, allocated on demand
static uint8_t *wavBuffer = nullptr;
static int16_t *audioBuffer = nullptr;
static String statusMessage = "Boot";
static String currentText = "Ready";
static uint32_t lastPoll = 0;
static uint32_t lastAnimation = 0;
static bool phoneOnline = false;
static uint32_t statusSeq = 0;
static uint32_t btnBPressAt=0;
static bool btnBHandled=false;
static bool approvalPending=false;
static bool holdTriggered=false;
static uint32_t lastWifiAttempt=0;
static uint32_t lastUserActivity=0;
static bool screenDimmed=false;
static bool dialogMode=false;
static uint8_t sessionClips=0;
static uint32_t nextClipAt=0;

// Volatile RTC breadcrumbs identify the last active section, NOT a backtrace.
// No NVS writes and no automatic reset, sleep, watchdog or brownout changes.
struct NexusBootTrace {
  uint32_t magic;
  uint32_t stage;
};
static RTC_NOINIT_ATTR volatile NexusBootTrace nexusBootTrace;
static uint32_t nexusPreviousStage = 0;
static int nexusResetReason = 0;
static constexpr uint32_t NEXUS_TRACE_MAGIC = 0x4E585231;  // NXR1

class NexusStageGuard {
 public:
  explicit NexusStageGuard(uint32_t stage) : previous_(nexusBootTrace.stage) {
    nexusBootTrace.stage = stage;
  }
  ~NexusStageGuard() { nexusBootTrace.stage = previous_; }
  NexusStageGuard(const NexusStageGuard&) = delete;
  NexusStageGuard& operator=(const NexusStageGuard&) = delete;
 private:
  uint32_t previous_;
};

void nexusBeginTrace() {
  const esp_reset_reason_t why = esp_reset_reason();
  nexusResetReason = static_cast<int>(why);
  // A cold boot/brownout makes previous RTC contents unsuitable for diagnosis.
  nexusPreviousStage = (nexusBootTrace.magic == NEXUS_TRACE_MAGIC &&
                        why != ESP_RST_POWERON && why != ESP_RST_BROWNOUT)
                       ? nexusBootTrace.stage : 0;
  nexusBootTrace.magic = NEXUS_TRACE_MAGIC;
  nexusBootTrace.stage = 0;
}

#include "NexusMinimalUI.h"
// NEXUS_MINIMAL_UI_R3
void displayStatus() {
  NexusStageGuard trace(50);
  nexus_ui::render();
}

bool postEvent(const char* type) {
  NexusStageGuard trace(41);
  if (WiFi.status() != WL_CONNECTED) {
#ifdef NEXUS_ENABLE_BLE
    return NexusBle::send(type);
#else
    return false;
#endif
  }
  HTTPClient h;
  h.setTimeout(1500);
  updateBridgeAddress(); h.begin(bridgeUrl + "/event");
  h.addHeader("Content-Type","application/json");
  h.addHeader("X-Device-Token",DEVICE_TOKEN);
  JsonDocument j; j["event"] = type;
  String body; serializeJson(j,body);
  int code=h.POST(body);
  h.end();
  return code==200;
}

// Records WAV mono 16-bit PCM. No microphone streaming or persistent capture.
// M5Unified microphone and speaker share audio resources on this device.

// NEXUS_SMART_VOICE_R7
#ifndef NEXUS_R7_STREAMING
#define NEXUS_R7_STREAMING 1  // Runtime opt-in from NEXUS settings, initially OFF
#endif
#ifndef NEXUS_R7_VAD_LEVEL
#define NEXUS_R7_VAD_LEVEL 320.0f
#endif
#ifndef NEXUS_R7_SILENCE_MS
#define NEXUS_R7_SILENCE_MS 700u
#endif

// Audio sent during capture in small chunks; requires matching /voice/pcm-stream.
// Batch mode remains the default, so unexpected server differences do not block voice.
#if NEXUS_R7_STREAMING
#include <WiFiClient.h>
static bool nexusR7BeginStream(WiFiClient& sock) {
  if (WiFi.status()!=WL_CONNECTED) return false;
  IPAddress host = WiFi.gatewayIP();
  if (host == IPAddress(0,0,0,0) || !sock.connect(host, 8765)) return false;
  sock.setTimeout(3000);
  sock.print("POST /voice/pcm-stream HTTP/1.1\r\nHost: ");
  sock.print(host.toString());
  sock.print(":8765\r\nContent-Type: application/octet-stream\r\n");
  sock.print("X-Device-Token: ");sock.print(DEVICE_TOKEN);
  sock.print("\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n");
  return sock.connected();
}
static bool nexusR7WriteChunk(WiFiClient& sock, const int16_t* pcm, size_t n) {
  if (!sock.connected()) return false;
  const size_t size = n * sizeof(int16_t);
  sock.print(String(size, HEX));sock.print("\r\n");
  if (sock.write(reinterpret_cast<const uint8_t*>(pcm), size) != size) return false;
  sock.print("\r\n");
  return sock.connected();
}
static int nexusR7FinishStream(WiFiClient& sock) {
  if (!sock.connected()) { sock.stop();return -1; }
  sock.print("0\r\n\r\n");
  const uint32_t since = millis();
  while (!sock.available() && sock.connected() && millis()-since < 60000u) {
    delay(20);M5.update();
  }
  int code = -3;
  if (sock.available()) {
    String status=sock.readStringUntil('\n');
    status.trim();
    if (status.startsWith("HTTP/1.")) code=status.substring(9,12).toInt();
  }
  sock.stop();
  return code;
}
#endif

bool recordAndSend() {
  NexusStageGuard trace(42);
  if (WiFi.status()!=WL_CONNECTED) {
    statusMessage="WiFi offline"; displayStatus(); return false;
  }
  // The classic ESP32 has too little contiguous internal RAM for this clip.
  // Use PLUS2 external PSRAM. The first 44 bytes hold a WAV header; the
  // microphone writes PCM straight into the aligned region after that.
  if (!wavBuffer) {
    const size_t totalBytes = 44 + AUDIO_SAMPLES * sizeof(int16_t);
    if (!psramFound()) {
      statusMessage="PSRAM not found";
      currentText="Check BOARD_HAS_PSRAM";
      Serial.printf("NEXUS AUDIO R2: PSRAM not detected\n");
      displayStatus();
      return false;
    }
    wavBuffer = (uint8_t*)heap_caps_malloc(totalBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!wavBuffer) {
      statusMessage="PSRAM alloc failed";
      currentText="Audio memory unavailable";
      Serial.printf("NEXUS AUDIO R2: PSRAM free=%u need=%u\n",
                    (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
                    (unsigned)totalBytes);
      displayStatus();
      return false;
    }
    audioBuffer = (int16_t*)(wavBuffer + 44);
    Serial.printf("NEXUS AUDIO R2: buffer OK (%u B), PSRAM free=%u\n",
                  (unsigned)totalBytes,
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
  }
  // NEXUS_SMART_VOICE_R7: energy-based end-of-speech; no cloud VAD required.
  statusMessage="LISTEN..."; currentText="Speak now"; displayStatus();
  M5.Speaker.end();
  if (!M5.Mic.begin()) {
    statusMessage="Mic init failed"; displayStatus(); return false;
  }
  size_t filled=0;
  bool detected=false;
  bool timeout=false;
  uint8_t voiceBlocks=0;
  uint32_t quietSamples=0;
  const uint32_t startedAt=millis();
  // M5.Mic.record is asynchronous: DO NOT read a block until isRecording()==0.
#if NEXUS_R7_STREAMING
  WiFiClient streamSocket;
  const bool streaming=nexusStreamingEnabled && nexusR7BeginStream(streamSocket);
  bool streamOK=streaming;
  bool sentAny=false;
#endif
  while (filled < AUDIO_SAMPLES && millis()-startedAt<8000u) {
    const size_t count = min(static_cast<size_t>(AUDIO_CHUNK_SAMPLES), AUDIO_SAMPLES-filled);
    if (!M5.Mic.record(audioBuffer+filled,count,AUDIO_RATE)) {
      delay(2);M5.update();continue;
    }
    const uint32_t blockStarted=millis();
    while (M5.Mic.isRecording() && millis()-blockStarted<600u) {
      delay(1);M5.update();
    }
    if (M5.Mic.isRecording()) {timeout=true;break;}
    // Only now is the submitted buffer complete and safe for VAD/network upload.
    float energy=0.f;
    for (size_t j=0;j<count;j+=8) {
      const float v=(float)audioBuffer[filled+j];energy+=v*v;
    }
    const float rms=sqrtf(energy / ((count+7u)/8u));
    // Threshold approximately matches the existing 300 RMS silence gate.
    // Small block count makes very short clicks unlikely to arm VAD.
    const bool voiced=(rms>=NEXUS_R7_VAD_LEVEL);
    if (!detected) {
      if (voiced) {if(voiceBlocks<10) ++voiceBlocks;}
      else voiceBlocks=0;
      if (voiceBlocks>=2) detected=true;
    } else {
      if (voiced) quietSamples=0;
      else quietSamples+=(uint32_t)count;
    }
#if NEXUS_R7_STREAMING
    if (streaming && streamOK) {
      if (!nexusR7WriteChunk(streamSocket,audioBuffer+filled,count)) streamOK=false;
      else sentAny=true;
    }
#endif
    filled+=count;
    // End after roughly 0.7 second of silence following detected speech.
    if (detected && filled>=(size_t)(AUDIO_RATE/2) && quietSamples>=(uint32_t)(AUDIO_RATE*NEXUS_R7_SILENCE_MS/1000u)) break;
    // Do not wait the entire clip if nobody spoke.
    if (!detected && filled >= (size_t)(AUDIO_RATE*5/2)) break;
    M5.update();
  }
  M5.Mic.end();
  const uint32_t recordMs=millis()-startedAt;
  Serial.printf("NEXUS R7 CAPTURE: %lu ms, %u samples, speech=%u\n",
                (unsigned long)recordMs,(unsigned)filled, (unsigned)detected);
  if (timeout || filled<(size_t)(AUDIO_RATE/5)) {
#if NEXUS_R7_STREAMING
    if(streaming) streamSocket.stop();
#endif
    statusMessage=timeout?"Mic timeout":"No audio";displayStatus();return false;
  }
  if (!detected) {
#if NEXUS_R7_STREAMING
    if(streaming) streamSocket.stop();
#endif
    statusMessage="Silence";displayStatus();return false;
  }
  // PCM was written directly into wavBuffer+44; no second 128KB malloc.
  const uint32_t pcmBytes=filled * sizeof(int16_t);
  const size_t wavBytes=44 + pcmBytes;
  uint8_t *wav=wavBuffer;
  memcpy(wav,"RIFF",4);
  uint32_t riffBytes=pcmBytes+36; memcpy(wav+4,&riffBytes,4);
  memcpy(wav+8,"WAVEfmt ",8);
  uint32_t fmtSize=16;memcpy(wav+16,&fmtSize,4);
  uint16_t fmt=1;memcpy(wav+20,&fmt,2);
  uint16_t channels=1;memcpy(wav+22,&channels,2);
  uint32_t rate=AUDIO_RATE;memcpy(wav+24,&rate,4);
  uint32_t byteRate=rate*2;memcpy(wav+28,&byteRate,4);
  uint16_t align=2;memcpy(wav+32,&align,2);
  uint16_t bits=16;memcpy(wav+34,&bits,2);
  memcpy(wav+36,"data",4);memcpy(wav+40,&pcmBytes,4);
  statusMessage="Sending audio...";displayStatus();
  int code=-1;
  String response;
  const uint32_t requestAt=millis();
#if NEXUS_R7_STREAMING
  // If a stream was opened, never retry the same command as a batch upload:
  // the server might already have executed it (double-send is unsafe).
  if (streaming) {
    code=streamOK?nexusR7FinishStream(streamSocket):-3;
    if(!streamOK)streamSocket.stop();
  } else
#endif
  {
    HTTPClient h;
    h.setTimeout(60000);
    updateBridgeAddress();
    if(h.begin(bridgeUrl+"/voice")) {
      h.addHeader("X-Device-Token",DEVICE_TOKEN);
      h.addHeader("Content-Type","audio/wav");
      h.addHeader("X-Nexus-Capture-Ms",String(recordMs));
      code=h.POST(wav,wavBytes);
      if(code==200)response=h.getString();
      h.end();
    }
  }
  Serial.printf("NEXUS R7 VOICE: capture=%lu ms, request_wait=%lu ms, code=%d, bytes=%u\n",
                (unsigned long)recordMs,(unsigned long)(millis()-requestAt),code,(unsigned)wavBytes);
  statusMessage=(code==200)?"Voice received":("Voice HTTP "+String(code));
  if(code==200) {
    JsonDocument doc;
    if (deserializeJson(doc,response)==DeserializationError::Ok)
      currentText=doc["display"].as<String>();
  }
  displayStatus();
  return code==200;
}


#include "NexusPTT_R86.h" // NEXUS_R86_PTT_10SEC

// NEXUS_R88_UNIFIED: minimal behavior changes, NO UI redesign.
#include <math.h>
#include "NexusR88Settings.h"
static bool nexusR88PendingPTT = false;
static uint32_t nexusR88CanceledTTSSeq = 0;

// The user holds the screen toward themselves with the button down.
// Rotation in the YZ plane is rotation around the IMU X axis.
// Convert a *rapid* X-axis flick into one event on the existing UI channel.
// Other axes must be quiet to reject shaking and walking motion.
// NEXUS_R886_ANY_POSITION: side flick independent of gravity/holding angle.
// A gyroscope reports body-relative rotation, even when reclining or upside down.
// A tilt must rotate >= 12 degrees, peak speed >= 65 deg/s. A return motion
// re-arms but NEVER scrolls, and simply holding an angle cannot repeat.
static const char* nexusR886State = "BOOT";
static float nexusR886G[3] = {0.0f,0.0f,0.0f};
static float nexusR886Angle = 0.0f;
static int nexusR886Axis = -1;
static int nexusR886Step = 0;
static uint32_t nexusR886ShowStepUntil = 0;

static bool nexusR88SideFlick(float gx, float gy, float gz) {
  (void)gx; (void)gy; (void)gz; // legacy signature
  static uint8_t phase = 0; // 0=ready; 1=tilting; 2=returning
  static int axis = -1;
  static float total = 0.0f;
  static float peak = 0.0f;
  static bool cameBack = false;
  static uint32_t sampleAt = 0, beginAt = 0, stillAt = 0, firedAt = 0;
  const uint32_t now = millis();

  if (M5.getButton(0).isPressed() || M5.getButton(1).isPressed() || nexusR88PendingPTT) {
    phase = 0; axis = -1; total=0.0f; stillAt=0;
    nexusR886State = "BUTTON";
    return false;
  }
  if (!M5.Imu.isEnabled()) { nexusR886State = "IMU OFF"; return false; }
  M5.Imu.update();
  float x=0.0f,y=0.0f,z=0.0f;
  if (!M5.Imu.getGyro(&x,&y,&z)) { nexusR886State = "NO GYRO"; return false; }
  if (!isfinite(x) || !isfinite(y) || !isfinite(z)) {
    nexusR886State = "BAD GYRO";
    return false;
  }
  nexusR886G[0]=x; nexusR886G[1]=y; nexusR886G[2]=z;
  const float v[3] = {x,y,z};
  float maximum = fabsf(v[0]);
  int dominant = 0;
  if (fabsf(v[1]) > maximum) { maximum=fabsf(v[1]); dominant=1; }
  if (fabsf(v[2]) > maximum) { maximum=fabsf(v[2]); dominant=2; }
  const bool quiet = maximum < 13.0f;
  const uint32_t delta = sampleAt ? now - sampleAt : 25u;
  sampleAt = now;
  const float dt = (float)((delta < 110u) ? delta : 110u)*0.001f;

  // Select an axis *only after* a clear movement. No pose/gravity calibration.
  if (phase == 0) {
    nexusR886State = "READY";
    const int candidate = (NEXUS_R886_AXIS == 3) ? dominant : NEXUS_R886_AXIS;
    if (candidate < 0 || candidate > 2 ||
        fabsf(v[candidate]) < NEXUS_R886_START_DPS ||
        (NEXUS_R886_AXIS == 3 && fabsf(v[candidate]) < maximum * 0.999f))
      return false;
    axis = candidate; phase = 1; total=0.0f; peak=0.0f;
    beginAt = now; stillAt = 0;
    nexusR886Axis = axis;
    nexusR886State = "TILT";
  }

  if (axis < 0 || axis > 2) { phase=0; return false; }
  float vel = (fabsf(v[axis]) < 5.0f) ? 0.0f : v[axis];
  total += vel * dt;
  if (fabsf(vel) > peak) peak=fabsf(vel);
  nexusR886Angle = total;

  if (phase == 1) {
    nexusR886State = "TILT";
    if (fabsf(total) >= NEXUS_R886_TRIGGER_DEG && peak >= NEXUS_R886_START_DPS) {
      const int step = (total >= 0.0f ? 1 : -1) *
                       (NEXUS_R88_INVERT_FLICK ? -1 : 1);
      nexus_ui::adjustScroll(nexus_ui::menuOpen ? step : step * NEXUS_R884_TEXT_LINES);
      nexusR886Step = step;
      nexusR886ShowStepUntil = now + 1100u;
      nexusR886State = step > 0 ? "DOWN" : "UP";
      phase = 2; firedAt=now; stillAt=0; cameBack=false;
      Serial.printf("NEXUS R886 SCROLL axis=%d vel=%.1f angle=%.1f step=%d\n",
                     axis,vel,total,step);
      return true;
    }
    // A hesitant shake is ignored; next gesture can try again.
    if ((uint32_t)(now-beginAt)>700u || (quiet && (uint32_t)(now-beginAt)>240u)) {
      phase=0; axis=-1; total=0.0f; stillAt=0;
      nexusR886State="READY";
    }
    return false;
  }

  // phase 2: a return toward the initial device pose is consumed, NOT scrolled.
  nexusR886State = "RETURN";
  if (fabsf(total) <= NEXUS_R886_REARM_DEG) cameBack = true;
  if (cameBack && quiet) {
    if (!stillAt) stillAt=now;
    if ((uint32_t)(now-stillAt)>=180u &&
        (uint32_t)(now-firedAt)>=NEXUS_R886_COOLDOWN_MS) {
      phase=0; axis=-1; total=0.0f;
      nexusR886State="READY";
    }
  } else stillAt=0;
  // If the user naturally changed their resting pose, accept it as a new
  // zero only after several seconds fully motionless, never while returning.
  if (phase==2 && quiet && (uint32_t)(now-firedAt)>4000u) {
    if (!stillAt) stillAt=now;
    if ((uint32_t)(now-stillAt)>700u) {
      phase=0; axis=-1; total=0.0f;
      nexusR886State="READY";
    }
  }
  return false;
}

// Temporary diagnostics; disable from Termux after observing real gyro values.
static void nexusR886DrawDebug() {
  // NEXUS_R887_MENU_SECTIONS: diagnostics render ONLY inside their own page.
  if (!nexus_ui::diagnosticsPage()) return;
  static uint32_t last=0;
  const uint32_t now=millis();
  if ((uint32_t)(now-last) < 160u) return;
  last=now;
  auto& d=M5.Display;
  if (d.height()<55 || d.width()<90) return;
  const int yy=d.height()-61;
  d.pushState();
  d.fillRect(0,yy,d.width(),36,TFT_BLACK);
  d.setFont(&fonts::Font0);
  d.setTextSize(1);
  d.setTextColor(TFT_YELLOW,TFT_BLACK);
  d.setCursor(2,yy+2);
  d.printf("GX:%+.0f GY:%+.0f",nexusR886G[0],nexusR886G[1]);
  d.setCursor(2,yy+13);
  d.printf("GZ:%+.0f A:%d D:%+.0f",nexusR886G[2],nexusR886Axis,nexusR886Angle);
  d.setCursor(2,yy+24);
  if ((int32_t)(nexusR886ShowStepUntil-now)>0) {
    d.printf("STEP %+d",nexus_ui::menuOpen ? nexusR886Step : nexusR886Step*NEXUS_R884_TEXT_LINES);
  } else d.print(nexusR886State);
  d.popState();

}

// Draw ONLY while the existing settings menu is open. Adds a small footer;
// it does not replace or reorder any UI screens or menu items.
static void nexusR88DrawBattery() {
  if (!nexus_ui::menuOpen) return;
  static uint32_t lastRead = 0;
  static int batteryPercent = -1;
  const uint32_t now = millis();
  if (batteryPercent < 0 || (uint32_t)(now - lastRead) >= NEXUS_R88_BATTERY_REFRESH_MS) {
    lastRead = now;
    batteryPercent = M5.Power.getBatteryLevel();
    if (batteryPercent > 100) batteryPercent = 100;
  }
  const int16_t w = (int16_t)M5.Display.width();
  const int16_t h = (int16_t)M5.Display.height();
  if (w < 85 || h < 30) return;
  const int16_t x = w - 70;
  const int16_t y = h - 13;
  M5.Display.pushState();
  M5.Display.fillRect(x, y, 70, 13, TFT_BLACK);
  M5.Display.setFont(&fonts::Font0);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(TFT_CYAN, TFT_BLACK);
  M5.Display.setCursor(x + 2, y + 2);
  M5.Display.print("BAT ");
  if (batteryPercent < 0) M5.Display.print("--");
  else M5.Display.print(batteryPercent);
  M5.Display.print('%');
  M5.Display.popState();
}

// Button C is an actual power/wake button on StickC Plus2; per the M5 docs
// its `wasHold()` event is supported, but not all ordinary button callbacks.
static void nexusR88PowerButton() {
  if (!M5.getButton(4).wasHold()) return;
  statusMessage = "POWER OFF";
  displayStatus();
  M5.Speaker.stop();
  M5.Speaker.end();
  M5.Mic.end();
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  delay(100);
  M5.Power.powerOff();
  // On USB power a true battery disconnect is not possible; fall back to
  // low power sleep if the hardware did not cut power.
  M5.Power.deepSleep();
}


// NEXUS_BUZZER_R4: experimental speech over passive GPIO2 buzzer
static uint32_t nexusTtsLastSeq = 0;

// One short audio preview per completed AI response. Does NOT use phone speaker.
// Piezo cannot faithfully reproduce the full speech frequency range.
bool nexusSpeakLatest(uint32_t seq) {
  if (seq == nexusR88CanceledTTSSeq) return false; // R88 stale audio
  if (WiFi.status()!=WL_CONNECTED || bridgeUrl.length() == 0) return false;
  NexusStageGuard trace(45);
  HTTPClient req;
  req.setTimeout(15000);
  const String url = bridgeUrl + "/tts/pcm?seq=" + String(seq);
  if (!req.begin(url)) return false;
  req.addHeader("X-Device-Token",DEVICE_TOKEN);
  const int code = req.GET();
  if (code != 200) {
    Serial.printf("NEXUS BUZZER: TTS HTTP %d\n", code);
    req.end();
    return false;
  }
  const int bytes = req.getSize();
  if (bytes < 64 || bytes > 120000 || !psramFound()) {
    Serial.printf("NEXUS BUZZER: bad PCM size=%d / PSRAM=%d\n", bytes, psramFound());
    req.end();
    return false;
  }
  uint8_t *data = (uint8_t*)heap_caps_malloc((size_t)bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!data) { Serial.println("NEXUS BUZZER: audio allocation failed"); req.end(); return false; }
  auto *stream = req.getStreamPtr();
  size_t got=0;
  const uint32_t started=millis();
  while (got < (size_t)bytes && millis()-started < 20000u) {
    M5.update();
    if (M5.getButton(0).isPressed()) {
      nexusR88CanceledTTSSeq = seq;
      nexusR88PendingPTT = true;
      req.end();
      heap_caps_free(data);
      return false;
    }
    int avail = stream->available();
    if (avail > 0) {
      const size_t remaining=(size_t)bytes-got;
      const size_t want=remaining>2048u?2048u:remaining;
      const int take=avail < (int)want ? avail : (int)want;
      int received=stream->read(data+got, (size_t)take);
      if (received > 0) got += (size_t)received;
    } else {
      delay(5);
    }
  }
  req.end();
  if (got != (size_t)bytes) {
    Serial.printf("NEXUS BUZZER: PCM partial %u/%d\n",(unsigned)got,bytes);
    heap_caps_free(data);
    return false;
  }
  // Audiofix R2 already stopped the microphone after its recording phase.
  // M5Unified's built-in buzzer backend is used, not DAC or an extra speaker.
  if (!M5.Speaker.begin()) { heap_caps_free(data); Serial.println("NEXUS BUZZER: speaker init failed"); return false; }
  M5.Speaker.setVolume(220);
  const String previous=statusMessage;
  statusMessage="SPEAKING";
  displayStatus();
  const bool queued=M5.Speaker.playRaw((const uint8_t*)data,(size_t)bytes,8000,false,1,-1,true);
  if (queued) {
    const uint32_t voiceStarted=millis();
    while (M5.Speaker.isPlaying() && millis()-voiceStarted < 16000u) {
      M5.update();
      if (M5.getButton(0).isPressed()) {
        nexusR88CanceledTTSSeq = seq;
        nexusR88PendingPTT = true;
        M5.Speaker.stop();
        break;
      }
      delay(5);
    }
  } else Serial.println("NEXUS BUZZER: playRaw failed");
  M5.Speaker.stop();
  M5.Speaker.end();
  heap_caps_free(data);
  statusMessage=previous;
  displayStatus();
  return queued;
}

void fetchStatus() {
  NexusStageGuard trace(40);
  if(WiFi.status()!=WL_CONNECTED)return;
  uint32_t speakSeq=0;
  HTTPClient h;h.setTimeout(1500);
  updateBridgeAddress(); h.begin(bridgeUrl+"/status");
  h.addHeader("X-Device-Token",DEVICE_TOKEN);
  if(h.GET()==200) {
    JsonDocument j;
    if(deserializeJson(j,h.getString())==DeserializationError::Ok) {
      currentText=j["display"].as<String>();
      statusMessage=j["state"].as<String>();
      approvalPending = !j["approval"].isNull();
      if(!j["settings"].isNull()) {
        nexusTtsEnabled=j["settings"]["tts"].as<bool>();
        nexusLocalSttEnabled=j["settings"]["local_stt"].as<bool>();
        nexusStreamingEnabled=j["settings"]["streaming"].as<bool>();
        nexusAutoScroll=j["settings"]["autoscroll"].as<bool>();
      }
      const uint32_t nextSeq = j["seq"].as<uint32_t>();
      if (statusMessage.startsWith("ANSWER") && !approvalPending &&
          nextSeq != 0u && nextSeq != nexusTtsLastSeq) {
        nexusTtsLastSeq = nextSeq;  // No endless replay if synthesis fails.
        speakSeq = nextSeq;
      }
    }
  }
  h.end();
  if (speakSeq && nexusTtsEnabled) {
    // Show the answer BEFORE potentially slow eSpeak/buzzer playback.
    displayStatus();
    nexusSpeakLatest(speakSeq);
  }
}


// Experimental IMU gesture sample: six aggregate features from a short 1-second
// window (mean absolute gyro X/Y/Z and net gyro rotation X/Y/Z).
// This is orientation / gesture classification, not spatial position tracking.
void sendGestureSample() {
  NexusStageGuard trace(43);
  if(WiFi.status()!=WL_CONNECTED)return;
  statusMessage="MOVE DEVICE...";displayStatus();
  float absSum[3]={0,0,0};float sums[3]={0,0,0};
  const int frames=20;
  for(int i=0;i<frames;i++) {
    M5.update();
    float x=0,y=0,z=0;
    if(M5.Imu.getGyro(&x,&y,&z)) {
      const float v[3]={x,y,z};
      for(int k=0;k<3;k++){absSum[k]+=fabsf(v[k]);sums[k]+=v[k];}
    }
    delay(50);
  }
  JsonDocument j;
  JsonArray features=j["features"].to<JsonArray>();
  for(int k=0;k<3;k++)features.add(absSum[k]/frames);
  for(int k=0;k<3;k++)features.add(sums[k]/frames);
  String body;serializeJson(j,body);
  HTTPClient h;updateBridgeAddress(); h.begin(bridgeUrl+"/gesture/sample");
  h.setTimeout(3500);
  h.addHeader("Content-Type","application/json");
  h.addHeader("X-Device-Token",DEVICE_TOKEN);
  int code=h.POST(body);
  statusMessage=code==200?"Gesture sent":"Gesture failed";
  h.end();displayStatus();
}

void updateBridgeAddress() {
  if (WiFi.status() != WL_CONNECTED) return;
  IPAddress gateway = WiFi.gatewayIP();
  if (gateway == IPAddress(0, 0, 0, 0)) return;
  bridgeUrl = "http://" + gateway.toString() + ":8765";
}
void setup() {
  nexusBeginTrace();
  NexusStageGuard trace(10);
  auto cfg=M5.config(); M5.begin(cfg);
  esp_reset_reason_t resetReason = esp_reset_reason();
  Serial.begin(115200);
  Serial.printf("NEXUS RESET REASON: %d\n", (int)resetReason);
  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(5, 5);
  M5.Display.setTextColor(WHITE);
  M5.Display.printf("RESET: %d\nPREV STAGE: %lu\nBOOTFIX R1",
                    (int)resetReason, (unsigned long)nexusPreviousStage);
  Serial.printf("NEXUS BOOTFIX R1: previous_stage=%lu\n",
                (unsigned long)nexusPreviousStage);
  delay(2500);
  M5.Display.setRotation(NEXUS_R8_PORTRAIT_ROTATION);
  M5.Display.setTextWrap(true);
  // Voice buffer allocated lazily in PSRAM on first A press.
#ifdef NEXUS_ENABLE_BLE
  NexusBle::init();
#endif
  WiFi.mode(WIFI_STA);WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
  statusMessage="Connecting WiFi";displayStatus();
  uint32_t started=millis();
  while(WiFi.status()!=WL_CONNECTED && millis()-started<10000)delay(100);
  lastUserActivity=millis();M5.Display.setBrightness(110);
  statusMessage=(WiFi.status()==WL_CONNECTED)?"WiFi connected":"Offline";
  displayStatus();
}


// NEXUS R8: only four bounded settings, authenticated by the existing device token.
bool nexusSetDeviceSetting(int index) {
  if(WiFi.status()!=WL_CONNECTED) {statusMessage="WiFi offline"; displayStatus();return false;}
  const char* keys[]={"tts","local_stt","streaming","autoscroll"};
  if(index<0 || index>=4)return false;
  const bool values[]={nexusTtsEnabled,nexusLocalSttEnabled,nexusStreamingEnabled,nexusAutoScroll};
  JsonDocument j;
  j["key"]=keys[index];j["value"]=!values[index];
  String body;serializeJson(j,body);
  updateBridgeAddress();
  HTTPClient h;h.setTimeout(3000);
  if(!h.begin(bridgeUrl+"/device/settings")){statusMessage="Settings offline";displayStatus();return false;}
  h.addHeader("X-Device-Token",DEVICE_TOKEN);
  h.addHeader("Content-Type","application/json");
  const int code=h.POST(body);
  h.end();
  statusMessage=code==200?"Settings saved":code==409?"Start local STT":"Settings HTTP "+String(code);
  displayStatus();
  if(code==200)fetchStatus();
  return code==200;
}

void loop() {
  { NexusStageGuard trace(20); M5.update(); }
  nexusR88PowerButton();
  NexusStageGuard trace(21);
  if(WiFi.status()!=WL_CONNECTED && millis()-lastWifiAttempt>15000){
    NexusStageGuard wifiTrace(23);
    lastWifiAttempt=millis();WiFi.disconnect();WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
  }
  if(M5.getButton(0).isPressed() || M5.getButton(1).isPressed())lastUserActivity=millis();
  bool dim=(millis()-lastUserActivity>60000);
  if(dim!=screenDimmed){M5.Display.setBrightness(dim?22:110);screenDimmed=dim;}

  if(nexusR88PendingPTT || M5.getButton(0).wasPressed()) {
    nexusR88PendingPTT = false;
    if(nexus_ui::menuOpen) {
      nexusSetDeviceSetting(nexus_ui::settingIndex());
    } else {
      bool ok=nexusPTTRecordAndSend(); // NEXUS_R86_PTT_10SEC
      sessionClips=ok?1:0;
      nextClipAt=millis()+1600;
    }
    lastUserActivity=millis();
  }
  // Follow-up mode keeps conversation context, but NEVER starts the mic by itself.
  // The user presses A for each turn; this avoids accidental unattended recordings.
  if(M5.getButton(1).wasPressed()) {btnBPressAt=millis();btnBHandled=false;}
  if(M5.getButton(1).isPressed() && !btnBHandled && millis()-btnBPressAt>(approvalPending ? 3000u : 1400u)) {
    btnBHandled=true;
    if(approvalPending) {
      bool ok=postEvent("confirm_button");
      statusMessage=ok?"Confirmation sent":"Confirm failed";displayStatus();
    } else {
      // Hold B while settings is open retains the existing gesture training.
      // Hold B on the main screen toggles dialogue mode.
      if(nexus_ui::menuOpen) {
        sendGestureSample();
      } else {
        dialogMode=!dialogMode;
        sessionClips=0;
        postEvent(dialogMode?"session_start":"session_stop");
        statusMessage=dialogMode?"Dialogue ON (press A)":"Push-to-talk";
        displayStatus();
      }
    }
  }
  if(M5.getButton(1).wasReleased() && !btnBHandled) {
    nexus_ui::toggleMenu();
    lastUserActivity=millis();
    displayStatus();
    nexusR88DrawBattery();
  }
  {
    // NEXUS_R886_IMU_LOOP: sample gyro in the hardware coordinate system.
    NexusStageGuard imuTrace(30);
    if (nexusR88SideFlick(0.0f,0.0f,0.0f)) {
      lastUserActivity=millis();
      nexus_ui::render();
    }
    nexusR886DrawDebug();
  }
  if(millis()-lastPoll>700u) {fetchStatus();lastPoll=millis();displayStatus();nexusR88DrawBattery();} // NEXUS_R85_QUICK_RESPONSE
  if (millis()-lastAnimation>(screenDimmed?2000u:600u)) {displayStatus();nexusR88DrawBattery();lastAnimation=millis();}
  delay(25);
}
