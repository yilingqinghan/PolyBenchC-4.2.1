#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="bin"
REPEAT=5         # 统计重复轮次
WARMUP=2         # 预热次数
MIN_TOTAL=2.0    # 每轮至少累计这么久（秒），保障精度
QUIET=0
OUT="results-precise-$(date +%Y%m%d-%H%M%S).csv"

usage() {
  cat <<EOF
用法: $0 [-r 重复轮次] [-w 预热] [-t 最小累计秒数] [-q] [-o 输出CSV]
示例:
  $0 -q -r 7 -t 3 -o precise.csv
  OMP_NUM_THREADS=8 $0
EOF
}

# 解析参数
while getopts ":r:w:t:qo:h" opt; do
  case "$opt" in
    r) REPEAT="$OPTARG" ;;
    w) WARMUP="$OPTARG" ;;
    t) MIN_TOTAL="$OPTARG" ;;
    q) QUIET=1 ;;
    o) OUT="$OPTARG" ;;
    h) usage; exit 0 ;;
    \?) echo "未知参数 -$OPTARG"; usage; exit 2 ;;
    :)  echo "选项 -$OPTARG 需要参数"; usage; exit 2 ;;
  esac
done

# 没有可执行文件就先编译
if [[ ! -d "$BIN_DIR" ]] || ! compgen -G "$BIN_DIR/*" >/dev/null; then
  echo "[info] 未发现 bin/*，先 make -j"
  make -j
fi

# 高精度计时函数（纳秒）
now_ns() { date +%s%N; }

# 统计工具
mean() { awk '{sum+=$1} END{printf("%.9f", sum/NR)}'; }
stdev() { awk '{x[NR]=$1; s+=$1} END{m=s/NR; for(i=1;i<=NR;i++){d=x[i]-m; v+=d*d} printf("%.9f", sqrt(v/(NR-1)))}'; }
minv() { sort -g | head -n1; }

echo "benchmark,rep,iter_per_rep,elapsed_total_sec,per_iter_sec,per_iter_ns,threads" > "$OUT"

shopt -s nullglob
for exe in "$BIN_DIR"/*; do
  [[ -x "$exe" ]] || continue
  b=$(basename "$exe")

  # 预热（不计入统计）
  for ((w=1; w<=WARMUP; w++)); do
    [[ $QUIET -eq 1 ]] || echo "warmup $b ($w/$WARMUP)"
    "$exe" >/dev/null 2>&1 || true
  done

  # 自适应确定每轮迭代数（保证累计时间 >= MIN_TOTAL）
  iter=1
  while :; do
    start=$(now_ns)
    for ((i=1; i<=iter; i++)); do "$exe" >/dev/null 2>&1 || true; done
    end=$(now_ns)
    elapsed=$(awk -v s="$start" -v e="$end" 'BEGIN{printf("%.9f",(e-s)/1e9)}')
    # 达标就用这个迭代数
    awk -v t="$elapsed" -v m="$MIN_TOTAL" 'BEGIN{exit !(t<m)}' || break
    # 不达标，加倍迭代数（上限避免极端情况）
    iter=$(( iter*2 ))
    if (( iter > 100000 )); then
      echo "[warn] $b 自适应迭代超过 100000，放弃加倍"; break
    fi
  done
  [[ $QUIET -eq 1 ]] || echo "==> $b: iter_per_rep=$iter (目标累计 ${MIN_TOTAL}s)"

  # 正式统计 REPEAT 轮
  per_rep_totals=()
  for ((r=1; r<=REPEAT; r++)); do
    start=$(now_ns)
    for ((i=1; i<=iter; i++)); do "$exe" >/dev/null 2>&1 || true; done
    end=$(now_ns)
    total=$(awk -v s="$start" -v e="$end" 'BEGIN{printf("%.9f",(e-s)/1e9)}')
    per_iter_sec=$(awk -v t="$total" -v it="$iter" 'BEGIN{printf("%.12f", t/it)}')
    per_iter_ns=$(awk -v s="$per_iter_sec" 'BEGIN{printf("%.0f", s*1e9)}')
    threads="${OMP_NUM_THREADS:-1}"
    echo "$b,$r,$iter,$total,$per_iter_sec,$per_iter_ns,$threads" >> "$OUT"
    per_rep_totals+=("$total")
    [[ $QUIET -eq 1 ]] || echo "   rep $r: total=${total}s  per_iter=${per_iter_sec}s"
  done

done

echo
echo "[done] 结果已保存: $OUT"

