#!/bin/bash
# 爬虫守护脚本：每15分钟检查 analysis_run 最后一条任务状态
# 如果非 completed 且爬虫进程不在，则重启爬虫
# 用法：crontab 中配置 */15 * * * * /root/my-claude/my-trend/watchdog.sh

WORKDIR=/root/my-claude/my-trend
PYTHON=/usr/bin/python3
LOG="$WORKDIR/logs/watchdog.log"
LOCK="/tmp/my-trend-analysis.lock"

mkdir -p "$WORKDIR/logs"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"
}

# 查最后一条 analysis_run 的状态
status=$($PYTHON -c "
import sys
sys.path.insert(0, '$WORKDIR')
from config import load_config
from database import Database
from analysis.models import AnalysisRun
cfg = load_config('$WORKDIR/config.yaml')
db = Database(cfg.database)
session = db.get_session()
run = session.query(AnalysisRun).order_by(AnalysisRun.started_at.desc()).first()
if run:
    print(f'{run.status}|{run.run_id}|{run.done_cursor}/{run.total_count}')
else:
    print('none')
session.close()
" 2>/dev/null)

if [ "$status" = "none" ]; then
    log "[INFO] 无任务记录，跳过"
    exit 0
fi

run_status=$(echo "$status" | cut -d'|' -f1)
run_id=$(echo "$status" | cut -d'|' -f2)
progress=$(echo "$status" | cut -d'|' -f3)

if [ "$run_status" = "completed" ]; then
    log "[OK] 最近任务 $run_id 已完成（$progress）"
    exit 0
fi

# 任务未完成，检查爬虫进程是否在线
if pgrep -f "python.*-m analysis.main" > /dev/null; then
    log "[OK] 任务 $run_id 进行中（$progress），爬虫在线"
    exit 0
fi

# 爬虫不在线，用 flock 防止重复拉起
log "[WARN] 任务 $run_id 未完成（$run_status, $progress），爬虫离线，正在重启..."

(
    flock -xn 200 || { log "[SKIP] 已有实例在启动中"; exit 0; }
    cd "$WORKDIR"
    nohup $PYTHON -m analysis.main --all-stocks &
    log "[START] 爬虫已拉起 PID=$!"
) 200>"$LOCK"
