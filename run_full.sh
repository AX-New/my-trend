#!/bin/bash
# my-trend 全量基本面分析（火山云，永久循环）

export PYTHONPATH=/root/my-claude/my-trend
cd /root/my-claude/my-trend

LOG_DIR=/root/my-claude/my-trend/logs
mkdir -p "$LOG_DIR"

while true; do
    LOG="$LOG_DIR/run_$(date +%Y%m%d_%H%M%S).log"

    echo "========== $(date) 开始全量基本面分析 ==========" | tee -a "$LOG"

    systemctl is-active mysql-tunnel > /dev/null || systemctl restart mysql-tunnel
    sleep 2

    git pull --ff-only >> "$LOG" 2>&1

    python3.11 -m analysis.main --all-stocks >> "$LOG" 2>&1

    echo "========== $(date) 本轮完成，立即开始下一轮 ==========" | tee -a "$LOG"

    find "$LOG_DIR" -name "run_*.log" -mtime +30 -delete
done
