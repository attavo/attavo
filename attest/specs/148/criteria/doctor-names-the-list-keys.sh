#!/bin/sh
grep -q '"ordinary_commits"' bin/attavo && grep -q '"unrecorded_commits"' bin/attavo
