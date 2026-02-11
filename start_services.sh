#!/bin/bash
# Start services independent of Claude Code

cd /home/ubuntu/apps/test-generator-from-paragraph

# Kill any existing instances first
pkill -f "streamlit run app.py" 2>/dev/null
pkill ngrok 2>/dev/null
sleep 2

# Start Streamlit with ngrok-compatible settings
streamlit run app.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  --server.baseUrlPath "" \
  --browser.serverAddress "0.0.0.0" &

echo "Streamlit started, waiting for it to be ready..."
sleep 5

# Start ngrok
ngrok http 8501 &

echo "ngrok started, waiting for tunnel..."
sleep 3

# Display the ngrok URL
echo ""
echo "========================================="
echo "Services started successfully!"
echo "========================================="
echo ""
curl -s http://localhost:4040/api/tunnels | python3 -c "import sys, json; data=json.load(sys.stdin); print('Public URL:', data['tunnels'][0]['public_url'])" 2>/dev/null
echo ""
echo "Local URL: http://localhost:8501"
echo ""
echo "To stop services:"
echo "  pkill -f 'streamlit run app.py'"
echo "  pkill ngrok"
echo ""
