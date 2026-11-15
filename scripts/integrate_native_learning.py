"""Apply the native integration to the exact v2.0.5 sources, once.

Unknown source versions are rejected instead of partially rewriting a user's
working installation. This is a build-time source migration, not self-modifying
runtime code. It is run and tested on the dedicated GitHub feature branch.
"""
from pathlib import Path
import ast
import hashlib

ROOT = Path(__file__).resolve().parents[1]


def apply(source, expected, replacements):
    marks = [marker for old, new, marker in replacements]
    if all(mark in source for mark in marks):
        # Idempotent on the exact inserted snippets, not merely one marker.
        if not all(new in source for old, new, marker in replacements):
            raise RuntimeError('Partially modified native integration')
        return source
    raw = source.encode('utf-8')
    digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if digest != expected:
        raise RuntimeError('Unexpected source revision: ' + digest)
    result = source
    for old, new, marker in replacements:
        if result.count(old) != 1:
            raise RuntimeError('Integration anchor is missing or ambiguous: ' + marker)
        result = result.replace(old, new, 1)
    ast.parse(result)
    return result


APP = [
    ('from planner import plan\n',
     'from planner import plan\nimport native_learning as _native_learning  # NEXUS_NATIVE_IMPORT\n',
     'NEXUS_NATIVE_IMPORT'),
    ("    if not text:return {'error':'Пустой запрос'},400\n",
     "    if not text:return {'error':'Пустой запрос'},400\n"
     "    # NEXUS_NATIVE_CONTROL: local controls are never sent to the model.\n"
     "    native_control = _native_learning.control(text)\n"
     "    if native_control is not None:\n"
     "        publish(native_control['reply'], 'SETTINGS', event)\n"
     "        return native_control, 200\n",
     'NEXUS_NATIVE_CONTROL'),
    ("    # NEXUS_R85_QUICK_RESPONSE: strictly read-only local responses; never run\n",
     "    # NEXUS_NATIVE_REUSE: exact operation + fresh data, without a cloud call.\n"
     "    native_result = _native_learning.reuse(text)\n"
     "    if native_result is not None:\n"
     "        publish(native_result['reply'], 'ANSWER via NEXUS Native', event)\n"
     "        return native_result, 200\n"
     "    # NEXUS_R85_QUICK_RESPONSE: strictly read-only local responses; never run\n",
     'NEXUS_NATIVE_REUSE'),
    ("    _nexus_route='fast' if _nexus_is_fast_info(text) else 'planner'\n",
     "    # NEXUS_NATIVE_ROUTING: tasks use the capability-aware planner.\n"
     "    _nexus_route = ('fast' if _nexus_is_fast_info(text) and not\n"
     "                    _native_learning.is_task_request(text) else 'planner')\n",
     'NEXUS_NATIVE_ROUTING'),
    ("    answer=response['reply']\n    save_dialog('user',text);save_dialog('assistant',answer)\n",
     "    answer=response['reply']\n"
     "    # NEXUS_NATIVE_HISTORY: do not put native task inputs into future chat context.\n"
     "    if not response.get('learning'):\n"
     "        save_dialog('user',text);save_dialog('assistant',answer)\n",
     'NEXUS_NATIVE_HISTORY'),
    ("'pending':bool(response.get('pending'))},200\n",
     "'pending':bool(response.get('pending')),\n"
     "            'learning':response.get('learning')},200  # NEXUS_NATIVE_RESULT\n",
     'NEXUS_NATIVE_RESULT'),
    ("    text = ' '.join(transcript.casefold().split())\n",
     "    # NEXUS_NATIVE_VOICE: do not bypass task routing on legacy /voice.\n"
     "    if _native_learning.is_task_request(transcript) or _native_learning.is_control(transcript):\n"
     "        return False\n"
     "    text = ' '.join(transcript.casefold().split())\n",
     'NEXUS_NATIVE_VOICE'),
    ("        shutdown();cancel_all();publish('Остановлено пользователем','STOPPED','stop');return jsonify(ok=True)\n",
     "        _native_learning.cancel()  # NEXUS_NATIVE_DEVICE_STOP\n"
     "        shutdown();cancel_all();publish('Остановлено пользователем','STOPPED','stop');return jsonify(ok=True)\n",
     'NEXUS_NATIVE_DEVICE_STOP'),
    ("        cancel_all();publish('Остановлено','STOPPED',event);return {'reply':'Остановлено'},200\n",
     "        _native_learning.cancel()  # NEXUS_NATIVE_TEXT_STOP\n"
     "        cancel_all();publish('Остановлено','STOPPED',event);return {'reply':'Остановлено'},200\n",
     'NEXUS_NATIVE_TEXT_STOP'),
]

PLANNER = [
    ('from learning20 import STORE as _learning20_store\n',
     'from learning20 import STORE as _learning20_store\nimport native_learning  # NEXUS_NATIVE_PLANNER_IMPORT\n',
     'NEXUS_NATIVE_PLANNER_IMPORT'),
    ('TOOL_DESCRIPTIONS = {\n',
     "TOOL_DESCRIPTIONS = {\n"
     "    # NEXUS_NATIVE_TOOL: arguments cannot replace the owner's task.\n"
     "    'learn_capability': 'Освоить недостающий способ и продолжить исходную задачу. "
     "Только если существующих инструментов недостаточно. Не для ошибок доступа, сети "
     "или авторизации. argument пустой: исходный запрос берётся приложением.',\n",
     'NEXUS_NATIVE_TOOL'),
    ("SYSTEM+_learning20_store.safe_context_for(prompt)+' Используй инструменты",
     "SYSTEM+_learning20_store.safe_context_for(prompt)+native_learning.PLANNER_HINT+'' "
     "# NEXUS_NATIVE_HINT\n"
     "                        +' Используй инструменты",
     'NEXUS_NATIVE_HINT'),
    ("            return {'reply':reply,'provider':'Gemini','actions':actions,'pending':pending}\n",
     "            # NEXUS_NATIVE_GAP: only an explicit capability gap, not a provider/access failure.\n"
     "            return native_learning.after_answer(prompt,\n"
     "                {'reply':reply,'provider':'Gemini','actions':actions,'pending':pending})\n",
     'NEXUS_NATIVE_GAP'),
    ("        if name not in TOOL_DESCRIPTIONS:\n",
     "        # NEXUS_NATIVE_DISPATCH: one acquisition per request, no replay of earlier actions.\n"
     "        if name in ('learn_capability', 'extension_request', 'evolution_draft'):\n"
     "            if actions or pending:\n"
     "                return {'reply':'Часть действий уже выполнена. Не повторяю их и не запускаю '\n"
     "                               'обучение для всего запроса заново.',\n"
     "                        'provider':'NEXUS-Native','actions':actions,'pending':pending,\n"
     "                        'learning':{'status':'partial','answered':False,'skill_id':None}}\n"
     "            return native_learning.learn(prompt)\n"
     "        if name not in TOOL_DESCRIPTIONS:\n",
     'NEXUS_NATIVE_DISPATCH'),
]


def main():
    targets = [
        ('nexus-ai/android_bridge/app.py', '9356f37a4b17e7a9a51f892fd76f7db6e8858b46', APP),
        ('nexus-ai/android_bridge/planner.py', '713bf5c45475159e26645a059f93d822ca6d74d5', PLANNER),
    ]
    # Validate all files before changing either of them.
    outputs = [(ROOT / path, apply((ROOT / path).read_text(encoding='utf-8'), sha, edits))
               for path, sha, edits in targets]
    for path, source in outputs:
        path.write_text(source, encoding='utf-8')
    print('Native learning integration applied; both Python files parse successfully.')


if __name__ == '__main__':
    main()
