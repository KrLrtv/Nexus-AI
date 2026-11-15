// NEXUS_R86_PTT_10SEC: holding A records; releasing A finishes one request.
// HTTP Transfer-Encoding: chunked carries 512-sample PCM16 frames to Termux;
// Termux groups the continuous PCM into <=10s blocks for sherpa-onnx.
// No full 10s clip ever resides on the ESP32. A bounded 1KB mic buffer is used.
#pragma once

#include <WiFiClient.h>

static constexpr uint32_t NEXUS_PTT_MAX_MS = 120000u;
static constexpr uint32_t NEXUS_PTT_RATE = 16000u;
static constexpr size_t NEXUS_PTT_FRAME_SAMPLES = 512u;
static constexpr uint32_t NEXUS_PTT_MIN_SAMPLES = 3200u; // 0.2s
static int16_t nexusPttFrame[NEXUS_PTT_FRAME_SAMPLES];

static bool nexusPttOpen(WiFiClient& client) {
  if (WiFi.status() != WL_CONNECTED) return false;
  const IPAddress host = WiFi.gatewayIP();
  if (host == IPAddress(0, 0, 0, 0) || !client.connect(host, 8765)) return false;
  client.setTimeout(3000);
  client.print("POST /voice/ptt-stream HTTP/1.1\r\nHost: ");
  client.print(host.toString());
  client.print(":8765\r\nContent-Type: application/octet-stream\r\n");
  client.print("X-Device-Token: ");
  client.print(DEVICE_TOKEN);
  client.print("\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n");
  return client.connected();
}

static bool nexusPttWrite(WiFiClient& client, const uint8_t* data, size_t count) {
  // Include the 2-byte application frame header in the HTTP chunk length.
  if (!client.connected() || count > 2048u || (count & 1u)) return false;
  const size_t total = count + 2u;
  if (!client.print(String((unsigned int)total, HEX)) || !client.print("\r\n")) return false;
  const uint8_t header[2] = {uint8_t(count & 0xFFu), uint8_t((count >> 8) & 0xFFu)};
  if (client.write(header, 2) != 2) return false;
  if (count && client.write(data, count) != count) return false;
  if (!client.print("\r\n")) return false;
  return client.connected();
}

static int nexusPttFinish(WiFiClient& client) {
  // Frame length 0 is an explicit release marker. No processing without it.
  if (!nexusPttWrite(client, nullptr, 0u)) {client.stop();return -3;}
  client.print("0\r\n\r\n"); // End HTTP body.
  const uint32_t begun = millis();
  int result = -3;
  while (millis()-begun < 12000u) {
    M5.update();
    if (client.available()) {
      String firstLine = client.readStringUntil('\n');
      firstLine.trim();
      if (firstLine.startsWith("HTTP/1.")) {
        result = firstLine.substring(9,12).toInt();
      }
      break;
    }
    if (!client.connected()) break;
    delay(15);
  }
  client.stop();
  return result;
}

static bool nexusPTTRecordAndSend() {
  NexusStageGuard trace(46);
  if (WiFi.status() != WL_CONNECTED) {
    statusMessage = "WiFi offline";displayStatus();return false;
  }
  WiFiClient client;
  if (!nexusPttOpen(client)) {
    statusMessage = "PTT connect failed";displayStatus();return false;
  }
  M5.Speaker.end();
  if (!M5.Mic.begin()) {
    client.stop();statusMessage="Mic init failed";displayStatus();return false;
  }
  uint32_t totalSamples = 0u;
  const uint32_t begun = millis();
  uint32_t updateAt = begun;
  bool failed = false;
  bool reachedLimit = false;
  statusMessage = "REC - hold A";
  currentText = "Release A to send";
  displayStatus();
  while (true) {
    M5.update();
    if (!M5.getButton(0).isPressed()) break;
    if (millis()-begun >= NEXUS_PTT_MAX_MS) {reachedLimit=true;break;}
    if (WiFi.status()!=WL_CONNECTED || !client.connected()) {failed=true;break;}
    if (!M5.Mic.record(nexusPttFrame, NEXUS_PTT_FRAME_SAMPLES, NEXUS_PTT_RATE)) {
      delay(2);
      continue;
    }
    const uint32_t frameAt = millis();
    while (M5.Mic.isRecording() && millis()-frameAt < 750u) {
      M5.update();
      delay(1);
    }
    if (M5.Mic.isRecording()) {failed=true;break;}
    if (!nexusPttWrite(client,
            reinterpret_cast<const uint8_t*>(nexusPttFrame),
            NEXUS_PTT_FRAME_SAMPLES*sizeof(int16_t))) {failed=true;break;}
    totalSamples += NEXUS_PTT_FRAME_SAMPLES;
    if (millis()-updateAt > 1000u) {
      updateAt=millis();
      statusMessage = "REC " + String(totalSamples / NEXUS_PTT_RATE) + "s";
      displayStatus();
    }
    delay(1);
  }
  M5.Mic.end();
  if (reachedLimit || failed || totalSamples < NEXUS_PTT_MIN_SAMPLES) {
    // Deliberately omit application end marker and HTTP terminator. Server
    // will discard incomplete audio; no truncated device command can execute.
    client.stop();
    statusMessage = reachedLimit ? "PTT limit 120s" :
                    failed ? "PTT network error" : "PTT too short";
    displayStatus();
    Serial.printf("NEXUS R86 PTT ABORT samples=%lu failed=%d limit=%d\n",
                  (unsigned long)totalSamples,(int)failed,(int)reachedLimit);
    return false;
  }
  statusMessage = "Sending PTT"; displayStatus();
  const int code = nexusPttFinish(client);
  Serial.printf("NEXUS R86 PTT sent=%lu samples in %lu ms HTTP=%d\n",
                (unsigned long)totalSamples,(unsigned long)(millis()-begun),code);
  statusMessage = (code==202) ? "Recognizing..." : ("PTT HTTP "+String(code));
  displayStatus();
  return code == 202;
}
