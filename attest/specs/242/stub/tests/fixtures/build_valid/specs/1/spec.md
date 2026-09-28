## Problem

Class: fix

The build script does not emit a marker file that proves it ran.

## Claims

- built.txt

## Criteria

- checks_built.sh: fails until built.txt exists, passes once it does
