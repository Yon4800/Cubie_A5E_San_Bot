#!/bin/bash
echo "Starting local Mockoon CLI on port 4444..."

# Check if port 4444 is already in use
PID=$(netstat -ano 2>/dev/null | grep :4444 | grep LISTENING | awk '{print $5}')
PID=$(echo "$PID" | tr -d '\r')

if [ -n "$PID" ]; then
  echo "Warning: Port 4444 is already in use by process $PID."
  echo "Stopping the existing process..."
  taskkill.exe /F /PID "$PID" 2>/dev/null || kill -9 "$PID" 2>/dev/null
  sleep 1
fi

npx -y @mockoon/cli start --data mockoon-environment.json --port 4444
