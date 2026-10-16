#!/usr/bin/env bash
# Prove CI timeout exit-code capture cannot false-green a failing command.
# No business data / Mongo / app imports — pure shell contract for workflow helpers.
set -euo pipefail

fail=0

# Broken pattern (historical workflow): status=$? inside `if ! …` is 0 after a
# failed command because the `if` compound itself succeeded.
broken_status="$(
  bash -c '
    set +e
    if ! bash -c "exit 7"; then
      status=$?
      printf "%s" "$status"
      exit 0
    fi
    printf "unreachable"
  '
)"
if [ "${broken_status}" = "0" ]; then
  echo "PASS: documented false-green pattern captures status=0 after exit 7"
else
  echo "FAIL: expected broken pattern to capture 0, got ${broken_status}"
  fail=1
fi

# Correct pattern: capture status immediately after timeout/command, outside if !.
correct_fail="$(
  bash -c '
    set +e
    timeout --signal=TERM --kill-after=2s 5s bash -c "exit 7"
    status=$?
    set -e
    printf "%s" "$status"
  '
)"
if [ "${correct_fail}" = "7" ]; then
  echo "PASS: correct pattern preserves pytest-like exit 7"
else
  echo "FAIL: expected status 7, got ${correct_fail}"
  fail=1
fi

correct_ok="$(
  bash -c '
    set +e
    timeout --signal=TERM --kill-after=2s 5s bash -c "exit 0"
    status=$?
    set -e
    printf "%s" "$status"
  '
)"
if [ "${correct_ok}" = "0" ]; then
  echo "PASS: correct pattern preserves success exit 0"
else
  echo "FAIL: expected status 0, got ${correct_ok}"
  fail=1
fi

# timeout itself must surface as non-zero when the deadline fires.
timeout_fail="$(
  bash -c '
    set +e
    timeout --signal=TERM --kill-after=1s 1s bash -c "sleep 30"
    status=$?
    set -e
    printf "%s" "$status"
  '
)"
if [ "${timeout_fail}" -ne 0 ]; then
  echo "PASS: deadline timeout yields non-zero status (${timeout_fail})"
else
  echo "FAIL: expected non-zero on deadline, got 0"
  fail=1
fi

# Wrapper mimicking fixed workflow step: must exit non-zero on child failure.
wrapper_rc=0
bash -c '
  set -euo pipefail
  set +e
  timeout --signal=TERM --kill-after=2s 5s bash -c "exit 3"
  status=$?
  set -e
  if [ "${status}" -ne 0 ]; then
    echo "::error::synthetic suite exited with status ${status}"
    exit "${status}"
  fi
' || wrapper_rc=$?
if [ "${wrapper_rc}" = "3" ]; then
  echo "PASS: workflow-style wrapper exits with child status 3"
else
  echo "FAIL: wrapper rc expected 3, got ${wrapper_rc}"
  fail=1
fi

if [ "${fail}" -ne 0 ]; then
  echo "CI_TIMEOUT_EXIT_CAPTURE_FAILED"
  exit 1
fi
echo "CI_TIMEOUT_EXIT_CAPTURE_OK"
exit 0
