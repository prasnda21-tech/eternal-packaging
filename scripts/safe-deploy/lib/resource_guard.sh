# shellcheck shell=bash
# Resource guard — FAIL CLOSED. Does not create swap.

resource_guard_check() {
  local avail_mb load1 cpus load_per disk_mb
  local min_mem="${MIN_AVAIL_MEM_MB:-1536}"
  local max_load="${MAX_LOAD_PER_CPU:-2.0}"
  local min_disk="${MIN_DISK_AVAIL_MB:-4096}"
  local path="${APP_ROOT:-/opt/eternal-packaging}"
  if [[ ! -e "$path" ]]; then
    path="${SAFE_DEPLOY_TEST_ROOT:-/tmp}"
  fi

  # Available RAM (MemAvailable)
  avail_mb="$(awk '/MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
  load1="$(awk '{print $1}' /proc/loadavg)"
  cpus="$(nproc_safe)"
  load_per="$(python3 -c "print(round(float('$load1')/max(int('$cpus'),1), 3))")"
  disk_mb="$(df -Pm "$path" 2>/dev/null | awk 'NR==2{print $4}')"
  disk_mb="${disk_mb:-0}"

  info "resource: avail_mem_mb=${avail_mb} (min=${min_mem}) load1=${load1} cpus=${cpus} load/cpu=${load_per} (max=${max_load}) disk_avail_mb=${disk_mb} (min=${min_disk})"

  if [[ "${FORCE_LOW_MEMORY:-0}" == "1" ]]; then
    avail_mb=0
    info "FORCE_LOW_MEMORY=1 → simulating low memory"
  fi

  if [[ "$avail_mb" -lt "$min_mem" ]]; then
    die "resource guard: available RAM ${avail_mb}MB < ${min_mem}MB — abort (swap not configured; do not proceed)"
  fi

  python3 -c "import sys; sys.exit(0 if float('$load_per') <= float('$max_load') else 1)" \
    || die "resource guard: load/cpu ${load_per} > ${max_load} — abort"

  if [[ "$disk_mb" -lt "$min_disk" ]]; then
    die "resource guard: disk available ${disk_mb}MB < ${min_disk}MB on $path — abort"
  fi

  info "resource guard: PASS"
}

report_swap_recommendation() {
  cat <<'EOF'
[RECOMMENDED SWAP — REPORT ONLY, NOT APPLIED]
  Production has Swap: 0B on ~7.8 GiB RAM.
  Recommendation for explicit future approval (do not apply in deploy tooling):
    - size: 2–4 GiB swapfile on /
    - example (approval required):
        fallocate -l 2G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    - vm.swappiness=10 suggested for DB-heavy hosts
  This is a resilience cushion only; it does not authorize heavy in-RAM hashing.
EOF
}
