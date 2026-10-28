#!/usr/bin/env bash
# Deploy the Discord /tennis Worker. Needs a Cloudflare API token with "Workers Scripts: Edit"
# (export CLOUDFLARE_API_TOKEN=... ; CLOUDFLARE_ACCOUNT_ID=e00392fc51f4378b29ccadec823e0c49) or `npx wrangler login`.
set -euo pipefail
cd "$(dirname "$0")"
node build.mjs
npx wrangler@4 deploy
echo "Interactions Endpoint URL: https://tennis-oracle.dreiss324.workers.dev/interactions"
