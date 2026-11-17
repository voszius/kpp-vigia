#!/usr/bin/env bash
# Disparador gratis y sin IA: el cron de GitHub se atrasa horas, así que la caja dispara
# kpp-vigia (corrida normal: preguntas + pedidos) cada 10 min con workflow_dispatch.
# Uso: bash deploy/vigia_caja.sh start | stop | status     (necesita GH_TOKEN en el entorno)
# Log: /workspace/ml_state/vigia_caja.log (solo hora y código HTTP; nada privado).
PID=/workspace/ml_state/vigia_caja.pid; LOG=/workspace/ml_state/vigia_caja.log; CADA=${VIGIA_CADA:-600}
mkdir -p /workspace/ml_state
vivo() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
case "$1" in
  start)
    vivo && { echo "ya corre (pid $(cat $PID))"; exit 0; }
    [ -n "$GH_TOKEN" ] || { echo "falta GH_TOKEN"; exit 1; }
    setsid nohup bash -c '
      while true; do
        # no dispara si ya hay una corrida en cola o corriendo
        act=$(curl -s -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
          "https://api.github.com/repos/voszius/kpp-vigia/actions/runs?per_page=5" | python3 -c "import sys,json;print(sum(r[\"status\"]!=\"completed\" for r in json.load(sys.stdin).get(\"workflow_runs\",[])))" 2>/dev/null)
        if [ "$act" = "0" ]; then
          c=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $GH_TOKEN" \
            -H "Accept: application/vnd.github+json" \
            https://api.github.com/repos/voszius/kpp-vigia/actions/workflows/ml.yml/dispatches -d "{\"ref\":\"main\"}")
          echo "$(date "+%F %T") disparo HTTP $c" >> '"$LOG"'
        else echo "$(date "+%F %T") ya hay corrida activa, no disparo" >> '"$LOG"'; fi
        tail -n 500 '"$LOG"' > '"$LOG"'.t && mv '"$LOG"'.t '"$LOG"'
        sleep '"$CADA"'
      done' >/dev/null 2>&1 &
    echo $! > "$PID"; echo "arrancado (pid $!, cada $CADA s)";;
  stop) vivo && kill "$(cat $PID)" && rm -f "$PID" && echo detenido || echo "no corría";;
  status) vivo && echo "corriendo (pid $(cat $PID))" || echo "detenido"; tail -n 3 "$LOG" 2>/dev/null;;
  *) echo "uso: $0 start|stop|status";;
esac
