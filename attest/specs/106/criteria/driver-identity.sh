#!/bin/sh
grep -q 'def driver_identity' bin/attavo && grep -q 'driver@attavo.invalid' bin/attavo
