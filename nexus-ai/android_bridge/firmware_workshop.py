"""NEXUS v2 experimental firmware workshop.

An AI suggests bounded, exact string edits to a public firmware source file.
No model-generated command is run. Review/apply is user-initiated; USB flashing
is the separate existing M5 menu action with its own explicit confirmation.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from ai_provider import post_json, ProviderError

HOME=Path.home()
FIRMWARE=HOME/"nexus-firmware"/"firmware"
WORKSHOP=HOME/".config"/"nexus"/"firmware-workshop"
REQUEST=WORKSHOP/"request.json"
DRAFT=WORKSHOP/"draft.json"
BACKUP=WORKSHOP/"last-source-backup.json"
ALLOWED={
    "Интерфейс и экран": "src/NexusMinimalUI.h",
    "Кнопки, голос и логика": "src/main.cpp",
    "Меню настроек M5": "src/NexusR88Settings.h",
}
MAX_SOURCE=50000
MAX_CHANGE=12000
MAX_EDITS=5

def _safe_dir():
    if WORKSHOP.is_symlink():
        raise ValueError("Каталог мастерской прошивки является символической ссылкой.")
    WORKSHOP.mkdir(parents=True,exist_ok=True,mode=0o700)
    WORKSHOP.chmod(0o700)

def _write_json(path,obj):
    _safe_dir()
    if path.is_symlink():
        raise ValueError("Конфигурация мастерской является ссылкой.")
    fd,name=tempfile.mkstemp(dir=WORKSHOP,prefix=".workshop-",suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(obj,f,ensure_ascii=False,indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name,0o600)
        os.replace(name,path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def _read_json(path):
    if path.is_symlink():
        raise ValueError("Файл мастерской является ссылкой.")
    if not path.is_file():
        return None
    if path.stat().st_size>150000:
        raise ValueError("Слишком большой файл мастерской.")
    return json.loads(path.read_text(encoding="utf-8"))

def _sha(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def _source(label):
    if label not in ALLOWED:
        raise ValueError("Файл прошивки не разрешён для редактирования.")
    path=FIRMWARE / ALLOWED[label]
    if path.is_symlink() or not path.is_file():
        raise ValueError("Нет подходящего исходного файла прошивки.")
    data=path.read_text(encoding="utf-8")
    if not data or len(data)>MAX_SOURCE:
        raise ValueError("Размер исходника не поддерживается мастерской.")
    return path,data

def request_change(description):
    """Queue a spoken request; voice never edits the firmware directly."""
    value=" ".join(str(description).split())
    if not 8<=len(value)<=500:
        raise ValueError("Опиши изменение прошивки одним предложением (8–500 символов).")
    _write_json(REQUEST,{"description":value,"at":int(time.time())})
    return "Задача изменения прошивки сохранена. Открой в Termux раздел M5 → Мастерская кода."

def request_status():
    item=_read_json(REQUEST)
    draft=_read_json(DRAFT)
    return {"request":item["description"] if item else "",
            "draft":bool(draft),
            "file":draft["label"] if draft else "",
            "backup":BACKUP.is_file()}

def propose(description=None,label="Интерфейс и экран"):
    """Gemini returns exact find/replace edits, never a shell script or patch."""
    if description is None:
        item=_read_json(REQUEST)
        if not item:
            raise ValueError("Сначала опиши изменение или попроси NEXUS голосом.")
        description=item["description"]
    description=" ".join(str(description).split())
    if not 8<=len(description)<=500:
        raise ValueError("Опиши изменение прошивки одним предложением.")
    path,before=_source(label)
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key:
        raise ValueError("Добавь Gemini API-ключ через настройки NEXUS.")
    model=os.environ.get("GEMINI_MODEL","gemini-flash-lite-latest")
    if not re.fullmatch(r"[a-zA-Z0-9._-]{2,100}",model):
        raise ValueError("Некорректное имя модели.")
    instruction=(
        "You are editing an Arduino ESP32 M5StickC Plus2 firmware file. "
        "Return ONLY strict JSON: {\"edits\":[{\"find\":\"EXACT EXISTING BLOCK\","
        "\"replace\":\"UPDATED BLOCK\"}],\"summary\":\"short Russian explanation\"}. "
        "Use 1 to 5 minimal, exact, non-overlapping edits. The find string MUST "
        "occur exactly once in the existing source. Preserve all unrelated code. "
        "Do not include code fences, comments outside the JSON, shell commands, "
        "network exfiltration, credential handling, bootloader/partition changes, "
        "or disabling authentication. Never read or change local Wi-Fi/token headers. "
        "If request cannot be safely expressed as simple replacement, return edits=[]."
    )
    # This source is shipped publicly. config_local.h and user files are never read.
    payload={
        "systemInstruction":{"parts":[{"text":instruction}]},
        "contents":[{"role":"user","parts":[{"text":
            "ЗАДАЧА ВЛАДЕЛЬЦА: "+description+"\nФАЙЛ: "+str(path.relative_to(FIRMWARE))+
            "\nИСХОДНЫЙ КОД:\n"+before}]}],
        "generationConfig":{"responseMimeType":"application/json",
                            "maxOutputTokens":4500,"temperature":0.15},
    }
    data=post_json(
        "https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent",
        payload,{"x-goog-api-key":key},timeout=45)
    try:
        result=json.loads("".join(
            part.get("text","") for part in data["candidates"][0]["content"]["parts"]))
    except (ValueError,TypeError,KeyError,IndexError) as exc:
        raise ValueError("Gemini вернул невалидное предложение изменения кода.") from exc
    edits=result.get("edits")
    if not isinstance(edits,list) or not (1<=len(edits)<=MAX_EDITS):
        raise ValueError("Gemini не предложил ограниченное и применимое изменение.")
    after=apply_edits(before,edits)
    if after==before:
        raise ValueError("Код не изменился.")
    diff=list(difflib.unified_diff(before.splitlines(keepends=True),
                                   after.splitlines(keepends=True),
                                   fromfile="старая прошивка/"+path.name,
                                   tofile="предложение/"+path.name))
    diff_text="".join(diff)
    if len(diff_text)>MAX_CHANGE:
        raise ValueError("Слишком большое изменение. Попроси более узкую доработку.")
    _write_json(DRAFT,{"description":description,"label":label,
                       "sha256_before":_sha(before),"sha256_after":_sha(after),
                       "edits":edits,"summary":str(result.get("summary",""))[:300],
                       "diff":diff_text,"created":int(time.time())})
    return "Черновик создан для "+label+". Открой просмотр изменений перед применением."

def apply_edits(before,edits):
    """Deterministic exact replacements; a draft never edits code by itself."""
    if not isinstance(edits,list) or not 1<=len(edits)<=MAX_EDITS:
        raise ValueError("Неожиданное количество изменений.")
    original=before
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={"find","replace"}:
            raise ValueError("Недопустимый формат предложения.")
        old,new=edit["find"],edit["replace"]
        if not isinstance(old,str) or not isinstance(new,str):
            raise ValueError("Некорректное содержимое изменения.")
        if not 8<=len(old)<=9000 or len(new)>10000:
            raise ValueError("Превышен лимит одного изменения.")
        if original.count(old)!=1:
            raise ValueError("Фрагмент для замены должен точно один раз встречаться в исходнике.")
        if old==new:
            raise ValueError("Пустое изменение.")
        # Guard security-critical definitions in our already installed firmware.
        guarded=("NEXUS_DEVICE_TOKEN","NEXUS_WIFI_PASSWORD",
                 "NEXUS_WIFI_SSID","config_local.h","config_generated.h",
                 "updateBridgeAddress","NEXUS_BRIDGE_URL","DEVICE_TOKEN",
                 "WiFi.begin","partitions.csv","esp_ota_")
        if any(k in old or k in new for k in guarded):
            raise ValueError("Предложение затрагивает защищённую конфигурацию или авторизацию.")
        before=before.replace(old,new,1)
    if len(before)>MAX_SOURCE+MAX_CHANGE:
        raise ValueError("Новая версия исходника слишком большая.")
    return before

def review():
    item=_read_json(DRAFT)
    if not item:
        return "Черновика пока нет."
    path,before=_source(item["label"])
    if _sha(before)!=item["sha256_before"]:
        return "Исходный код изменился после создания черновика. Нужно создать предложение заново."
    after=apply_edits(before,item["edits"])
    if _sha(after)!=item["sha256_after"]:
        raise ValueError("Контрольная сумма предложения не совпадает.")
    return ("ЗАДАЧА: "+item["description"]+"\nРЕЗЮМЕ: "+item["summary"]+
            "\n\n"+item["diff"])

def apply():
    """Only call after explicit local review and menu confirmation."""
    item=_read_json(DRAFT)
    if not item:
        raise ValueError("Черновик не создан.")
    path,before=_source(item["label"])
    if _sha(before)!=item["sha256_before"]:
        raise ValueError("Исходник изменён. Предложение устарело.")
    after=apply_edits(before,item["edits"])
    if _sha(after)!=item["sha256_after"]:
        raise ValueError("Контрольная сумма предложения не совпадает.")
    _write_json(BACKUP,{"label":item["label"],"before":before,
                        "after_hash":_sha(after),"at":int(time.time())})
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".nexus-code-",suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(after)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    DRAFT.unlink(missing_ok=True)
    return "Изменение применено к исходнику. Далее собери прошивку в меню. На M5 ничего не записано."

def revert():
    """Restore only if live source still matches the exact workshop output."""
    item=_read_json(BACKUP)
    if not item:
        raise ValueError("Нет резервной копии исходника мастерской.")
    path,now=_source(item["label"])
    if _sha(now)!=item["after_hash"]:
        raise ValueError("Исходник изменился. Автооткат остановлен, чтобы не потерять новые правки.")
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".nexus-revert-",suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(item["before"])
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    BACKUP.unlink(missing_ok=True)
    return "Предыдущий код восстановлен. На M5 ничего не записано."
