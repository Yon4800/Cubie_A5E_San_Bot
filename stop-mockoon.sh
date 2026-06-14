#!/bin/bash
echo "Stopping local Mockoon CLI on port 4444..."

# Find the PID using port 4444
PID=$(netstat -ano 2>/dev/null | grep :4444 | grep LISTENING | awk '{print $5}')
PID=$(echo "$PID" | tr -d '\r')

if [ -n "$PID" ]; then
  echo "Killing process $PID..."
  taskkill.exe /F /PID "$PID" 2>/dev/null || kill -9 "$PID" 2>/dev/null
  echo "Mockoon CLI stopped."
else
  echo "No process running on port 4444."
fi
