## Problem

Class: fix

The build script does not emit a success marker file.

## Claims

- src/**

## Criteria

- checks_marker.sh: fails until src/marker.txt exists, passes once it does
