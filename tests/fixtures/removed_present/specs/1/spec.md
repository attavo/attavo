## Problem

The build script leaves a scratch file, src/doomed.txt, that must be removed.

## Claims

- src/**

## Criteria

- absent.sh: fails while src/doomed.txt exists, passes once it is gone
