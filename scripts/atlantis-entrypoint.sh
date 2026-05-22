#!/bin/sh
set -e

: "${REDIS_URL:=redis://redis:6379/0}"
: "${REDIS_BACKENDS_KEY:=atlantis:backends}"
: "${ATLANTIS_SELF_URL:?ATLANTIS_SELF_URL must be set}"
: "${ATLANTIS_GITEA_USER:=atlantis}"
: "${ATLANTIS_GITEA_BASE_URL:=http://gitea:3000}"

deregister() {
    redis-cli -u "$REDIS_URL" SREM "$REDIS_BACKENDS_KEY" "$ATLANTIS_SELF_URL" || true
    echo "Deregistered $ATLANTIS_SELF_URL from $REDIS_BACKENDS_KEY"
}

trap deregister TERM INT

redis-cli -u "$REDIS_URL" SADD "$REDIS_BACKENDS_KEY" "$ATLANTIS_SELF_URL"
echo "Registered $ATLANTIS_SELF_URL in $REDIS_BACKENDS_KEY"

if [ -n "${ATLANTIS_GITEA_TOKEN_FILE:-}" ]; then
    retries=0
    until [ -s "$ATLANTIS_GITEA_TOKEN_FILE" ]; do
        retries=$((retries + 1))
        if [ "$retries" -gt 120 ]; then
            echo "Timed out waiting for token file: $ATLANTIS_GITEA_TOKEN_FILE"
            exit 1
        fi
        sleep 1
    done
    ATLANTIS_GITEA_TOKEN="$(cat "$ATLANTIS_GITEA_TOKEN_FILE")"
fi

if [ -n "${ATLANTIS_GITEA_TOKEN:-}" ]; then
    # Gitea API may return clone URLs with localhost, which is not reachable from Atlantis containers.
    # Rewrite those clone URLs to the internal compose host so git clone works for autoplan and comments.
    git config --global --add url."${ATLANTIS_GITEA_BASE_URL%/}/".insteadOf "http://localhost:3000/"
    git config --global --add url."${ATLANTIS_GITEA_BASE_URL%/}/".insteadOf "https://localhost:3000/"
    git config --global --add url."${ATLANTIS_GITEA_BASE_URL%/}/".insteadOf "http://${ATLANTIS_GITEA_USER}:${ATLANTIS_GITEA_TOKEN}@localhost:3000/"
    git config --global --add url."${ATLANTIS_GITEA_BASE_URL%/}/".insteadOf "https://${ATLANTIS_GITEA_USER}:${ATLANTIS_GITEA_TOKEN}@localhost:3000/"

    set -- "$@" \
        "--gitea-user=$ATLANTIS_GITEA_USER" \
        "--gitea-token=$ATLANTIS_GITEA_TOKEN" \
        "--gitea-base-url=$ATLANTIS_GITEA_BASE_URL"
fi

atlantis "$@" &
ATLANTIS_PID=$!
wait $ATLANTIS_PID
