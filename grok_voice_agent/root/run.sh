#!/bin/sh
# Same entry as rootfs/etc/services.d/grok/run. Kept so a manual start still
# receives SUPERVISOR_TOKEN from s6.
cd /app
if [ -x /usr/bin/with-contenv ]; then
  exec /usr/bin/with-contenv python3 -m app.main
fi
if [ -x /command/with-contenv ]; then
  exec /command/with-contenv python3 -m app.main
fi
exec python3 -m app.main
