#!/bin/sh
awk '/^  doctor:$/{f=1; next} f && /^  [a-z-]+:$/{f=0} f' .github/workflows/ci.yml | grep -q "if: github.ref == 'refs/heads/main'"
