#!/bin/bash

# Pastikan berada di folder project
cd "$(dirname "$0")"

echo "====================================="
echo "MAMULAI BILLING INTERNET & WA GATEWAY"
echo "====================================="

# Hentikan proses lama jika ada
echo "[1/3] Membersihkan port yang mungkin masih nyangkut..."
pkill -f "node server.js" || true
pkill -f "python app.py" || true

# Jalankan WA Gateway di background
echo "[2/3] Menjalankan WA Gateway (Node.js)..."
cd wa-gateway
npm install > /dev/null 2>&1
node server.js &
WA_PID=$!
cd ..

# Jalankan Flask App
echo "[3/3] Menjalankan Aplikasi Web (Flask)..."
if [ ! -d "venv" ]; then
    echo "Virtual environment tidak ditemukan, membuat baru..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Set dummy secret key untuk lokal, atau gunakan yang sudah ada
export SECRET_KEY=${SECRET_KEY:-"kunci-rahasia-lokal-wsl-123"}
export FLASK_ENV=development

echo "Aplikasi siap diakses! Tekan Ctrl+C untuk mematikan semuanya."
python app.py

# Jika Flask dimatikan dengan Ctrl+C, matikan juga WA Gateway
kill $WA_PID
echo "Semua layanan telah dimatikan."
