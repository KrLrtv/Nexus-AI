#pragma once
// NEXUS_R8_MINIMAL_CONTROL: portrait-only 135x240, device-relative gyro navigation,
// UTF-8 Cyrillic, selected LaTeX-like math constructs, scrollable settings.
#include <vector>
#include <algorithm>
#include <math.h>

#ifndef NEXUS_R8_PORTRAIT_ROTATION
#define NEXUS_R8_PORTRAIT_ROTATION 0  // set 2 if the hardware is upside-down
#endif
#ifndef NEXUS_R8_SCROLL_GYRO_AXIS
#define NEXUS_R8_SCROLL_GYRO_AXIS 1   // 0 = gx, 1 = gy (device LONG axis), 2 = gz
#endif

namespace nexus_ui {
constexpr uint16_t BG = 0x0841;
constexpr uint16_t BORDER = 0x2146;
constexpr uint16_t TEXT = 0xF79E;
constexpr uint16_t MUTED = 0x8C51;
constexpr uint16_t ACCENT = 0x6F7A;
constexpr uint16_t WARNING = 0xFE60;
constexpr int HEADER_H = 23;
constexpr int FOOTER_H = 17;
constexpr int LINE_HEIGHT = 17;
constexpr size_t MAX_RESPONSE_BYTES = 3600;
constexpr uint32_t AUTO_SCROLL_MS = 2800u;
constexpr uint32_t MANUAL_PAUSE_MS = 7000u;

static bool dirty = true;
static bool menuOpen = false;
static int menuIndex = 0;
static String cachedText;
static String cachedState;
static bool cachedOnline = false;
static bool cachedApproval = false;
static bool cachedDialog = false;
static bool cachedMenu = false;
static int cachedMenuIndex = -1;
static uint8_t cachedSettings = 0xFF;
static std::vector<String> rows;
static size_t rowStart = 0;
static uint32_t lastAuto = 0;
static uint32_t lastManual = 0;
static uint32_t lastGyro = 0;
static float gyroAccum = 0.f;

inline size_t nextUtf8(const String& s, size_t i) {
  if (i >= s.length()) return i;
  uint8_t c = (uint8_t)s[i];
  size_t count = (c & 0x80) == 0 ? 1 : (c & 0xE0) == 0xC0 ? 2 : (c & 0xF0) == 0xE0 ? 3 : (c & 0xF8) == 0xF0 ? 4 : 1;
  return std::min(s.length(), i + count);
}

inline String replaceMath(String s) {
  s.replace("\\times", "×"); s.replace("\\cdot", "·");
  s.replace("\\pi", "π"); s.replace("\\alpha", "α");
  s.replace("\\beta", "β"); s.replace("\\theta", "θ");
  s.replace("\\lambda", "λ"); s.replace("\\Delta", "Δ");
  s.replace("\\leq", "≤"); s.replace("\\geq", "≥");
  s.replace("\\infty", "∞"); s.replace("\\int", "∫"); s.replace("\\sum", "∑");
  s.replace("\\pm", "±"); s.replace("\\neq", "≠");
  s.replace("**", ""); s.replace("##", "");
  s.replace("^2", "²"); s.replace("^3", "³");
  s.replace("_1", "₁"); s.replace("_2", "₂"); s.replace("_3", "₃");
  // Inline square root is displayed as a root symbol plus a parenthesized operand.
  int at = s.indexOf("\\sqrt{");
  while (at >= 0) {
    int end = s.indexOf('}', at + 6);
    if (end < 0) break;
    String replacement = "√(" + s.substring(at + 6, end) + ")";
    s = s.substring(0, at) + replacement + s.substring(end + 1);
    at = s.indexOf("\\sqrt{", at + replacement.length());
  }
  // Render inline fractions compactly; stand-alone blocks get a true bar.
  int frac = s.indexOf("\\frac{");
  while (frac >= 0) {
    int firstEnd=s.indexOf('}',frac+6);
    if(firstEnd<0 || firstEnd+1 >= (int)s.length() || s[firstEnd+1]!='{') break;
    int secondEnd=s.indexOf('}',firstEnd+2);
    if(secondEnd<0)break;
    String replacement = s.substring(frac+6,firstEnd)+"/"+s.substring(firstEnd+2,secondEnd);
    s=s.substring(0,frac)+replacement+s.substring(secondEnd+1);
    frac=s.indexOf("\\frac{",frac+replacement.length());
  }
  s.replace("\\(", ""); s.replace("\\)", "");
  s.replace("$$", ""); s.replace("$", "");
  return s;
}

// A stand-alone formula line $$\\frac{a+b}{c}$$ gets a real bar drawn on TFT.
// Other LaTeX constructs are displayed as readable compact inline notation.
inline bool parseFraction(String value, String& numerator, String& denominator) {
  value.trim();
  if (value.startsWith("$$") && value.endsWith("$$") && value.length() >= 4)
    value = value.substring(2, value.length()-2);
  else if (value.startsWith("\\[") && value.endsWith("\\]") && value.length() >= 4)
    value = value.substring(2, value.length()-2);
  value.trim();
  if (!value.startsWith("\\frac{")) return false;
  int e1 = value.indexOf('}', 6);
  if (e1 <= 6 || e1+1 >= (int)value.length() || value[e1+1] != '{') return false;
  int e2 = value.indexOf('}', e1+2);
  if (e2 < 0 || e2 != (int)value.length()-1) return false;
  numerator = replaceMath(value.substring(6, e1));
  denominator = replaceMath(value.substring(e1+2, e2));
  return numerator.length() && denominator.length();
}

inline void pushRow(const String& s) { if (rows.size() < 256) rows.push_back(s); }
inline void addWord(lgfx::LGFXBase& d, String& line, const String& word, bool spaceBefore, int maxW) {
  if (word.isEmpty()) return;
  String candidate=line;
  if (spaceBefore && line.length()) candidate += " ";
  candidate += word;
  if (d.textWidth(candidate.c_str()) <= maxW) {line=candidate;return;}
  if (line.length()) {pushRow(line);line="";}
  for (size_t i=0;i<word.length();) {
    const size_t n=nextUtf8(word,i);
    String fragment=word.substring(i,n);
    if (line.length() && d.textWidth((line+fragment).c_str()) > maxW) {pushRow(line);line="";}
    line+=fragment;
    i=n;
  }
}

inline void addWrapped(const String& in) {
  auto& d=M5.Display;
  String s=replaceMath(in), word, line;
  bool hasSpace=false;
  for (size_t i=0;i<s.length() && rows.size()<255;) {
    size_t n=nextUtf8(s,i);
    char c=s[i];
    if(c=='\n' || c=='\r' || c==' ' || c=='\t') {
      if(word.length()){addWord(d,line,word,hasSpace,d.width()-21);word="";}
      if(c=='\n'){pushRow(line);line="";hasSpace=false;}
      else hasSpace=true;
    } else word+=s.substring(i,n);
    i=n;
  }
  if(word.length()) addWord(d,line,word,hasSpace,d.width()-21);
  if(line.length() || rows.empty()) pushRow(line);
}

inline void reflow(const String& text) {
  auto& d=M5.Display;
  d.setFont(&fonts::efontJA_14);d.setTextSize(1);
  rows.clear();
  size_t cut = std::min(text.length(), MAX_RESPONSE_BYTES);
  while (cut>0 && cut<text.length() && ((uint8_t)text[cut]&0xC0)==0x80) --cut;
  String full = text.substring(0,cut);
  if(cut<text.length()) full += "...";
  // Accept common multi-line Markdown display-math delimiters.
  full.replace("$$\n", "$$");
  full.replace("\n$$", "$$");
  full.replace("\\[\n", "\\[");
  full.replace("\n\\]", "\\]");
  // A line may contain an entire $$...$$ math block, including a fraction.
  for (size_t start=0; start<full.length() && rows.size()<255;) {
    int e=full.indexOf('\n',start);
    if(e<0)e=full.length();
    String piece=full.substring(start,e),num,den;
    if(parseFraction(piece,num,den)) {
      pushRow(String("\x01")+"N"+num);
      pushRow(String("\x01")+"F");
      pushRow(String("\x01")+"D"+den);
    } else addWrapped(piece);
    start=e+1;
  }
  if(rows.empty()) pushRow("");
  rowStart=0; lastAuto=millis();dirty=true;
}

inline int visibleRows() {
  return std::max(1,(int)(M5.Display.height()-HEADER_H-FOOTER_H-8)/LINE_HEIGHT);
}
// NEXUS_R887_MENU_SECTIONS: 0=main, 1=experimental, 2=diagnostics.
// The original four settings retain their external numeric IDs: 0..3.
inline int nexusMenuSection = 0;
inline bool diagnosticsPage() { return menuOpen && nexusMenuSection == 2; }
inline int nexusMenuRows() { return nexusMenuSection == 0 ? 4 : nexusMenuSection == 1 ? 3 : 1; }
inline void nexusMenuRoot(int selected = 0) {
  nexusMenuSection = 0;
  menuIndex = selected;
  dirty = true;
}

inline void adjustScroll(int steps) {
  if (menuOpen) {
    const int count = nexusMenuRows();
    menuIndex = ((menuIndex + steps) % count + count) % count;
  } else {
    const int maxRow = std::max(0, (int)rows.size() - visibleRows());
    const int next = (int)rowStart + steps;
    rowStart = (size_t)std::max(0, std::min(maxRow, next));
  }
  dirty = true; lastManual = millis(); lastAuto = millis();
}
inline bool onGyro(float gx,float gy,float gz) {
  // Body-relative angular velocity; no accelerometer or gravity vector is used.
  // Thus the same hand twist scrolls when seated, lying down or on the move.
  const uint32_t now=millis();
  const float dt=lastGyro ? std::min(0.10f, (now-lastGyro)/1000.0f) : 0.f;
  lastGyro=now;
  const float values[3]={gx,gy,gz};
  const float v=values[NEXUS_R8_SCROLL_GYRO_AXIS];
  if(dt<=0.f || !isfinite(v))return false;
  if(fabsf(v)<35.f){gyroAccum*=0.85f;return false;}
  gyroAccum += v*dt;
  if(fabsf(gyroAccum)<32.f)return false;
  int delta=gyroAccum>0.f?1:-1;
  adjustScroll(delta);
  gyroAccum=0.f;
  return true;
}
inline void toggleMenu() {
  if (!menuOpen) {
    menuOpen = true;
    nexusMenuRoot(0);
  } else if (nexusMenuSection != 0) {
    nexusMenuRoot(nexusMenuSection == 1 ? 2 : 3);
  } else {
    menuOpen = false;
    dirty = true;
  }
}
inline int settingIndex() {
  if (!menuOpen) return -1;
  if (nexusMenuSection == 0) {
    if (menuIndex == 0 || menuIndex == 1) return menuIndex;
    if (menuIndex == 2) { nexusMenuSection = 1; menuIndex = 0; dirty = true; return -1; }
    if (menuIndex == 3) { nexusMenuSection = 2; menuIndex = 0; dirty = true; return -1; }
  } else if (nexusMenuSection == 1) {
    if (menuIndex == 0 || menuIndex == 1) return menuIndex + 2;
    nexusMenuRoot(2);
    return -1;
  } else if (nexusMenuSection == 2) {
    nexusMenuRoot(3);
    return -1;
  }
  return -1;
}
inline void closeMenu() {
  menuOpen = false;
  nexusMenuRoot(0);
}

inline void drawHeader(const String& state,bool online) {
  auto& d=M5.Display;int w=d.width();
  d.fillRect(0,0,w,HEADER_H,BG);
  d.setFont(&fonts::Font0);d.setTextSize(1);d.setTextColor(TEXT,BG);
  d.drawString(menuOpen?(nexusMenuSection==2?"NEXUS / DIAG":nexusMenuSection==1?"NEXUS / EXP":"NEXUS / SET"):"NEXUS AI",6,7);
  const bool err=state.indexOf("ERROR")>=0 || state.indexOf("failed")>=0 || state.indexOf("HTTP")>=0;
  const char* label = !online?"OFF":err?"ERR":state.indexOf("RECORD")>=0?"REC":state.indexOf("THINK")>=0?"WAIT":"ON";
  d.setTextColor(err?WARNING:online?ACCENT:MUTED,BG);
  d.drawString(label,w-d.textWidth(label)-7,7);
  d.drawFastHLine(5,HEADER_H-1,w-10,BORDER);
}
inline void drawFooter(){
  auto& d=M5.Display;const int y=d.height()-FOOTER_H;
  d.fillRect(0,y,d.width(),FOOTER_H,BG);
  d.drawFastHLine(5,y,d.width()-10,BORDER);
  d.setFont(&fonts::Font0);d.setTextSize(1);d.setTextColor(MUTED,BG);
  d.drawString(menuOpen?"A OK   B BACK":"A VOICE   B MENU",6,y+5);
}
inline void drawMenu() {
  auto& d=M5.Display;
  d.fillRect(0,HEADER_H,d.width(),d.height()-HEADER_H-FOOTER_H,BG);
  d.setTextSize(1);

  if (nexusMenuSection == 2) {
    // Diagnostics stay in this page. Nothing is drawn over normal answers.
    d.setFont(&fonts::efontJA_14);
    d.setTextColor(ACCENT,BG);
    d.drawString("Диагностика",8,HEADER_H+7);
    d.setFont(&fonts::Font0);
    d.setTextColor(TEXT,BG);
    d.setCursor(8,HEADER_H+35);
    d.printf("IMU: %s",M5.Imu.isEnabled()?"ON":"OFF");
    d.setCursor(8,HEADER_H+57);
    d.printf("WiFi: %s",WiFi.status()==WL_CONNECTED?"ON":"OFF");
    d.setCursor(8,HEADER_H+79);
    const int level=M5.Power.getBatteryLevel();
    if(level<0)d.print("BAT: --");
    else d.printf("BAT: %d%%",level>100?100:level);
    d.setTextColor(MUTED,BG);
    d.drawString("A / B: BACK",8,HEADER_H+109);
    // The R88.6 gyro monitor draws its own GX/GY/GZ in the space below.
    return;
  }

  const bool root=(nexusMenuSection==0);
  const char* mainNames[4] = {"Озвучивание","Локальное STT","Эксперименты","Диагностика"};
  const char* expNames[3] = {"Потоковый голос","Автопрокрутка","Назад"};
  const char** names = root ? mainNames : expNames;
  const int count = nexusMenuRows();
  d.setFont(&fonts::efontJA_14);
  for(int i=0;i<count;i++) {
    const int y=HEADER_H+7+i*46;
    if(i==menuIndex)d.fillRoundRect(3,y-3,d.width()-6,42,6,0x192C);
    d.setTextColor(i==menuIndex?ACCENT:TEXT,BG);
    d.drawString(names[i],9,y);
    const bool isSetting=(root && i<2) || (!root && i<2);
    if(isSetting) {
      const bool enabled=root ? (i==0?nexusTtsEnabled:nexusLocalSttEnabled)
                              : (i==0?nexusStreamingEnabled:nexusAutoScroll);
      d.setTextColor(enabled?ACCENT:MUTED,BG);
      d.drawString(enabled?"ВКЛ":"ВЫКЛ",9,y+19);
    } else {
      d.setFont(&fonts::Font0);
      d.setTextColor(MUTED,BG);
      d.drawString(i==2 && !root?"BACK":"OPEN >",9,y+19);
      d.setFont(&fonts::efontJA_14);
    }
  }
}
inline void drawBody(){
  auto& d=M5.Display;
  const int top=HEADER_H, h=d.height()-HEADER_H-FOOTER_H;
  d.fillRect(0,top,d.width(),h,BG);
  d.setFont(&fonts::efontJA_14);d.setTextSize(1);d.setTextColor(TEXT,BG);
  const int count=visibleRows();
  for(int i=0;i<count && rowStart+(size_t)i<rows.size();++i) {
    String s=rows[rowStart+i];
    const int y=top+6+i*LINE_HEIGHT;
    if(s.length()>1 && s[0]=='\x01') {
      if(s[1]=='F') {
        d.drawFastHLine(15,y+8,d.width()-30,ACCENT);
      } else {
        s=s.substring(2);
        d.drawString(s.c_str(),std::max(6L,(d.width()-d.textWidth(s.c_str()))/2),y);
      }
    } else d.drawString(s.c_str(),6,y);
  }
  if(rows.size()>(size_t)count){
    const int railY=top+5, railH=h-11;
    int maxRow=std::max(1,(int)rows.size()-count);
    int thumbH=std::max(8,(railH*count)/(int)rows.size());
    int pos=railY+(railH-thumbH)*(int)rowStart/maxRow;
    d.drawFastVLine(d.width()-4,railY,railH,BORDER);
    d.drawFastVLine(d.width()-4,pos,thumbH,ACCENT);
  }
}
inline void render(){
  const uint32_t now=millis();
  const bool online=WiFi.status()==WL_CONNECTED;
  const uint8_t prefs=(nexusTtsEnabled?1:0)|(nexusLocalSttEnabled?2:0)|(nexusStreamingEnabled?4:0)|(nexusAutoScroll?8:0);
  const bool textChanged=currentText!=cachedText;
  if(textChanged){reflow(currentText);cachedText=currentText;}
  if(!menuOpen && nexusAutoScroll && rows.size()>(size_t)visibleRows() &&
     now-lastManual>MANUAL_PAUSE_MS && now-lastAuto>AUTO_SCROLL_MS){
    rowStart=(rowStart+1)%(rows.size()-(size_t)visibleRows()+1);
    lastAuto=now;dirty=true;
  }
  const bool changed=dirty || cachedState!=statusMessage || cachedOnline!=online ||
      cachedApproval!=approvalPending || cachedDialog!=dialogMode ||
      cachedMenu!=menuOpen || cachedMenuIndex!=menuIndex || cachedSettings!=prefs;
  if(!changed)return;
  auto& d=M5.Display; d.startWrite();d.setTextWrap(false);
  if(dirty)d.fillScreen(BG);
  drawHeader(statusMessage,online);
  if(menuOpen)drawMenu();else drawBody();
  drawFooter();d.endWrite();
  cachedState=statusMessage;cachedOnline=online;
  cachedApproval=approvalPending;cachedDialog=dialogMode;
  cachedMenu=menuOpen;cachedMenuIndex=menuIndex;cachedSettings=prefs;
  dirty=false;
}
} // namespace nexus_ui
