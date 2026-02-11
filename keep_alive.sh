#!/bin/bash

# Keep Streamlit and ngrok running
while true; do
    # Check and restart Streamlit
    if ! pgrep -f "streamlit run app.py" > /dev/null; then
        echo "[$(date)] Starting Streamlit..."
        cd /home/ubuntu/apps/test-generator-from-paragraph
        streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false > /tmp/streamlit.log 2>&1 &
    fi

    # Check and restart ngrok
    if ! pgrep -f "ngrok http" > /dev/null; then
        echo "[$(date)] Starting ngrok..."
        ngrok http 8501 --log=stdout > /tmp/ngrok.log 2>&1 &
        sleep 3
        # Get and display the URL
        curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print('ngrok URL:', data['tunnels'][0]['public_url'])" 2>/dev/null || echo "ngrok starting..."
    fi

    sleep 10
done
