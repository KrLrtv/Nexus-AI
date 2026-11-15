"""Bounded Gemini function-call orchestrator; no arbitrary shell or URL actions."""
import os
import time
import json
from ai_provider import post_json, ProviderError, ask as fallback_ask, SYSTEM
from learning20 import STORE as _learning20_store
import native_learning  # NEXUS_NATIVE_PLANNER_IMPORT
from agent import execute, save_dialog, recent_dialog, REQUIRES_APPROVAL
import source_learning
SYSTEM = SYSTEM + source_learning.PLANNER_HINT


# NEXUS_R89_SPEED
from pathlib import Path as _nexus_r89_Path

def nexus_r89_fast():
    try:
        return (_nexus_r89_Path.home() / '.config/nexus/speed_mode').read_text().strip() == 'fast'
    except OSError:
        return False

TOOL_DESCRIPTIONS = {
    # NEXUS_NATIVE_TOOL: arguments cannot replace the owner's task.
    'learn_capability': 'Освоить недостающий способ и продолжить исходную задачу. Только если существующих инструментов недостаточно. Не для ошибок доступа, сети или авторизации. argument пустой: исходный запрос берётся приложением.',
    'graph_add': 'Add an explicitly supplied fact/project/device to graph; argument JSON {"kind":"project|device|person|fact|topic","label":"...","detail":"..."}. Do not invent private facts.',
    'graph_search': 'Find connected stored knowledge; argument query',
    'goal_add': 'Create a user-requested long term goal; argument title',
    'goal_list': 'List goals and steps; argument empty',
    'task_add': 'Add task to goal; argument JSON {"goal_id":1,"title":"..."}',
    'task_done': 'Mark task completed ONLY when user explicitly says it is done; argument task ID',
    'research_scan': 'Fetch new article headlines from a fixed trusted RSS feed; argument technology,science,world. Never assert that a headline was verified.',
    'notification_digest': 'Summarize locally visible notification titles only, after Android grants access; argument empty',
    'evolution_draft': 'When existing tools cannot solve request, generate a Python source proposal ONLY (never executed). argument task description',

    'extension_request': 'Когда пользователь явно просит создать новую функцию или не хватает инструмента, предложить безопасный модуль из имеющихся функций; argument = описание требуемой возможности. Новый модуль НЕ будет включён без подтверждения владельца в Android.',
    'battery': 'Получить заряд телефона',
    'memory_save': 'Запомнить явно сообщённый пользователем факт или предпочтение; argument = содержимое записи',
    'memory_search': 'Найти свои локальные воспоминания по ключевым словам; argument = поисковые слова',
    'memory_delete': 'Удалить локальное воспоминание по ID. Только по явно выраженному запросу пользователя',
    'reminder_timed_list': 'Показать назначенные напоминания с датой и временем',
    'reminder_timed_add': 'Создать напоминание по времени; argument = JSON-строка {"text":"...","when":"2026-11-12T09:00:00+03:00"}. Если дата или часовой пояс неизвестны, СПРОСИ пользователя, НЕ УГАДЫВАЙ. Напоминание работает только пока Termux запущен',
    'reminder_timed_cancel': 'Удалить назначенное напоминание по ID только по явной команде пользователя',
    'notes_list': 'Показать сохранённые заметки',
    'note_add': 'Сохранить заметку по тексту',
    'reminders_list': 'Показать список напоминаний',
    'reminder_add': 'Добавить пункт в список напоминаний; не назначает время',
    'torch_on': 'Включить фонарик по запросу пользователя',
    'torch_off': 'Выключить фонарик по запросу пользователя',
    'notify': 'Показать уведомление по запросу пользователя',
    'speak': 'Произнести текст через Android TTS по запросу пользователя',
    'learn_public_source': 'Самостоятельно найти и изучить публичные источники, собрать проверяемый сценарий чтения и ответить. Для актуальных сведений, публичных API, документов, лент; также для вопросов без повелительного глагола. argument пустой; выполняется исходный запрос пользователя.',
}
READ_ONLY={'graph_search','goal_list','notification_digest','battery','notes_list','reminders_list','memory_search','reminder_timed_list'}
MAX_CALLS=4
FUNCTIONS=[{
    'name': k,
    'description': description,
    'parameters': {'type':'OBJECT','properties':{'argument':{'type':'STRING','description':'Только текст заметки, напоминания или уведомления. Для других функций пустая строка.'}}, 'required':['argument']}
} for k,description in TOOL_DESCRIPTIONS.items()]

def plan(prompt, history, voice_mode=False):
    """Gemini selects bounded tools. All side effects go through a pending approval gate."""
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key:
        answer,provider=fallback_ask(prompt,history)
        return {'reply':answer,'provider':provider,'actions':[],'pending':[]}
    model=os.getenv('GEMINI_MODEL','gemini-2.5-flash-lite')
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    contents=[{'role':'model' if role=='assistant' else 'user','parts':[{'text':body[:1000]}]} for role,body in history[-6:]]
    contents.append({'role':'user','parts':[{'text':prompt[:3000]}]})  # NEXUS_R86_PTT_10SEC
    actions=[];pending=[]
    voice_started=time.monotonic()
    nexus_r89_budget = 9 if nexus_r89_fast() and voice_mode else 24
    for _ in range(MAX_CALLS+1):
        if voice_mode and time.monotonic()-voice_started>nexus_r89_budget:
            raise ProviderError('Голосовой запрос превысил лимит времени; попробуй ещё раз')
        data=post_json(url,{'systemInstruction':{'parts':[{'text':SYSTEM+_learning20_store.safe_context_for(prompt)+native_learning.PLANNER_HINT+'' # NEXUS_NATIVE_HINT
                        +' Используй инструменты только при необходимости. Не заявляй об успешном действии без ответа инструмента. Не запрашивай голосовые коды или подтверждение кнопкой B. Не утверждай что действие выполнено до результата инструмента.'}]},'contents':contents,'tools':[{'functionDeclarations':FUNCTIONS}],'generationConfig':{'maxOutputTokens':(160 if nexus_r89_fast() and voice_mode else 350)}}, {'x-goog-api-key':key},timeout=(min(20,max(2,nexus_r89_budget-(time.monotonic()-voice_started))) if voice_mode else (7 if nexus_r89_fast() else 35)))
        try: parts=data['candidates'][0]['content']['parts']
        except (KeyError,IndexError,TypeError):raise ProviderError('Gemini вернул ответ без содержимого')
        calls=[p['functionCall'] for p in parts if 'functionCall' in p]
        if not calls:
            reply=''.join(p.get('text','') for p in parts).strip() or 'Готово.'
            # NEXUS_NATIVE_GAP: only an explicit capability gap, not a provider/access failure.
            return native_learning.after_answer(prompt,
                {'reply':reply,'provider':'Gemini','actions':actions,'pending':pending})
        # Never execute more than MAX_CALLS requested actions.
        if len(actions)+len(pending)>=MAX_CALLS:
            return {'reply':'Лимит шагов достигнут. Можно продолжить отдельным запросом.', 'provider':'Gemini','actions':actions,'pending':pending}
        # Bound tool execution to one function call per turn, per step.
        call=calls[0];name=call.get('name','');arg=str((call.get('args') or {}).get('argument',''))[:500]
        # NEXUS_PUBLIC_DISPATCH: model arguments never replace the user request.
        if name == 'learn_public_source':
            if actions or pending:
                return {'reply':'Часть действий уже выполнена. Не запускаю исходную задачу заново.',
                        'provider':'NEXUS-Sources','actions':actions,'pending':pending,
                        'learning':{'status':'partial','answered':False}}
            import source_learning
            return source_learning.learn(prompt)
        # NEXUS_NATIVE_DISPATCH: one acquisition per request, no replay of earlier actions.
        if name in ('learn_capability', 'extension_request', 'evolution_draft'):
            if actions or pending:
                return {'reply':'Часть действий уже выполнена. Не повторяю их и не запускаю '
                               'обучение для всего запроса заново.',
                        'provider':'NEXUS-Native','actions':actions,'pending':pending,
                        'learning':{'status':'partial','answered':False,'skill_id':None}}
            return native_learning.learn(prompt)
        if name not in TOOL_DESCRIPTIONS:
            result='Недопустимый инструмент'
        elif name in REQUIRES_APPROVAL and not __import__('core10').permission(name):
            from authority import request as queue_approval
            challenge=queue_approval(name,arg)
            pending.append({'name':name,'argument':arg})
            result='Ожидается голосовая команда с кодом '+challenge['code']+'. Действие НЕ выполнено.'
        elif name=='extension_request':
            from extensions import propose
            try:
                draft=propose(arg)
                result=('Создано предложение расширения #'+str(draft['id'])+'. Его нужно проверить и активировать в Android.' if draft['status']=='pending' else 'Невозможно расширить текущими ресурсами: '+draft.get('reason',''))
            except (ProviderError,ValueError) as exc:
                result='Расширение не создано: '+str(exc)[:200]
            actions.append({'name':name,'result':result[:450]})
        else:
            # NEXUS_R893_DIRECT_COMMANDS
            if name in ('memory_delete', 'reminder_timed_cancel'):
                _r893_prompt = prompt.casefold()
                _r893_explicit = __import__('re').search(r'\b(?:удали|удалить|удаление|забудь|забыть|сотри|стереть|отмени|отменить|delete|forget|cancel)\b', _r893_prompt)
                _r893_negation = __import__('re').search(r'\b(?:не|not)\s+(?:удали|удалить|удаляй|забудь|забыть|отмени|отменить|отменяй|delete|forget|cancel)\b', _r893_prompt)
                if not _r893_explicit or _r893_negation:
                    result = 'Не выполнено: удаление или отмена возможны только по явной команде.'
                else:
                    result=execute(name,arg)
            else:
                result=execute(name,arg)
            actions.append({'name':name,'result':result[:450]})
        # NEXUS_R891_THOUGHT_SIGNATURE_FIX
        # Возвращаем оригинальные model parts со всеми thoughtSignature и порядком частей.
        contents.append(data['candidates'][0]['content'])
        # Один инструмент исполняем, остальные из параллельного вызова явно пропускаем.
        _nexus_r891_responses = [{'functionResponse': {'name': name, 'response': {'result': result}}}]
        for _nexus_r891_unused in calls[1:]:
            _nexus_r891_unused_name = _nexus_r891_unused.get('name', '')
            if _nexus_r891_unused_name:
                _nexus_r891_responses.append({'functionResponse': {'name': _nexus_r891_unused_name,
                    'response': {'result': 'Не выполнено: NEXUS выполняет только один инструмент за шаг.'}}})
        contents.append({'role': 'user', 'parts': _nexus_r891_responses})
        if pending:
            return {'reply':'Подтверди на устройстве: РАЗРЕШАЮ '+challenge['code'] + ('. Затем удерживай B три секунды.' if challenge['level']=='voice_button' else '.'),'provider':'Gemini','actions':actions,'pending':pending}
    return {'reply':'Достигнут лимит шагов. Выполненные действия перечислены в журнале.','provider':'Gemini','actions':actions,'pending':pending}
