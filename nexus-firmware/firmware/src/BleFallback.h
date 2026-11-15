#pragma once
// Optional BLE short-command fallback. This is NOT an audio transport.
// Enable -DNEXUS_ENABLE_BLE in platformio.ini, then test bonding on your phone.
#ifdef NEXUS_ENABLE_BLE
#include <NimBLEDevice.h>
#include <string>
namespace NexusBle {
static const char* SERVICE_ID="a5d00001-3726-4bc1-a125-642b29dc197b";
static const char* COMMAND_ID="a5d00002-3726-4bc1-a125-642b29dc197b";
static NimBLEClient* client=nullptr;
static NimBLERemoteCharacteristic* command=nullptr;
static unsigned long lastTry=0;
static void init(){
  NimBLEDevice::init("NEXUS_M5");
  NimBLEDevice::setSecurityAuth(true,true,true);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
}
static bool reconnect(){
  if(command && client && client->isConnected())return true;
  command=nullptr;
  if(millis()-lastTry<15000)return false;
  lastTry=millis();
  NimBLEScan* scanner=NimBLEDevice::getScan();
  scanner->setActiveScan(true);
  NimBLEScanResults results=scanner->start(2,false);
  NimBLEAdvertisedDevice* target=nullptr;
  for(int i=0;i<results.getCount();i++){
    auto dev=results.getDevice(i);
    if(dev.isAdvertisingService(NimBLEUUID(SERVICE_ID))){
      // Caller owns a copy; not a pointer to scan result after clearResults.
      target=new NimBLEAdvertisedDevice(dev);break;
    }
  }
  if(!target){scanner->clearResults();return false;}
  if(!client)client=NimBLEDevice::createClient();
  bool connected=client->connect(target);
  delete target;scanner->clearResults();
  if(!connected)return false;
  auto service=client->getService(SERVICE_ID);
  if(!service){client->disconnect();return false;}
  command=service->getCharacteristic(COMMAND_ID);
  if(!command){client->disconnect();return false;}
  return true;
}
static bool send(const char* event){
  if(!reconnect())return false;
  std::string msg(event);
  if(msg!="stop" && msg!="shake" && msg!="session_start" && msg!="session_stop")return false;
  // GATT protected by encrypted write, application enforces bonded peer.
  return command->writeValue((uint8_t*)msg.data(),msg.size(),true);
}
}
#endif
