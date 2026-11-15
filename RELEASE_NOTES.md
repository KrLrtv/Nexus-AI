# NEXUS AI v0.1.2-alpha

Исправление установщика для чистого Termux F-Droid и Ubuntu PRoot на Android.

## Исправлено
- `tpio setup` ранее вызывал `apt-get` внутри Ubuntu с `--no-link2symlink`, что приводило к `dpkg: unable to make backup link` и `Permission denied` при обновлении `perl-base`.
- При установке NEXUS теперь исправляется только вспомогательный `_proot_run()` в локальном Termux-PlatformIO: пакетный менеджер Ubuntu запускается со штатной эмуляцией hard links. Компиляция PlatformIO не изменена.
- Сохранены исправления v0.1.1-alpha: автоматическая установка Python перед проверкой архива и загрузка ZIP через codeload.github.com.

## Возможности
- M5StickC Plus2 firmware, Android Termux bridge, NEXUS Control Center, PTT, локальное STT sherpa-onnx, eSpeak и PlatformIO.

## Ограничения
- Проверки GitHub Actions проверяют синтаксис и комплектность исходников, но не полноценную установку на Android.
- Требуется доступ к доменам GitHub и серверам моделей, а также свободная память телефона.
- Не прошивает M5 автоматически, секреты вводятся локально.
- Это экспериментальная alpha-версия.
