#!/usr/bin/env bash
# Start Appium bound to localhost only. Logs to data/logs/appium.log
cd "$(dirname "$0")/.."; mkdir -p data/logs
exec appium --address 127.0.0.1 --port 4723 --log-timestamp --local-timezone --log data/logs/appium.log "$@"
