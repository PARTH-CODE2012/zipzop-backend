#!/bin/sh
# Build the frontend from a clean copy of the source and serve it.
set -eu

rm -rf /app/frontend /app/backend
mkdir -p /app/frontend /app/backend/app/assets
# The LUTs live in the backend and are synced into `public/` at build time.
cp -r /src/backend/app/assets/luts /app/backend/app/assets/
# The types are generated from the committed contract, as CI does.
cp /src/openapi.json /app/openapi.json
cd /src/frontend
tar --exclude=./node_modules --exclude=./.next -cf - . | tar -C /app/frontend -xf -

cd /app/frontend
corepack enable
pnpm install --frozen-lockfile
pnpm generate:types
pnpm build
exec pnpm start -p 3000
