#!/bin/bash

echo "========================================="
echo "NEET App Diagnostics"
echo "========================================="
echo ""

echo "1. Checking processes..."
ps aux | grep -E "streamlit|ngrok" | grep -v grep
echo ""

echo "2. Checking port 8501..."
netstat -tlnp 2>/dev/null | grep 8501 || ss -tlnp | grep 8501
echo ""

echo "3. Testing local Streamlit..."
curl -I http://localhost:8501 2>&1 | head -5
echo ""

echo "4. Getting ngrok URL..."
curl -s http://localhost:4040/api/tunnels | python3 -c "import sys, json; data=json.load(sys.stdin); print('URL:', data['tunnels'][0]['public_url'])" 2>/dev/null
echo ""

echo "5. Testing ngrok tunnel from inside..."
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | python3 -c "import sys, json; data=json.load(sys.stdin); print(data['tunnels'][0]['public_url'])" 2>/dev/null)
echo "Testing: $NGROK_URL"
curl -I "$NGROK_URL" 2>&1 | head -10
echo ""

echo "6. Checking Streamlit configuration..."
ps aux | grep streamlit | grep -v grep | grep -oE "\-\-server\.[^ ]*"
echo ""

echo "7. Checking ngrok status..."
curl -s http://localhost:4040/api/tunnels | python3 -m json.tool | grep -E '"public_url"|"addr"|"proto"'
echo ""

echo "========================================="
