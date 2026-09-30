#!/bin/sh
set -e
python3 -m pytest -q -p no:cacheprovider tests/test_pass.py \
  -k "test_a_second_checkout_on_this_host_reads_the_first_ones_worker_as_running or test_the_holders_heartbeat_is_read_where_its_marker_says or test_the_marker_a_pass_writes_names_its_checkouts_heartbeat_directory or test_two_passes_at_once_start_one_worker_and_the_loser_says_so" > "${TMPDIR:-/tmp}/xlock-criterion.out" 2>&1 || { cat "${TMPDIR:-/tmp}/xlock-criterion.out"; exit 1; }
grep -q "4 passed" "${TMPDIR:-/tmp}/xlock-criterion.out"
grep -q "def holder_heartbeat_path(" attavo/doctor.py
grep -q '"events": str(events_dir or stage_events_dir(root))' attavo/doctor.py
