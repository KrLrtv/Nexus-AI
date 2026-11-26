from pathlib import Path
p=Path('native/firmware/src/main.cpp')
s=p.read_text()
s=s.replace('#include <esp_system.h>', '#include <esp_system.h>\n#include <nimble/nimble/host/include/host/ble_gatt.h>\n#include <nimble/nimble/host/include/host/ble_hs_mbuf.h>\n#include <nimble/nimble/host/include/host/ble_att.h>')
s=s.replace('const char* TX=', 'const char* TX_UUID=').replace('const char* RX=', 'const char* RX_UUID=')
s=s.replace('createCharacteristic(TX,', 'createCharacteristic(TX_UUID,').replace('createCharacteristic(RX,', 'createCharacteristic(RX_UUID,')
s=s.replace('static NimBLEServer* server=nullptr;', 'static NimBLEServer* server=nullptr;\nstatic volatile uint16_t peerHandle=BLE_HS_CONN_HANDLE_NONE;')
s=s.replace('connected=true; authenticated=false; subscribed=false;', 'peerHandle=desc->conn_handle; connected=true; authenticated=false; subscribed=false;')
s=s.replace('connected=false; authenticated=false; subscribed=false; message', 'connected=false; authenticated=false; subscribed=false; peerHandle=BLE_HS_CONN_HANDLE_NONE; message')
old='  tx->setValue(data,length); return tx->notify();'
assert old in s
s=s.replace(old, '''  const uint16_t handle=peerHandle;
  if(handle==BLE_HS_CONN_HANDLE_NONE || ble_att_mtu(handle)<length+3) return false;
  // NimBLE 1.4.3 notify() returns void. Use the pinned stack API to check
  // queue acceptance; this is NOT a delivery acknowledgement. Android checks
  // sequence numbers and the final frame count before accepting a recording.
  os_mbuf* buffer=ble_hs_mbuf_from_flat(data,length);
  if(!buffer) return false;
  // The stack consumes buffer even when an error is returned.
  return ble_gatts_notify_custom(handle,tx->getHandle(),buffer)==0;''')
p.write_text(s)
p=Path('native/firmware/platformio.ini');s=p.read_text()
assert '-DBOARD_HAS_PSRAM' in s
s=s.replace('-DBOARD_HAS_PSRAM','-DBOARD_HAS_PSRAM\n  -DCONFIG_BT_NIMBLE_MAX_CONNECTIONS=1')
p.write_text(s)
