#!/bin/sh
set -e
here=$(pwd)
build() {
  dir=$1
  attempts=$2
  cd "$dir"
  git init -q -b main .
  git config user.email person@attavo.invalid
  git config user.name "ledger fixture"
  printf '[paths]\nrecords = "records"\nspecs = "specs"\n' > attavo.toml
  git add attavo.toml
  git commit -q -m install
  git checkout -q -b attavo/1-x
  mkdir -p specs/1 records/1
  printf '# item 1\n' > specs/1/spec.md
  git add specs
  git commit -q -m "spec for item 1"
  for stage in spec approval
  do
    printf '{"status": "pass"}' > "records/1/$stage-01.json"
    git add records
    git commit -q -m "$stage record for item 1"
  done
  n=1
  while test "$n" -le "$attempts"
  do
    nn=$(printf '%02d' "$n")
    for stage in build verify
    do
      printf '{"status": "pass"}' > "records/1/$stage-$nn.json"
      git add records
      git commit -q -m "$stage record $nn for item 1"
    done
    n=$((n+1))
  done
  git checkout -q main
  git merge -q --no-ff -m "Merge branch 'attavo/1-x'" attavo/1-x
  GIT_TRACE="$dir/trace" python3 "$here/bin/attavo" ledger --json > "$dir/ledger.json"
  cd "$here"
  grep -c 'built-in: git' "$dir/trace"
}
few=$(mktemp -d)
many=$(mktemp -d)
a=$(build "$few" 1)
b=$(build "$many" 5)
echo "git processes: 4 records $a, 12 records $b"
rm -rf "$few" "$many"
test $((b - a)) -le 2
