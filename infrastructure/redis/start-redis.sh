#!/bin/sh
# Starts copilot-redis with the ACL users of users.acl (SEC-004). The passwords
# come from the same URLs the clients use, so they are defined once, in .env:
#   REDIS_URL         redis://backend:<password>@copilot-redis:6379/0
#   RUNNER_REDIS_URL  redis://runner:<password>@copilot-redis:6379/0
# The filled-in ACL file lives only on a tmpfs. Passwords must be hex or
# alphanumeric (no '@', ':', '/', '&' or spaces). Nothing secret is printed.
set -eu

password_of() { # $1 = URL, $2 = expected user
  rest="${1#redis://"$2":}"
  if [ "$rest" = "$1" ]; then
    echo "start-redis: URL for user $2 is not redis://$2:<password>@host:port/db" >&2
    exit 1
  fi
  pw="${rest%@*}"  # up to the last @, as URL parsers do
  case "$pw" in
    "" | *[!A-Za-z0-9_-]*) echo "start-redis: password for $2 must be [A-Za-z0-9_-]+" >&2; exit 1 ;;
  esac
  printf '%s' "$pw"
}

backend_pw="$(password_of "$REDIS_URL" backend)"
runner_pw="$(password_of "$RUNNER_REDIS_URL" runner)"
if [ "$backend_pw" = "$runner_pw" ]; then
  echo "start-redis: the backend and runner passwords must differ" >&2
  exit 1
fi

umask 077
# redis ACL files allow no comments, so the template's comment lines are dropped.
sed -e "/^#/d" -e "/^[[:space:]]*$/d" \
  -e "s/__BACKEND_PASSWORD__/$backend_pw/" -e "s/__RUNNER_PASSWORD__/$runner_pw/" \
  /etc/ocp/users.acl.template > /run/ocp/users.acl
printf '%s' "$backend_pw" > /run/ocp/healthcheck.pw

exec redis-server --aclfile /run/ocp/users.acl --save "" --appendonly no \
  --protected-mode yes --port 6379
