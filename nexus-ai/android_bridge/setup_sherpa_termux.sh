#!/data/data/com.termux/files/usr/bin/bash
# Install sherpa-onnx inside the Ubuntu PRoot already used by PlatformIO.
# Leaves the existing Termux Python 3.13 and NEXUS server untouched.
set -euo pipefail
command -v proot-distro >/dev/null || { echo 'Сначала установи: pkg install proot-distro'; exit 1; }
echo '== Установка локального русского распознавания в Ubuntu =='
proot-distro login ubuntu -- bash -c '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip curl ca-certificates bzip2
mkdir -p /opt/nexus-stt/models
if [ ! -x /opt/nexus-stt/venv/bin/python ]; then python3 -m venv /opt/nexus-stt/venv; fi
/opt/nexus-stt/venv/bin/python -m pip install --disable-pip-version-check "sherpa-onnx==2.0.1" "sherpa-onnx-core==2.0.1" "sherpa-onnx-bin==2.0.1" "numpy<3"
/opt/nexus-stt/venv/bin/python -c "import sherpa_onnx, numpy; print(\"Sherpa import OK\")"
'
proot-distro login ubuntu -- bash -c '
set -e
cd /opt/nexus-stt/models
MODEL="sherpa-onnx-small-zipformer-ru-2024-09-18"
if [ -f "$MODEL/encoder.int8.onnx" ] && [ -f "$MODEL/decoder.int8.onnx" ] && [ -f "$MODEL/joiner.int8.onnx" ] && [ -f "$MODEL/tokens.txt" ]; then
 echo "Русская модель уже скачана"; exit 0
fi
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${MODEL}.tar.bz2"
# Partial downloads may be resumed by curl. Download from the official publisher.
curl --fail --location --retry 3 --continue-at - --output "${MODEL}.tar.bz2" "$URL"
# Only extract the expected top-level model directory.
if tar -tjf "${MODEL}.tar.bz2" | grep -vE "^${MODEL}(/|$)" | grep -q .; then
  echo "Неожиданное содержимое архива модели"; exit 1
fi
tar -xjf "${MODEL}.tar.bz2"
for f in encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx tokens.txt; do test -s "$MODEL/$f" || { echo "Не хватает $f"; exit 1; }; done
printf "Русская модель установлена: %s\n" "$MODEL"
'
echo '== Готово. Следом выполни: nexus-stt start =='
