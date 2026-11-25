"""Verify original source archive and apply explicit, reviewed compatibility repairs."""
from pathlib import Path
import hashlib, io, tarfile, sys, re
root = Path(__file__).resolve().parent
raw = b''.join(p.read_bytes() for p in sorted((root / 'parts').glob('*.bin')))
expected = '250723ba2dd0c24e21206e3f64aadee69ae1de95871dc71e34628bfc12dd8232'
if hashlib.sha256(raw).hexdigest() != expected:
    raise SystemExit('Source archive SHA256 mismatch; refusing extraction')
output = Path(sys.argv[1] if len(sys.argv)>1 else 'work')
output.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:xz') as archive:
    archive.extractall(output, filter='data')
def replace_file(path, before, changes):
    p = output / path
    if hashlib.sha256(p.read_bytes()).hexdigest() != before:
        raise SystemExit('Unexpected base source: ' + path)
    text = p.read_text(encoding='utf-8')
    for old, new in changes:
        if text.count(old) != 1:
            raise SystemExit('Unexpected source pattern: ' + path)
        text = text.replace(old, new)
    p.write_bytes(text.encode('utf-8'))
p=output/'firmware/src/main.cpp'
assert hashlib.sha256(p.read_bytes()).hexdigest()=='1d0f056881ad2c5cb014804f9796f36daf6b5ac4b45da5d232b194017d7c6589'
s,n=re.subn(r'\blink\b','btLink',p.read_text(encoding='utf-8'))
assert n==16
s=s.replace('#include <M5StickCPlus2.h>','#include <M5Unified.h>').replace('StickCP2.','M5.')
p.write_bytes(s.encode('utf-8'))
replace_file('firmware/platformio.ini', '7ed6a6478178ee972186eae09b3bd19bf068dd0db4f01876a256479f8939e618', [('lib_deps = m5stack/M5StickCPlus2@1.0.2', 'lib_deps =\n  m5stack/M5Unified@0.3.0\n  m5stack/M5GFX@0.3.0')])
replace_file('android/app/src/main/AndroidManifest.xml', '532c9b3fe0e34f1dddac793570c6b9d369cd09037e13bbffa0f47650aa7925c0', [(' <uses-feature android:name="android.hardware.bluetooth" android:required="false"/>', ' <uses-feature android:name="android.hardware.bluetooth" android:required="false"/>\n <uses-feature android:name="android.hardware.telephony" android:required="false"/>\n <queries><intent><action android:name="android.intent.action.TTS_SERVICE"/></intent></queries>')])
replace_file('android/app/src/main/java/ru/nexus/link/MainActivity.java', '7941bdb21b57e999491c5ff536570c9164d86b4e411165ac93309b58a3a40f51', [(' private void selectDevice(){\n  if(!hasBluetoothPermission()){permissions();return;}\n  try{BluetoothAdapter a=((BluetoothManager)getSystemService(BLUETOOTH_SERVICE)).getAdapter();if(a==null||!a.isEnabled()){state.setText("Включи Bluetooth");return;}\n   List<BluetoothDevice> devices=new ArrayList<>(a.getBondedDevices());if(devices.isEmpty()){state.setText("Сначала выполни системное сопряжение с NEXUS-Link");return;}\n   String[] names=new String[devices.size()];for(int n=0;n<names.length;n++)names[n]=devices.get(n).getName()+"\\n"+devices.get(n).getAddress();\n   new AlertDialog.Builder(this).setTitle("Выбери NEXUS-Link").setItems(names,(d,n)->{BluetoothDevice chosen=devices.get(n);Vault.prefs(this).edit().putString("mac",chosen.getAddress()).putString("device_name",chosen.getName()).apply();device.setText("Выбран: "+chosen.getName());service("RECONNECT",null);}).show();\n  }catch(Exception e){state.setText("Bluetooth: "+e.getMessage());}\n }\n', ' private void selectDevice(){\n  if(Build.VERSION.SDK_INT>=31&&checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED){permissions();return;}\n  try{BluetoothAdapter a=((BluetoothManager)getSystemService(BLUETOOTH_SERVICE)).getAdapter();if(a==null||!a.isEnabled()){state.setText("Включи Bluetooth");return;}\n   List<BluetoothDevice> devices=new ArrayList<>(a.getBondedDevices());if(devices.isEmpty()){state.setText("Сначала выполни системное сопряжение с NEXUS-Link");return;}\n   String[] names=new String[devices.size()];for(int n=0;n<names.length;n++)names[n]=devices.get(n).getName()+"\\n"+devices.get(n).getAddress();\n   new AlertDialog.Builder(this).setTitle("Выбери NEXUS-Link").setItems(names,(d,n)->chooseDevice(devices.get(n))).show();\n  }catch(SecurityException e){state.setText("Доступ Bluetooth отозван. Разреши подключение заново.");}\n   catch(Exception e){state.setText("Bluetooth: "+e.getMessage());}\n }\n private void chooseDevice(BluetoothDevice chosen){\n  if(Build.VERSION.SDK_INT>=31&&checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED){permissions();return;}\n  try{String name=chosen.getName();Vault.prefs(this).edit().putString("mac",chosen.getAddress()).putString("device_name",name).apply();device.setText("Выбран: "+name);service("RECONNECT",null);}\n  catch(SecurityException e){state.setText("Доступ Bluetooth отозван. Выбери устройство после выдачи разрешения.");}\n }\n'), (' protected void onStart(){', ' // Android 26-32 use the legacy overload; broadcasts are explicitly package-scoped.\n @android.annotation.SuppressLint("UnspecifiedRegisterReceiverFlag")\n protected void onStart(){')])
replace_file('android/app/src/main/java/ru/nexus/link/LinkService.java', 'd6d069f1487550d0a30ded9db156d948d463c86b565accdc2f198b362b905161', [(' @SuppressWarnings("deprecation") private void sendSms', ' // Pre-33 broadcasts target this package; newer Android explicitly uses NOT_EXPORTED.\n @android.annotation.SuppressLint("UnspecifiedRegisterReceiverFlag")\n @SuppressWarnings("deprecation") private void sendSms')])
expected_files={'firmware/src/main.cpp': '0edb442dfa368de510fbe212b2680846ac5ee68a2baef51915bb36e08a92e229', 'firmware/platformio.ini': '1386449686978f96cf2a09f0e05f41cafea188c7883dfc4c3fb93bde9f6c4f76', 'android/app/src/main/AndroidManifest.xml': 'd3bb375652f7b78bfc11bb610e332c6793985a9eb2608c7c66a32459e297b466', 'android/app/src/main/java/ru/nexus/link/MainActivity.java': 'a7607915ef809eede6ecb47eb8bb56421793c0a6c7a9f4092522469b6b278679', 'android/app/src/main/java/ru/nexus/link/LinkService.java': 'fa4e96922213baad2bc3dfba10ab83dc7f7426d44159651a9fe3817be7e0cad0'}
for f, h in expected_files.items():
    assert hashlib.sha256((output/f).read_bytes()).hexdigest()==h, f
print('Archive and repaired source checksums verified.')
